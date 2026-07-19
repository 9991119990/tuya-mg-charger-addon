#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import re
import socket
import time
import urllib.parse
import urllib.request

try:
    import tinytuya
except ImportError:
    tinytuya = None


ENDPOINTS = {
    "eu": "https://openapi.tuyaeu.com",
    "us": "https://openapi.tuyaus.com",
    "cn": "https://openapi.tuyacn.com",
    "in": "https://openapi.tuyain.com",
}

SENSORS = [
    ("power_w", "Aktuální výkon", "W", "power", "measurement", 0),
    ("charge_current_set_a", "Nastavený proud", "A", "current", "measurement", 0),
    ("temperature_c", "Teplota", "°C", "temperature", "measurement", 0),
    ("charge_energy_once_kwh", "Energie nabíjení", "kWh", "energy", "total_increasing", 2),
    ("work_state", "Stav", None, None, None, None),
    ("work_mode", "Režim", None, None, None, None),
]

BINARY_SENSORS = [
    ("enabled", "Spínač"),
]

BREAKER_SENSORS = [
    ("power_w", "Aktuální výkon", "W", "power", "measurement", 1),
    ("current_a", "Proud", "A", "current", "measurement", 3),
    ("voltage_v", "Napětí", "V", "voltage", "measurement", 1),
    ("reported_energy_kwh", "Hlášená energie", "kWh", "energy", "measurement", 3),
    ("temperature_c", "Teplota", "°C", "temperature", "measurement", 0),
    ("fault", "Porucha", None, None, None, None),
    ("relay_status", "Stav relé", None, None, None, None),
]

BREAKER_BINARY_SENSORS = [
    ("enabled", "Spínač"),
    ("child_lock", "Dětský zámek"),
]


def enc_str(value: str) -> bytes:
    data = value.encode()
    return len(data).to_bytes(2, "big") + data


def enc_len(length: int) -> bytes:
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length:
            digit |= 0x80
        out.append(digit)
        if not length:
            return bytes(out)


class MqttClient:
    def __init__(self, host, port, username=None, password=None, client_id="tuya-mg-charger"):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.username = username if username else None
        self.password = password if password else None
        self.client_id = client_id
        self._connect()

    def _send(self, packet_type: int, flags: int, payload: bytes) -> None:
        self.sock.sendall(bytes([(packet_type << 4) | flags]) + enc_len(len(payload)) + payload)

    def _connect(self) -> None:
        flags = 0x02
        payload = enc_str(self.client_id)
        if self.username is not None:
            flags |= 0x80
            payload += enc_str(self.username)
        if self.password is not None:
            flags |= 0x40
            payload += enc_str(self.password)
        variable = enc_str("MQTT") + bytes([4, flags, 0, 60])
        self._send(1, 0, variable + payload)
        response = self.sock.recv(4)
        if len(response) < 4 or response[0] != 0x20 or response[3] != 0:
            raise RuntimeError(f"MQTT connect failed: {response.hex(' ')}")

    def publish(self, topic: str, payload, retain=False) -> None:
        if not isinstance(payload, str):
            payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        flags = 0x01 if retain else 0x00
        self._send(3, flags, enc_str(topic) + payload.encode())

    def close(self) -> None:
        try:
            self._send(14, 0, b"")
        finally:
            self.sock.close()


class TuyaClient:
    def __init__(self, endpoint: str, client_id: str, client_secret: str):
        self.endpoint = endpoint.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expires_at = 0

    def _sign(self, method: str, path: str, body: bytes = b"", access_token: str = "") -> tuple[str, str]:
        timestamp = str(int(time.time() * 1000))
        body_hash = hashlib.sha256(body).hexdigest()
        string_to_sign = "\n".join([method.upper(), body_hash, "", path])
        sign_source = self.client_id + access_token + timestamp + string_to_sign
        sign = hmac.new(
            self.client_secret.encode(),
            sign_source.encode(),
            hashlib.sha256,
        ).hexdigest().upper()
        return timestamp, sign

    def request(self, method: str, path: str, body: dict | None = None, use_token: bool = True) -> dict:
        if use_token:
            self.ensure_token()
        payload = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
        token = self.access_token if use_token else ""
        timestamp, sign = self._sign(method, path, payload, token)
        headers = {
            "client_id": self.client_id,
            "sign": sign,
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "Content-Type": "application/json",
        }
        if use_token:
            headers["access_token"] = self.access_token
        req = urllib.request.Request(
            self.endpoint + path,
            data=payload if method.upper() != "GET" else None,
            headers=headers,
            method=method.upper(),
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
        if not data.get("success"):
            raise RuntimeError(f"Tuya API error: {data}")
        return data

    def ensure_token(self) -> None:
        if self.access_token and time.time() < self.token_expires_at - 60:
            return
        path = "/v1.0/token?grant_type=1"
        data = self.request("GET", path, use_token=False)
        result = data["result"]
        self.access_token = result["access_token"]
        self.token_expires_at = time.time() + int(result.get("expire_time", 7200))

    def get_device(self, device_id: str) -> dict:
        return self.request("GET", f"/v2.0/cloud/thing/{urllib.parse.quote(device_id)}")["result"]

    def get_status(self, device_id: str) -> list[dict]:
        quoted = urllib.parse.quote(device_id)
        paths = [
            f"/v1.0/iot-03/devices/{quoted}/status",
            f"/v1.0/devices/{quoted}/status",
        ]
        last_error = None
        for path in paths:
            try:
                result = self.request("GET", path)["result"]
                return result if isinstance(result, list) else result.get("status", [])
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"Unable to read Tuya device status: {last_error}")


