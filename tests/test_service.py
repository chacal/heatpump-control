import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from heatpump_control import ha
from heatpump_control.metrics import Snapshot
from heatpump_control.rounds import Inputs, RoundError, ha_state, plan_round, round_log
from heatpump_control.service import Service, next_round
from heatpump_control.settings import AWAY_MIN_C, Settings

NOW = datetime(2026, 11, 18, 16, 0, 30, tzinfo=UTC)
PUMP = {"normal_c": 23.0, "reduced_c": 18.0, "curve_level": -9.0, "curve_slope": 0.6}


def inputs(**kw):
    hours = [NOW.replace(minute=0, second=0) + timedelta(hours=i) for i in range(-2, 40)]
    d = dict(
        prices=[(t, 30.0 if t.hour < 5 else 150.0) for t in hours],
        temps=[(t, 5.0) for t in hours],
        radiation=[(t, 0.0) for t in hours],
        pump=PUMP,
        ret_start=24.0,
        speeds=[(NOW - timedelta(minutes=5 * i), 30.0 if i % 3 else 0.0) for i in range(576, 0, -1)],
    )
    return Inputs(**(d | kw))


# Settings: untrusted values from HA


def test_settings_clamp_and_keep_a_band():
    s = Settings()
    assert s.set("comfort_min", "10").comfort_min == 18.0
    assert s.set("comfort_max", "99").comfort_max == 24.0
    assert s.set("comfort_min", "21.8").comfort_min == 21.0  # max 22 - 1 K
    assert s.set("comfort_max", "nan") == s
    assert s.set("comfort_min", "warm") == s
    assert s.set("enabled", "OFF").enabled is False
    assert s.set("away", "maybe") == s
    assert s.set("unknown", "1") == s


def test_settings_round_trip_and_away_band():
    s = Settings(comfort_min=21.5, comfort_max=23.5, away=True)
    back = Settings.from_json(s.to_json())
    assert back == s
    assert back.band() == (AWAY_MIN_C, 23.5)
    assert Settings.from_json(b"not json") == Settings()
    assert Settings.from_json(b'{"comfort_max": 23}') == Settings(comfort_max=23.0)


# Home Assistant discovery


def test_discovery_topics_and_ids_are_unique_and_well_formed():
    msgs = ha.discovery()
    topics = [t for t, _ in msgs]
    assert len(set(topics)) == len(topics)
    ids = set()
    for topic, payload in msgs:
        assert topic.startswith(f"{ha.DISCOVERY}/") and topic.endswith("/config")
        cfg = json.loads(payload)
        ids.add(cfg["unique_id"])
        assert cfg["availability_topic"] == ha.AVAILABILITY
        if "command_topic" in cfg:
            assert cfg["command_topic"].startswith(ha.SET + "/")
    assert len(ids) == len(msgs)


# One round


def test_round_plans_with_the_settings_band():
    res = plan_round(NOW, 21.0, inputs(), Settings(comfort_min=20.5, comfort_max=22.5))
    entry = round_log(res)
    assert entry["band"] == [20.5, 22.5]
    assert len(entry["slots"]["heating"]) == len(res.round.slots) > 100
    state = ha_state(res, "shadow")
    assert state["mode"] == "shadow" and isinstance(state["heat_now"], bool)
    json.dumps(entry), json.dumps(state)


@pytest.mark.parametrize(
    "t_room, kw, msg",
    [
        (None, {}, "bedroom"),
        (21.0, {"ret_start": None}, "pump history"),
        (21.0, {"pump": PUMP | {"normal_c": None}}, "pump settings"),
        (21.0, {"prices": []}, "prices"),
    ],
)
def test_round_says_which_input_is_missing(t_room, kw, msg):
    with pytest.raises(RoundError, match=msg):
        plan_round(NOW, t_room, inputs(**kw))


def test_metrics_render():
    snap = Snapshot()
    assert 'outcome="ok"} 0' in snap.render()
    res = plan_round(NOW, 21.0, inputs())
    snap.update(result=res, log=round_log(res))
    snap.count(True)
    text = snap.render()
    assert "heatpump_control_bedroom_median_celsius 21.0" in text
    assert 'heatpump_control_rounds_total{outcome="ok"} 1' in text
    assert json.loads(snap.plan_json())["t_room"] == 21.0


def test_next_round_is_30_s_past_the_next_quarter():
    assert next_round(datetime(2026, 11, 18, 16, 0, 10, tzinfo=UTC)) == datetime(2026, 11, 18, 16, 0, 30, tzinfo=UTC)
    assert next_round(datetime(2026, 11, 18, 16, 0, 30, tzinfo=UTC)) == datetime(2026, 11, 18, 16, 15, 30, tzinfo=UTC)
    assert next_round(datetime(2026, 11, 18, 16, 59, 0, tzinfo=UTC)) == datetime(2026, 11, 18, 17, 0, 30, tzinfo=UTC)


