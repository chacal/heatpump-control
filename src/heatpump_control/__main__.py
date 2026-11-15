"""Shadow mode, one round: fetch the inputs, plan, print the plan and log it as one JSON line.

    python -m heatpump_control plan < sensor-archive-lines.jsonl

Room readings come from stdin as sensor-archive JSONL until the MQTT client exists. Settings
from the environment: HEATPUMP_LATLON ("lat,lon" for the forecast; not in this public repo),
PROMETHEUS_URL.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta

from heatpump_control.controller import MAX_HORIZON, decide, build_slots
from heatpump_control.program import HELSINKI, Program
from heatpump_control.rooms import Rooms
from heatpump_control.sources import fmi_forecast, pump_state, spot_prices


def run_plan(now: datetime) -> int:
    rooms = Rooms()
    for line in sys.stdin:
        if line.strip():
            rooms.update_archive_line(line)
    t_now = rooms.median(now)
    if t_now is None:
        print("no fresh bedroom readings", file=sys.stderr)
        return 1

    local = now.astimezone(HELSINKI).date()
    prices = spot_prices(local - timedelta(days=1), local + timedelta(days=1))
    forecast = fmi_forecast(os.environ["HEATPUMP_LATLON"], now + MAX_HORIZON)
    pump = pump_state(os.environ.get("PROMETHEUS_URL", "http://haukkakallio.chacal.fi:9090"))

    slots = build_slots(now, prices, forecast.get("Temperature", []), forecast.get("RadiationGlobal", []))
    if not slots:
        print("no prices or forecast for the next slot", file=sys.stderr)
        return 1
    registers = {"normal": pump["normal_c"], "reduced": pump["reduced_c"]}
    r = decide(slots, t_now, pump["compressor_on"], int(pump["minutes_in_state"] // 15), registers)

    print(f"bedrooms {t_now:.2f} °C ({', '.join(f'{s} {t:.2f}' for s, t in sorted(rooms.fresh(now).items()))}); "
          f"compressor {'on' if pump['compressor_on'] else 'off'} {pump['minutes_in_state']:.0f} min; "
          f"setpoints N {pump['normal_c']:g} R {pump['reduced_c']:g}")
    print(f"{len(slots)} slots to {slots[-1].start.astimezone(HELSINKI):%a %H:%M}; "
          f"plan €{r.plan.cost_eur:.2f}, {r.plan.starts} starts; program €{r.baseline.cost_eur:.2f}, "
          f"{r.baseline.starts} starts; savings ~€{r.savings_eur:.2f}; writes/day {', '.join(f'{d:%a} {n}' for d, n in sorted(r.setpoints.per_day().items())) or 0}"
          f"{' OVER CAP' if r.over_write_cap else ''}")
    print("time   c/kWh  out  sun  heat  room  program")
    for s, h, t, b in zip(slots, r.plan.heating, r.plan.t_in, r.baseline.heating):
        print(f"{s.start.astimezone(HELSINKI):%H:%M} {s.price / 10 * 1.255:6.2f} {s.t_out:4.1f} {s.radiation:4.0f}"
              f"  {'##' if h else '..'}  {t:5.2f}  {'##' if b else '..'}")
    for w in r.setpoints.writes:
        print(f"write {w.at.astimezone(HELSINKI):%a %H:%M} {w.register} = {w.value:g}")
    log = {
        "at": now.isoformat(), "t_now": t_now, "pump": pump, "slots": len(slots),
        "plan_eur": r.plan.cost_eur, "baseline_eur": r.baseline.cost_eur, "savings_eur": r.savings_eur,
        "starts": r.plan.starts, "write_penalty_eur": r.write_penalty_eur, "over_write_cap": r.over_write_cap,
        "heating": "".join("1" if h else "0" for h in r.plan.heating),
        "writes": [[w.at.isoformat(), w.register, w.value] for w in r.setpoints.writes],
    }
    print(json.dumps(log), file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="heatpump_control")
    parser.add_argument("command", choices=["plan"])
    parser.parse_args()
    return run_plan(datetime.now(UTC))


if __name__ == "__main__":
    sys.exit(main())
