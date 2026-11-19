import base64
import json
import logging
import os
import ssl
import time
from datetime import datetime, timezone

logger = logging.getLogger("mqtt-mongo-bridge")


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"A variável {name} precisa ser definida no arquivo .env")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def build_document(message) -> dict:
    raw_payload = bytes(message.payload)
    try:
        payload_text = raw_payload.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "topic": message.topic,
            "payload_base64": base64.b64encode(raw_payload).decode("ascii"),
            "received_at": datetime.now(timezone.utc),
            "qos": message.qos,
            "retain": message.retain,
        }

    try:
        payload_data = json.loads(payload_text)
    except json.JSONDecodeError:
        payload_data = payload_text

    return {
        "topic": message.topic,
        "payload": payload_text,
        "payload_data": payload_data,
        "received_at": datetime.now(timezone.utc),
        "qos": message.qos,
        "retain": message.retain,
    }


def add_persistence_timestamp(document: dict) -> None:
    document["data"] = datetime.now(timezone.utc)


def rate_limit_key(document: dict) -> tuple:
    topic = document["topic"]
    payload_data = document.get("payload_data")
    if isinstance(payload_data, dict):
        inner = payload_data.get("payload")
        if isinstance(inner, dict):
            return (topic, tuple(sorted(inner.keys())))
        return (topic, tuple(sorted(payload_data.keys())))
    return (topic,)


def should_persist(key: tuple, now: float, last_saved_at: dict, interval_seconds: float) -> bool:
    if interval_seconds <= 0:
        last_saved_at[key] = now
        return True
    previous = last_saved_at.get(key)
    if previous is None or (now - previous) >= interval_seconds:
        last_saved_at[key] = now
        return True
    return False


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    import paho.mqtt.client as mqtt
    from pymongo import MongoClient

    broker_host = required_env("MQTT_HOST")
    mongo_uri = required_env("MONGO_URI")
    topic = required_env("MQTT_TOPIC")
    mongo_database = required_env("MONGO_DATABASE")
    mongo_collection = required_env("MONGO_COLLECTION")
    broker_port = int(os.getenv("MQTT_PORT", "1883"))
    qos = int(os.getenv("MQTT_QOS", "1"))
    save_interval = float(os.getenv("SAVE_INTERVAL_SECONDS", "900"))
    if qos not in (0, 1, 2):
        raise ValueError("MQTT_QOS precisa ser 0, 1 ou 2")
    if save_interval < 0:
        raise ValueError("SAVE_INTERVAL_SECONDS não pode ser negativo")

    last_saved_at: dict[tuple, float] = {}

    mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    collection = mongo_client[mongo_database][mongo_collection]
    collection.database.command("ping")
    logger.info("Conexão com MongoDB estabelecida (intervalo de gravação: %.0fs)", save_interval)

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=os.getenv("MQTT_CLIENT_ID", "mqtt-mongo-bridge"),
        clean_session=False,
        protocol=mqtt.MQTTv311,
    )
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "")
    if username:
        client.username_pw_set(username, password)
    if env_bool("MQTT_TLS"):
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    client.manual_ack_set(True)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error("Falha ao conectar ao broker MQTT: %s", reason_code)
            return
        logger.info("Conectado ao broker MQTT; assinando %s (QoS %s)", topic, qos)
        client.subscribe(topic, qos=qos)

    def on_message(client, userdata, message):
        try:
            document = build_document(message)
            payload = document["payload"] if "payload" in document else "base64:" + document["payload_base64"]
            key = rate_limit_key(document)
            if not should_persist(key, time.monotonic(), last_saved_at, save_interval):
                client.ack(message.mid, message.qos)
                logger.debug("Mensagem ignorada pelo intervalo de %.0fs (tópico=%s)", save_interval, message.topic)
                return

            logger.info("Mensagem recebida (tópico=%s, payload=%s)", message.topic, payload)
            add_persistence_timestamp(document)
            result = collection.insert_one(document)
            client.ack(message.mid, message.qos)
            logger.info("Mensagem gravada no MongoDB (id=%s, tópico=%s)", result.inserted_id, message.topic)
        except Exception:
            logger.exception("Não foi possível gravar a mensagem; ela não será confirmada ao broker")

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(broker_host, broker_port, keepalive=60)
        logger.info("Ouvindo mensagens no tópico %s", topic)
        client.loop_forever()
    finally:
        client.disconnect()
        mongo_client.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError) as error:
        logger.error("Configuração inválida: %s", error)
        raise SystemExit(1) from error