# Message handling, without a broker


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def msg(topic, payload):
    return SimpleNamespace(topic=topic, payload=payload if isinstance(payload, bytes) else json.dumps(payload).encode())


def test_messages_update_rooms_and_settings():
    svc = Service({"MQTT_BROKER": "localhost"})
    c = FakeClient()
    ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    svc._on_message(c, None, msg("/sensor/S202/m/state", {"instance": "S202", "temperature": 21.4, "ts": ts}))
    svc._on_message(c, None, msg("/sensor/S203/m/state", b"garbage"))
    assert svc.rooms.median(datetime.now(UTC)) == 21.4
    svc._on_message(c, None, msg(f"{ha.SET}/comfort_min", b"20.5"))
    assert svc.settings.comfort_min == 20.5
    assert c.published[-1][0] == ha.SETTINGS and c.published[-1][2] is True
    svc._on_message(c, None, msg(f"{ha.SET}/comfort_min", b"oops"))
    assert svc.settings.comfort_min == 20.5  # rejected, and echoed so HA snaps back
    assert json.loads(c.published[-1][1])["comfort_min"] == 20.5
    svc._on_message(c, None, msg(ha.SETTINGS, Settings(away=True).to_json().encode()))
    assert svc.settings.away is True


@pytest.mark.skipif(not os.environ.get("MQTT_TEST_BROKER"), reason="needs MQTT_TEST_BROKER (host:port of a test mosquitto)")
def test_against_a_real_broker():
    import time

    import paho.mqtt.client as mqtt

    host, port = os.environ["MQTT_TEST_BROKER"].split(":")
    svc = Service({"MQTT_BROKER": host, "MQTT_PORT": port, "MQTT_CLIENT_ID": "hpc-test"})
    svc.client.connect(host, int(port))
    svc.client.loop_start()
    seen = {}
    probe = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hpc-probe")
    probe.on_message = lambda c, u, m: seen.__setitem__(m.topic, m.payload)
    probe.connect(host, int(port))
    probe.subscribe([("homeassistant/#", 0), ("heatpump-control/#", 0)])
    probe.loop_start()
    try:
        deadline = time.time() + 5
        while not svc.snapshot.mqtt_connected and time.time() < deadline:
            time.sleep(0.05)
        ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        probe.publish("/sensor/S204/m/state", json.dumps({"instance": "S204", "temperature": 20.9, "ts": ts}))
        probe.publish(f"{ha.SET}/away", "ON")
        deadline = time.time() + 5
        while (svc.rooms.median(datetime.now(UTC)) is None or not svc.settings.away) and time.time() < deadline:
            time.sleep(0.05)
        assert svc.rooms.median(datetime.now(UTC)) == 20.9
        assert svc.settings.away is True
        time.sleep(0.3)
        assert seen.get(ha.AVAILABILITY) == b"online"
        assert json.loads(seen[ha.SETTINGS])["away"] is True
        assert sum(t.startswith("homeassistant/") for t in seen) == len(ha.discovery())
    finally:
        svc.client.loop_stop(); probe.loop_stop()
        svc.client.disconnect(); probe.disconnect()


def test_hostile_sensor_messages_are_ignored():
    svc = Service({"MQTT_BROKER": "localhost"})
    c = FakeClient()
    now = datetime.now(UTC)
    iso = lambda t: t.isoformat().replace("+00:00", "Z")
    for payload in (
        [],
        {"temperature": 21.0, "ts": 12},
        {"temperature": True, "ts": iso(now)},
        {"temperature": 21.0, "ts": "2026-11-18T10:00:00"},  # no timezone
        {"temperature": 21.0, "ts": "not a time"},
        {"temperature": 30.0, "ts": iso(now + timedelta(days=365))},  # would pin the sensor
    ):
        svc._on_message(c, None, msg("/sensor/S202/m/state", payload))
    assert svc.rooms.latest == {}
    svc._on_message(c, None, msg("/sensor/S202/m/state", {"temperature": 21.0, "ts": iso(now)}))
    assert svc.rooms.median(datetime.now(UTC)) == 21.0


def test_a_round_publishes_state_and_settings(monkeypatch):
    import heatpump_control.service as service

    monkeypatch.setattr(service, "fetch_inputs", lambda now, prom, latlon: inputs())
    svc = Service({"MQTT_BROKER": "localhost", "HEATPUMP_LATLON": "0,0"})
    svc.client = FakeClient()
    svc._on_message(svc.client, None, msg("/sensor/S202/m/state", {"temperature": 21.0, "ts": NOW.isoformat()}))
    svc.run_round(NOW)
    topics = {t: p for t, p, retain in svc.client.published if retain}
    assert json.loads(topics[ha.STATE])["bedrooms"] == 21.0
    assert json.loads(topics[ha.SETTINGS]) == json.loads(Settings().to_json())
