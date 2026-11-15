import json
from datetime import UTC, datetime, timedelta

from heatpump_control.controller import build_slots
from heatpump_control.rooms import Rooms
from heatpump_control.sources import interpolate, step_value, parse_energy_charts, parse_fmi

NOW = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)


def line(sensor, temp, at=NOW):
    ts = at.isoformat().replace("+00:00", "Z")
    return json.dumps({"topic": f"/sensor/{sensor}/m/state", "payload": {"instance": sensor, "temperature": temp, "ts": ts}})


def test_bedroom_median_drops_bad_stale_and_other_sensors():
    rooms = Rooms()
    for l in [
        line("S202", 21.0),
        line("S203", 20.0),
        line("S204", 0.8),  # dying battery
        line("S211", 22.0, NOW - timedelta(minutes=10)),  # stale
        line("S205", 25.0),  # living room: not a bedroom
    ]:
        rooms.update_archive_line(l)
    assert rooms.fresh(NOW) == {"S202": 21.0, "S203": 20.0}
    assert rooms.median(NOW) == 20.5


def test_no_fresh_readings():
    assert Rooms().median(NOW) is None


def test_parse_energy_charts_skips_missing():
    body = json.dumps({"unix_seconds": [1794700800, 1794701700], "price": [12.5, None]}).encode()
    assert parse_energy_charts(body) == [(datetime.fromtimestamp(1794700800, UTC), 12.5)]


FMI = b"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:BsWfs="http://xml.fmi.fi/schema/wfs/2.0">
  <wfs:member><BsWfs:BsWfsElement>
    <BsWfs:Time>2026-11-15T09:00:00Z</BsWfs:Time><BsWfs:ParameterName>Temperature</BsWfs:ParameterName>
    <BsWfs:ParameterValue>7.7</BsWfs:ParameterValue></BsWfs:BsWfsElement></wfs:member>
  <wfs:member><BsWfs:BsWfsElement>
    <BsWfs:Time>2026-11-15T09:00:00Z</BsWfs:Time><BsWfs:ParameterName>RadiationGlobal</BsWfs:ParameterName>
    <BsWfs:ParameterValue>NaN</BsWfs:ParameterValue></BsWfs:BsWfsElement></wfs:member>
</wfs:FeatureCollection>"""


def test_parse_fmi_drops_nan():
    assert parse_fmi(FMI) == {"Temperature": [(datetime(2026, 11, 15, 9, tzinfo=UTC), 7.7)]}


def test_series_lookups():
    s = [(NOW, 0.0), (NOW + timedelta(hours=1), 4.0)]
    assert interpolate(s, NOW + timedelta(minutes=15)) == 1.0
    assert interpolate(s, NOW - timedelta(minutes=45)) == 0.0
    assert interpolate(s, NOW - timedelta(minutes=61)) is None
    assert step_value(s, NOW + timedelta(minutes=30)) == 0.0
    assert step_value(s, NOW + timedelta(minutes=90)) == 4.0
    assert step_value(s, NOW + timedelta(hours=2)) is None
    assert step_value(s, NOW - timedelta(minutes=1)) is None


def test_slots_stop_where_prices_end():
    prices = [(NOW + timedelta(minutes=15 * i), 50.0) for i in range(8)]
    temps = [(NOW + timedelta(hours=h), -3.0) for h in range(48)]
    slots = build_slots(NOW + timedelta(minutes=7), prices, temps, [])
    assert len(slots) == 8 and slots[0].start == NOW and slots[0].radiation == 0.0
