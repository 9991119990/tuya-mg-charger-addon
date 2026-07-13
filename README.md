# Tuya MG Charger MQTT Home Assistant Add-on

Reads MG charger data from Tuya Cloud API and publishes it to MQTT using Home Assistant MQTT discovery. It can also read selected Tuya smart breakers/meters.

## Published charger values

- Current charging power in W
- Configured charging current in A
- Charging session energy in kWh
- Charger temperature
- Work state
- Work mode
- Switch state as binary sensor

## Published breaker values

For every device listed in `breakers_json`:

- Current power in W
- Current in A
- Voltage in V
- Reported energy value from Tuya `add_ele` in kWh
- Temperature if the device reports it
- Fault and relay status
- Switch state and child lock as binary sensors

## Required configuration

Create or use an existing Tuya IoT Cloud project and provide:

- `client_id` - Tuya Access ID / Client ID
- `client_secret` - Tuya Access Secret / Client Secret
- `device_id` - Tuya device ID of the charger
- `breakers_json` - optional JSON list of Tuya breaker devices to publish
- MQTT broker credentials

Example `breakers_json`:

```json
[{"id":"bf23117b300ad83b550caa","name":"Solar jistič"},{"id":"bf93ca8fc4e56106e5qrle","name":"EAsun jistič"},{"id":"bfc2b53a7ea8026e88ya9k","name":"Spotřeba EV auto"}]
```

The add-on only reads cloud status and publishes sensors. It does not control the charger or breakers.
