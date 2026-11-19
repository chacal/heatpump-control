"""The long-running service (`python -m heatpump_control serve`), shadow mode.

Reads the bedroom sensors and the owner's controls from MQTT, plans every 15 minutes (30 s past
each quarter hour), and publishes the result: Home Assistant entities (ha.py), Prometheus metrics,
the latest plan and its summary over HTTP (metrics.py), and one JSON line per round to a log
file. Shadow mode writes nothing to the pump.

Environment: MQTT_BROKER (host), MQTT_PORT (1883; 8883 means TLS), MQTT_USERNAME, MQTT_PASSWORD,
MQTT_CLIENT_ID, PROMETHEUS_URL, HEATPUMP_LATLON ("lat,lon"), HTTP_HOST (127.0.0.1), HTTP_PORT
(9841; all endpoints), SUMMARY_PORT (none if unset; only /summary.json, on all addresses, for a
reader on the network), PRICE_EXTRA_C_KWH (0; per-kWh costs on top of spot: margin, transfer,
tax, c/kWh incl. VAT), ROUND_LOG (a file to append the rounds to; none if unset).
"""

import json
import logging
import os
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta

import paho.mqtt.client as mqtt

from heatpump_control import ha
from heatpump_control.metrics import Snapshot, serve_http
from heatpump_control.rooms import BEDROOMS, Rooms
from heatpump_control.rounds import RoundError, env_limits, fetch_inputs, ha_state, plan_round, round_log, summary
from heatpump_control.settings import Settings

log = logging.getLogger("heatpump_control")
MODE = "shadow"
FIRST_ROUND_AFTER_S = 60  # time to hear every bedroom sensor (they publish every ~5 s)
ROUND_OFFSET_S = 30


def next_round(now: datetime) -> datetime:
    q = now.replace(minute=now.minute - now.minute % 15, second=ROUND_OFFSET_S, microsecond=0)
    return q if q > now else q + timedelta(minutes=15)


class Service:
    def __init__(self, env: dict[str, str]):
        self.env = env
        self.rooms = Rooms()
        self.settings = Settings()
        self.lock = threading.Lock()
        self.snapshot = Snapshot()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=env.get("MQTT_CLIENT_ID", "heatpump-control"))
        if env.get("MQTT_USERNAME"):
            self.client.username_pw_set(env["MQTT_USERNAME"], env.get("MQTT_PASSWORD"))
        port = int(env.get("MQTT_PORT", "1883"))
        if port == 8883:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.will_set(ha.AVAILABILITY, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.broker, self.port = env.get("MQTT_BROKER", "localhost"), port

    # MQTT callbacks run on paho's network thread.
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        self.snapshot.update(mqtt_connected=True)
        client.subscribe([(f"/sensor/{s}/m/state", 0) for s in BEDROOMS] + [(ha.SETTINGS, 1), (f"{ha.SET}/+", 1)])
        for topic, payload in ha.discovery():
            client.publish(topic, payload, qos=1, retain=True)
        client.publish(ha.AVAILABILITY, "online", qos=1, retain=True)
        log.info("MQTT connected to %s:%d", self.broker, self.port)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.snapshot.update(mqtt_connected=False)
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            if msg.topic == ha.SETTINGS:
                with self.lock:
                    self.settings = Settings.from_json(msg.payload)
            elif msg.topic.startswith(ha.SET + "/"):
                key = msg.topic.rsplit("/", 1)[1]
                with self.lock:
                    new = self.settings.set(key, msg.payload.decode(errors="replace"))
                    changed = new != self.settings
                    self.settings = new
                if changed:
                    log.info("setting %s -> %s", key, new)
                # Echo even when unchanged, so HA's entity snaps back from a rejected value.
                client.publish(ha.SETTINGS, new.to_json(), qos=1, retain=True)
            else:
                payload = json.loads(msg.payload)
                with self.lock:
                    self.rooms.update(msg.topic, payload, datetime.now(UTC))
        except Exception as e:  # any publisher's bad message must not stop paho's thread
            log.debug("ignored message on %s: %r", msg.topic, e)

    def run_round(self, now: datetime) -> None:
        try:
            with self.lock:
                t_room, settings = self.rooms.median(now), self.settings
            inputs = fetch_inputs(now, self.env.get("PROMETHEUS_URL", "http://localhost:9090"), self.env["HEATPUMP_LATLON"])
            res = plan_round(now, t_room, inputs, settings, env_limits(self.env))
        except (RoundError, OSError, ValueError, KeyError) as e:
            self.snapshot.count(False, str(e))
            log.error("round failed: %s", e)
            return
        except Exception as e:  # a bug in one round must not stop the service; the metric shows it
            self.snapshot.count(False, repr(e))
            log.exception("round failed")
            return
        mode = MODE if settings.enabled else "off"
        entry = round_log(res)
        self.snapshot.update(result=res, log=entry, summary=summary(res, mode))
        self.snapshot.count(True)
        self.client.publish(ha.STATE, json.dumps(ha_state(res, mode)), qos=1, retain=True)
        # The settings too: by now any retained ones have been read back, and HA's switches and
        # numbers show unknown until the settings topic has a value.
        self.client.publish(ha.SETTINGS, settings.to_json(), qos=1, retain=True)
        if path := self.env.get("ROUND_LOG"):
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        log.info("round: bedrooms %.2f, heat now %s, savings ~€%.2f", res.t_room, res.round.plan.heating[0], res.round.savings_eur)

    def serve(self) -> None:
        serve_http(self.snapshot, self.env.get("HTTP_HOST", "127.0.0.1"), int(self.env.get("HTTP_PORT", "9841")))
        if port := self.env.get("SUMMARY_PORT"):
            serve_http(self.snapshot, "0.0.0.0", int(port), paths=("/summary.json",))
        self.client.connect_async(self.broker, self.port, keepalive=60)
        self.client.loop_start()
        time.sleep(FIRST_ROUND_AFTER_S)
        self.run_round(datetime.now(UTC))
        while True:
            at = next_round(datetime.now(UTC))
            time.sleep(max((at - datetime.now(UTC)).total_seconds(), 0))
            self.run_round(datetime.now(UTC))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Service(dict(os.environ)).serve()
    return 0