def decode_status(device: dict, status_items: list[dict]) -> dict:
    status = {item["code"]: item["value"] for item in status_items}
    power_total_raw = status.get("power_total", 0)
    single_phase_raw = status.get("sigle_phase_power", 0)
    return {
        "work_state": status.get("work_state"),
        "charge_current_set_a": status.get("charge_cur_set"),
        "single_phase_power_w": round(single_phase_raw, 1),
        "power_w": round(power_total_raw, 1),
        "power_kw": round(power_total_raw / 1000, 3),
        "work_mode": status.get("work_mode"),
        "enabled": bool(status.get("switch")),
        "temperature_c": status.get("temp_current"),
        "charge_energy_once_kwh": round(status.get("charge_energy_once", 0) / 100, 2),
        "online": bool(device.get("online") or device.get("is_online")),
        "raw": status,
    }


def decode_breaker_status(device: dict, status_items: list[dict]) -> dict:
    status = {item["code"]: item["value"] for item in status_items}
    enabled = status.get("switch", status.get("switch_1"))
    return {
        "enabled": bool(enabled),
        "power_w": round(status.get("cur_power", 0) / 10, 1),
        "current_a": round(status.get("cur_current", 0) / 1000, 3),
        "voltage_v": round(status.get("cur_voltage", 0) / 10, 1),
        "reported_energy_kwh": round(status.get("add_ele", 0) / 1000, 3),
        "temperature_c": status.get("temp_value"),
        "fault": status.get("fault"),
        "relay_status": status.get("relay_status"),
        "child_lock": bool(status.get("child_lock", False)),
        "online": bool(device.get("online") or device.get("is_online")),
        "raw": status,
    }


def slugify(value: str) -> str:
    value = value.lower()
    value = value.replace("ř", "r").replace("č", "c").replace("š", "s").replace("ž", "z")
    value = value.replace("ý", "y").replace("á", "a").replace("í", "i").replace("é", "e")
    value = value.replace("ě", "e").replace("ů", "u").replace("ú", "u").replace("ó", "o")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "device"


def parse_breakers(value: str) -> list[dict]:
    value = (value or "").strip()
    if not value or value == "[]":
        return []
    data = json.loads(value)
    if not isinstance(data, list):
        raise ValueError("breakers_json must be a JSON list")
    breakers = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("Each breaker entry must contain at least an id")
        breakers.append({"id": str(item["id"]), "name": str(item.get("name") or item["id"])})
    return breakers


def parse_local_devices(value: str) -> list[dict]:
    value = (value or "").strip()
    if not value or value == "[]":
        return []
    data = json.loads(value)
    if not isinstance(data, list):
        raise ValueError("local_devices_json must be a JSON list")
    devices = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Each local device entry must be an object")
        for key in ("id", "name", "host", "local_key"):
            if not item.get(key):
                raise ValueError(f"Each local device entry must contain {key}")
        devices.append({
            "id": str(item["id"]),
            "name": str(item["name"]),
            "host": str(item["host"]),
            "local_key": str(item["local_key"]),
            "version": float(item.get("version", 3.5)),
            "model": str(item.get("model") or "Local Tuya breaker"),
        })
    return devices


