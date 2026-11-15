"""Inputs from public APIs and Prometheus, over plain HTTP (stdlib only).

- Spot prices: Energy-Charts (Fraunhofer ISE), €/MWh without VAT, 15-minute since 2025-10.
- Forecast: the Finnish Meteorological Institute's open data. Temperature from FMI's edited
  forecast (the forecasters' own, shown on ilmatieteenlaitos.fi); global radiation from its
  MEPS model run (2.5 km, ~2 days), since the edited forecast doesn't publish radiation.
- Pump state: Prometheus `heatpump_*`, written every ~30 s from vcontrold on the pump's Pi.
"""

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from bisect import bisect_right
from datetime import UTC, date, datetime, timedelta

ENERGY_CHARTS = "https://api.energy-charts.info/price"
FMI_WFS = "https://opendata.fmi.fi/wfs"
FMI_EDITED = "fmi::forecast::edited::weather::scandinavia::point::simple"
FMI_MEPS = "fmi::forecast::meps::surface::point::simple"
_BSWFS = "{http://xml.fmi.fi/schema/wfs/2.0}"
TIMEOUT_S = 30

Series = list[tuple[datetime, float]]


def _get(url: str, params: dict) -> bytes:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "heatpump-control"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.read()


def parse_energy_charts(body: bytes) -> Series:
    d = json.loads(body)
    return [
        (datetime.fromtimestamp(t, UTC), float(p))
        for t, p in zip(d["unix_seconds"], d["price"], strict=True)
        if p is not None
    ]


def spot_prices(first: date, last: date, zone: str = "FI") -> Series:
    """Spot prices (€/MWh, no VAT) for the days `first`..`last`, sorted by start."""
    body = _get(ENERGY_CHARTS, {"bzn": zone, "start": first.isoformat(), "end": (last + timedelta(days=1)).isoformat()})
    return sorted(parse_energy_charts(body))


def parse_fmi(body: bytes) -> dict[str, Series]:
    out: dict[str, Series] = {}
    for el in ET.fromstring(body).iter(f"{_BSWFS}BsWfsElement"):
        value = float(el.findtext(f"{_BSWFS}ParameterValue"))
        if value != value:  # NaN: past the model's horizon
            continue
        at = datetime.fromisoformat(el.findtext(f"{_BSWFS}Time").replace("Z", "+00:00"))
        out.setdefault(el.findtext(f"{_BSWFS}ParameterName"), []).append((at, value))
    return {k: sorted(v) for k, v in out.items()}


def fmi_forecast(latlon: str, end: datetime) -> dict[str, Series]:
    """Hourly `Temperature` (edited) and `RadiationGlobal` (MEPS) up to `end` at "lat,lon"."""
    common = {"service": "WFS", "version": "2.0.0", "request": "getFeature", "latlon": latlon, "timestep": 60,
              "endtime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    temps = parse_fmi(_get(FMI_WFS, common | {"storedquery_id": FMI_EDITED, "parameters": "Temperature"}))
    sun = parse_fmi(_get(FMI_WFS, common | {"storedquery_id": FMI_MEPS, "parameters": "RadiationGlobal"}))
    return temps | sun


def step_value(series: Series, at: datetime) -> float | None:
    """The value in effect at `at` (a price): each lasts until the next, the last one as long as
    the step before it. None outside the series."""
    times = [t for t, _ in series]
    i = bisect_right(times, at) - 1
    if i < 0 or (i == len(series) - 1 and (len(series) < 2 or at >= times[-1] + (times[-1] - times[-2]))):
        return None
    return series[i][1]


def interpolate(series: Series, at: datetime) -> float | None:
    """Linear between the neighbouring points (a forecast); None outside the series, except
    that the first point also stands for the hour before it (a forecast starts at the next
    full hour)."""
    times = [t for t, _ in series]
    i = bisect_right(times, at)
    if i == 0:
        return series[0][1] if series and times[0] - at <= timedelta(hours=1) else None
    if i == len(series) and at > times[-1]:
        return None
    if i == len(series):
        return series[-1][1]
    (t0, v0), (t1, v1) = series[i - 1], series[i]
    return v0 + (v1 - v0) * (at - t0) / (t1 - t0)


def prometheus_value(url: str, expr: str) -> float | None:
    d = json.loads(_get(f"{url}/api/v1/query", {"query": expr}))
    result = d["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def pump_state(url: str) -> dict:
    """Compressor on, how many minutes it has been in that state (up to 120), the setpoints."""
    on = prometheus_value(url, "heatpump_Compressor")
    # Seconds since the compressor last changed state, from the 30-s samples of the last 2 h.
    since = prometheus_value(
        url, "time() - max_over_time(timestamp(changes(heatpump_Compressor[1m]) > 0)[2h:30s])"
    )
    return {
        "compressor_on": bool(on),
        "minutes_in_state": 120 if since is None else min(since / 60, 120),
        "normal_c": prometheus_value(url, "heatpump_RoomTempNormal"),
        "reduced_c": prometheus_value(url, "heatpump_RoomTempReduced"),
    }
