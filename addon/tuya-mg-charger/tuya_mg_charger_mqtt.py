#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import socket
import time
import urllib.parse
import urllib.request


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=sorted(ENDPOINTS), default="eu")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--mqtt-host", required=True)
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-user", default="")
    parser.add_argument("--mqtt-password", default="")
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    tuya = TuyaClient(ENDPOINTS[args.region], args.client_id, args.client_secret)
    state_topic = "tuya_mg_charger/state"

    while True:
        mqtt = None
        try:
            device = tuya.get_device(args.device_id)
            data = decode_status(device, tuya.get_status(args.device_id))
            mqtt = MqttClient(args.mqtt_host, args.mqtt_port, args.mqtt_user, args.mqtt_password)
            publish_discovery(mqtt, state_topic)
            mqtt.publish("tuya_mg_charger/availability", "online", retain=True)
            mqtt.publish(state_topic, data)
            print(
                f"Published MG charger: state={data.get('work_state')} "
                f"power={data.get('power_w')}W current={data.get('charge_current_set_a')}A "
                f"energy={data.get('charge_energy_once_kwh')}kWh temp={data.get('temperature_c')}C",
                flush=True,
            )
        except Exception as exc:
            print(f"Read/publish failed: {exc}", flush=True)
            if mqtt is not None:
                mqtt.publish("tuya_mg_charger/availability", "offline", retain=True)
        finally:
            if mqtt is not None:
                mqtt.close()
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
