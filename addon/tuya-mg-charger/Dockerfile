ARG BUILD_FROM=ghcr.io/home-assistant/{arch}-base:3.22
FROM ${BUILD_FROM}

WORKDIR /app
COPY tuya_mg_charger_mqtt.py /app/tuya_mg_charger_mqtt.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh /app/tuya_mg_charger_mqtt.py

CMD ["/run.sh"]