def decode_local_breaker_dps(dps: dict) -> dict:
    def get(key, default=None):
        return dps.get(str(key), dps.get(key, default))

    enabled = get(1)
    return {
        "enabled": bool(enabled),
        "power_w": round((get(19, 0) or 0) / 10, 1),
        "current_a": round((get(18, 0) or 0) / 1000, 3),
        "voltage_v": round((get(20, 0) or 0) / 10, 1),
        "reported_energy_kwh": round((get(17, 0) or 0) / 1000, 3),
        "temperature_c": get(47),
        "fault": get(26),
        "relay_status": get(38),
        "child_lock": bool(get(41, False)),
        "online": True,
        "raw": dps,
    }


def read_local_tuya_device(device: dict) -> dict:
    if tinytuya is None:
        raise RuntimeError("tinytuya is not installed")
    tuya_device = tinytuya.OutletDevice(
        dev_id=device["id"],
        address=device["host"],
        local_key=device["local_key"],
        version=device["version"],
    )
    tuya_device.set_socketPersistent(False)
    tuya_device.set_socketTimeout(10)
    status = tuya_device.status()
    if not isinstance(status, dict) or "dps" not in status:
        raise RuntimeError(f"Local Tuya status error: {status}")
    return decode_local_breaker_dps(status["dps"])


def discovery_config(name, key, state_topic, device, unit=None, device_class=None, state_class=None, precision=None):
    cfg = {
        "name": name,
        "unique_id": f"tuya_mg_charger_{key}",
        "state_topic": state_topic,
        "availability_topic": "tuya_mg_charger/availability",
        "value_template": "{{ value_json." + key + " }}",
        "device": device,
    }
    if unit:
        cfg["unit_of_measurement"] = unit
    if device_class:
        cfg["device_class"] = device_class
    if state_class:
        cfg["state_class"] = state_class
    if precision is not None:
        cfg["suggested_display_precision"] = precision
    return cfg


def binary_discovery_config(name, key, state_topic, device):
    return {
        "name": name,
        "unique_id": f"tuya_mg_charger_{key}",
        "state_topic": state_topic,
        "availability_topic": "tuya_mg_charger/availability",
        "value_template": "{{ value_json." + key + " | lower }}",
        "payload_on": "true",
        "payload_off": "false",
        "device": device,
    }


def breaker_discovery_config(name, key, state_topic, availability_topic, unique_prefix, device, unit=None, device_class=None, state_class=None, precision=None):
    cfg = {
        "name": name,
        "unique_id": f"{unique_prefix}_{key}",
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "value_template": "{{ value_json." + key + " }}",
        "device": device,
    }
    if unit:
        cfg["unit_of_measurement"] = unit
    if device_class:
        cfg["device_class"] = device_class
    if state_class:
        cfg["state_class"] = state_class
    if precision is not None:
        cfg["suggested_display_precision"] = precision
    return cfg


def breaker_binary_discovery_config(name, key, state_topic, availability_topic, unique_prefix, device):
    return {
        "name": name,
        "unique_id": f"{unique_prefix}_{key}",
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "value_template": "{{ value_json." + key + " | lower }}",
        "payload_on": "true",
        "payload_off": "false",
        "device": device,
    }


def publish_discovery(client: MqttClient, state_topic: str) -> None:
    device = {
        "identifiers": ["tuya_mg_charger"],
        "name": "Nabíječka od MG",
        "manufacturer": "Tuya",
        "model": "A1-01",
    }
    for key, name, unit, device_class, state_class, precision in SENSORS:
        topic = f"homeassistant/sensor/tuya_mg_charger/{key}/config"
        client.publish(topic, discovery_config(name, key, state_topic, device, unit, device_class, state_class, precision), retain=True)
    for key, name in BINARY_SENSORS:
        topic = f"homeassistant/binary_sensor/tuya_mg_charger/{key}/config"
        client.publish(topic, binary_discovery_config(name, key, state_topic, device), retain=True)


