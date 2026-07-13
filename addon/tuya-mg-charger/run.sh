#!/usr/bin/with-contenv sh
set -eu

region=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["region"])')
client_id=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["client_id"])')
client_secret=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["client_secret"])')
device_id=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["device_id"])')
mqtt_host=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["mqtt_host"])')
mqtt_port=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["mqtt_port"])')
mqtt_user=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["mqtt_user"])')
mqtt_password=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["mqtt_password"])')
interval=$(python3 -c 'import json; print(json.load(open("/data/options.json"))["interval"])')
breakers_json=$(python3 -c 'import json; print(json.load(open("/data/options.json")).get("breakers_json", "[]"))')

echo "Starting Tuya MG Charger MQTT bridge"
echo "Region: ${region}"
echo "Device ID: ${device_id}"
echo "MQTT broker: ${mqtt_host}:${mqtt_port}"
echo "Interval: ${interval}s"
echo "Breakers JSON: ${breakers_json}"

exec python3 /app/tuya_mg_charger_mqtt.py \
  --region "${region}" \
  --client-id "${client_id}" \
  --client-secret "${client_secret}" \
  --device-id "${device_id}" \
  --mqtt-host "${mqtt_host}" \
  --mqtt-port "${mqtt_port}" \
  --mqtt-user "${mqtt_user}" \
  --mqtt-password "${mqtt_password}" \
  --interval "${interval}" \
  --breakers-json "${breakers_json}"
