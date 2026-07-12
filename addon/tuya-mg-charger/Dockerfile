ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3

WORKDIR /app
COPY tuya_mg_charger_mqtt.py /app/tuya_mg_charger_mqtt.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh /app/tuya_mg_charger_mqtt.py

CMD ["/run.sh"]