def publish_breaker_discovery(client: MqttClient, state_topic: str, availability_topic: str, slug: str, name: str, model: str | None) -> None:
    unique_prefix = f"tuya_breaker_{slug}"
    device = {
        "identifiers": [unique_prefix],
        "name": name,
        "manufacturer": "Tuya",
        "model": model or "Smart breaker",
    }
    for key, sensor_name, unit, device_class, state_class, precision in BREAKER_SENSORS:
        topic = f"homeassistant/sensor/{unique_prefix}/{key}/config"
        client.publish(
            topic,
            breaker_discovery_config(sensor_name, key, state_topic, availability_topic, unique_prefix, device, unit, device_class, state_class, precision),
            retain=True,
        )
    for key, sensor_name in BREAKER_BINARY_SENSORS:
        topic = f"homeassistant/binary_sensor/{unique_prefix}/{key}/config"
        client.publish(
            topic,
            breaker_binary_discovery_config(sensor_name, key, state_topic, availability_topic, unique_prefix, device),
            retain=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=sorted(ENDPOINTS), default="eu")
    parser.add_argument("--client-id", default="")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--device-id", default="")
    parser.add_argument("--mqtt-host", required=True)
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-user", default="")
    parser.add_argument("--mqtt-password", default="")
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--breakers-json", default="")
    parser.add_argument("--local-devices-json", default="")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    use_cloud = bool(args.client_id and args.client_secret and args.device_id)
    tuya = TuyaClient(ENDPOINTS[args.region], args.client_id, args.client_secret) if use_cloud else None
    breakers = parse_breakers(args.breakers_json)
    local_devices = parse_local_devices(args.local_devices_json)
    state_topic = "tuya_mg_charger/state"

    while True:
        mqtt = None
        try:
            mqtt = MqttClient(args.mqtt_host, args.mqtt_port, args.mqtt_user, args.mqtt_password)

            if use_cloud:
                try:
                    device = tuya.get_device(args.device_id)
                    data = decode_status(device, tuya.get_status(args.device_id))
                    publish_discovery(mqtt, state_topic)
                    mqtt.publish("tuya_mg_charger/availability", "online", retain=True)
                    mqtt.publish(state_topic, data)
                    print(
                        f"Published MG charger: state={data.get('work_state')} "
                        f"power={data.get('power_w')}W current={data.get('charge_current_set_a')}A "
                        f"energy={data.get('charge_energy_once_kwh')}kWh temp={data.get('temperature_c')}C",
                        flush=True,
                    )
                    for breaker in breakers:
                        breaker_device = tuya.get_device(breaker["id"])
                        breaker_name = breaker.get("name") or breaker_device.get("custom_name") or breaker_device.get("name") or breaker["id"]
                        breaker_slug = slugify(breaker_name)
                        breaker_state_topic = f"tuya_breaker/{breaker_slug}/state"
                        breaker_availability_topic = f"tuya_breaker/{breaker_slug}/availability"
                        breaker_data = decode_breaker_status(breaker_device, tuya.get_status(breaker["id"]))
                        publish_breaker_discovery(
                            mqtt,
                            breaker_state_topic,
                            breaker_availability_topic,
                            breaker_slug,
                            breaker_name,
                            breaker_device.get("model") or breaker_device.get("product_name"),
                        )
                        mqtt.publish(breaker_availability_topic, "online", retain=True)
                        mqtt.publish(breaker_state_topic, breaker_data)
                        print(
                            f"Published breaker {breaker_name}: power={breaker_data.get('power_w')}W "
                            f"current={breaker_data.get('current_a')}A voltage={breaker_data.get('voltage_v')}V "
                            f"energy={breaker_data.get('reported_energy_kwh')}kWh temp={breaker_data.get('temperature_c')}C",
                            flush=True,
                        )
                except Exception as exc:
                    print(f"Cloud read/publish failed: {exc}", flush=True)
                    mqtt.publish("tuya_mg_charger/availability", "offline", retain=True)
                    for breaker in breakers:
                        mqtt.publish(f"tuya_breaker/{slugify(breaker.get('name') or breaker['id'])}/availability", "offline", retain=True)

            for local_device in local_devices:
                local_name = local_device["name"]
                local_slug = slugify(local_name)
                local_state_topic = f"tuya_breaker/{local_slug}/state"
                local_availability_topic = f"tuya_breaker/{local_slug}/availability"
                publish_breaker_discovery(
                    mqtt,
                    local_state_topic,
                    local_availability_topic,
                    local_slug,
                    local_name,
                    local_device.get("model"),
                )
                try:
                    local_data = read_local_tuya_device(local_device)
                    mqtt.publish(local_availability_topic, "online", retain=True)
                    mqtt.publish(local_state_topic, local_data)
                    print(
                        f"Published local breaker {local_name}: power={local_data.get('power_w')}W "
                        f"current={local_data.get('current_a')}A voltage={local_data.get('voltage_v')}V "
                        f"energy={local_data.get('reported_energy_kwh')}kWh temp={local_data.get('temperature_c')}C",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"Local read/publish failed for {local_name}: {exc}", flush=True)
                    mqtt.publish(local_availability_topic, "offline", retain=True)
        except Exception as exc:
            print(f"MQTT connection/publish failed: {exc}", flush=True)
        finally:
            if mqtt is not None:
                mqtt.close()
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
