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

from heatpump_control.controller import MAX_HORIZON, build_slots, decide
from heatpump_control.model import HEAT_KW_PER_SPEED_PCT, RETURN_MINUS_SLAB_K, Curve, observe_slab
from heatpump_control.program import HELSINKI
from heatpump_control.rooms import Rooms
from heatpump_control.sources import fmi_forecast, heat_history, pump_state, spot_prices


def run_plan(now: datetime) -> int:
    rooms = Rooms()
    for line in sys.stdin:
        if line.strip():
            rooms.update_archive_line(line)
    t_room = rooms.median(now)
    if t_room is None:
        print("no fresh bedroom readings", file=sys.stderr)
        return 1

    prometheus = os.environ.get("PROMETHEUS_URL", "http://haukkakallio.chacal.fi:9090")
    local = now.astimezone(HELSINKI).date()
    prices = spot_prices(local - timedelta(days=1), local + timedelta(days=1))
    forecast = fmi_forecast(os.environ["HEATPUMP_LATLON"], now + MAX_HORIZON)
    pump = pump_state(prometheus)
    ret_start, speeds = heat_history(prometheus, now)
    if ret_start is None or len(speeds) < 400:
        print("no pump history for the slab estimate", file=sys.stderr)
        return 1
    t_slab = observe_slab(ret_start - RETURN_MINUS_SLAB_K, [v * HEAT_KW_PER_SPEED_PCT for _, v in speeds], t_room, 5 / 60)

    slots = build_slots(now, prices, forecast.get("Temperature", []), forecast.get("RadiationGlobal", []))
    if not slots:
        print("no prices or forecast for the next slot", file=sys.stderr)
        return 1
    registers = {"normal": pump["normal_c"], "reduced": pump["reduced_c"]}
    curve = Curve(pump["curve_level"], pump["curve_slope"])
    r = decide(slots, t_room, t_slab, registers, curve)

    print(f"bedrooms {t_room:.2f} °C ({', '.join(f'{s} {t:.2f}' for s, t in sorted(rooms.fresh(now).items()))}); "
          f"slab ~{t_slab:.1f} °C; setpoints N {pump['normal_c']:g} R {pump['reduced_c']:g}; "
          f"curve {curve.level:g}/{curve.slope:g}")
    print(f"{len(slots)} slots to {slots[-1].start.astimezone(HELSINKI):%a %H:%M}; "
          f"plan €{r.plan.cost_eur:.2f}, {r.plan.starts} starts; program €{r.baseline.cost_eur:.2f}, "
          f"{r.baseline.starts} starts; savings ~€{r.savings_eur:.2f}; "
          f"writes/day {', '.join(f'{d:%a} {n}' for d, n in sorted(r.setpoints.per_day().items())) or 0}"
          f"{' OVER CAP' if r.over_write_cap else ''}")
    print("time   c/kWh  out  sun  heat  room  slab  program  room")
    for s, h, tr, ts, b, br in zip(slots, r.plan.heating, r.plan.t_room, r.plan.t_slab, r.baseline.heating, r.baseline.t_room):
        print(f"{s.start.astimezone(HELSINKI):%H:%M} {s.price / 10 * 1.255:6.2f} {s.t_out:4.1f} {s.radiation:4.0f}"
              f"  {'##' if h else '..'}  {tr:5.2f} {ts:5.1f}  {'##' if b else '..'}  {br:5.2f}")
    for w in r.setpoints.writes:
        print(f"write {w.at.astimezone(HELSINKI):%a %H:%M} {w.register} = {w.value:g}")
    # When Energy-Charts has tomorrow's prices shows as the first round whose prices_until jumps.
    prices_until = prices[-1][0] + (prices[-1][0] - prices[-2][0]) if len(prices) > 1 else None
    log = {
        "at": now.isoformat(), "t_room": t_room, "t_slab": t_slab, "pump": pump, "slots": len(slots),
        "prices_until": prices_until.isoformat() if prices_until else None,
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
