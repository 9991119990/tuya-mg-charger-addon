# Tuya MG Charger MQTT Home Assistant Add-on

Reads MG charger data from Tuya Cloud API and publishes it to MQTT using Home Assistant MQTT discovery.

## Published values

- Current charging power in W
- Configured charging current in A
- Charging session energy in kWh
- Charger temperature
- Work state
- Work mode
- Switch state as binary sensor

## Required configuration

Create or use an existing Tuya IoT Cloud project and provide:

- `client_id` - Tuya Access ID / Client ID
- `client_secret` - Tuya Access Secret / Client Secret
- `device_id` - Tuya device ID of the charger
- MQTT broker credentials

The add-on only reads cloud status and publishes sensors. It does not control the charger.
