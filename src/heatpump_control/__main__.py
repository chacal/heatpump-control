"""Commands:

    python -m heatpump_control plan [--json | --summary] < sensor-archive-lines.jsonl
    python -m heatpump_control serve

`plan`: one shadow round with the room readings from stdin as sensor-archive JSONL; prints the
plan (or only its JSON log line, or the `/summary.json` the service would serve). `serve`: the long-running service (service.py). Settings from
the environment: HEATPUMP_LATLON ("lat,lon" for the forecast; not in this public repo),
PROMETHEUS_URL, PRICE_EXTRA_C_KWH (service.py); `serve` takes more (service.py).
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from heatpump_control.program import HELSINKI
from heatpump_control.rooms import Rooms
from heatpump_control.rounds import RoundError, env_limits, fetch_inputs, plan_round, round_log, summary


def run_plan(now: datetime, as_json: bool = False, as_summary: bool = False) -> int:
    rooms = Rooms()
    for line in sys.stdin:
        if line.strip():
            rooms.update_archive_line(line)
    prometheus = os.environ.get("PROMETHEUS_URL", "http://haukkakallio.chacal.fi:9090")
    try:
        t_room = rooms.median(now)
        if t_room is None:
            raise RoundError("no fresh bedroom readings")
        res = plan_round(now, t_room, fetch_inputs(now, prometheus, os.environ["HEATPUMP_LATLON"]), limits=env_limits(os.environ))
    except RoundError as e:
        print(e, file=sys.stderr)
        return 1
    if as_summary:
        print(json.dumps(summary(res, "shadow"), indent=1))
        return 0
    entry = round_log(res)
    if as_json:
        print(json.dumps(entry))
        return 0
    r, pump = res.round, res.inputs.pump
    slots = r.slots
    print(f"bedrooms {res.t_room:.2f} °C ({', '.join(f'{s} {t:.2f}' for s, t in sorted(rooms.fresh(now).items()))}); "
          f"slab ~{res.t_slab:.1f} °C; setpoints N {pump['normal_c']:g} R {pump['reduced_c']:g}; "
          f"curve {pump['curve_level']:g}/{pump['curve_slope']:g}")
    print(f"{len(slots)} slots to {slots[-1].start.astimezone(HELSINKI):%a %H:%M}; "
          f"plan €{r.plan.cost_eur:.2f}, {r.plan.starts} starts; program €{r.baseline.cost_eur:.2f}, "
          f"{r.baseline.starts} starts; savings ~€{r.savings_eur:.2f}; "
          f"writes/day {', '.join(f'{d:%a} {n}' for d, n in sorted(r.setpoints.per_day().items())) or 0}"
          f"{' OVER CAP' if r.over_write_cap else ''}")
    print("time   c/kWh  out  sun  heat  room  slab  program  room")
    for s, h, tr, ts, b, br in zip(slots, r.plan.heating, r.plan.t_room, r.plan.t_slab, r.baseline.heating, r.baseline.t_room):
        print(f"{s.start.astimezone(HELSINKI):%H:%M} {res.limits.eur_per_kwh(s.price) * 100:6.2f} {s.t_out:4.1f} {s.radiation:4.0f}"
              f"  {'##' if h else '..'}  {tr:5.2f} {ts:5.1f}  {'##' if b else '..'}  {br:5.2f}")
    for w in r.setpoints.writes:
        print(f"write {w.at.astimezone(HELSINKI):%a %H:%M} {w.register} = {w.value:g}")
    print(json.dumps(entry), file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="heatpump_control")
    parser.add_argument("command", choices=["plan", "serve"])
    parser.add_argument("--json", action="store_true", help="plan: print only the round's JSON log line")
    parser.add_argument("--summary", action="store_true", help="plan: print only the round's summary")
    args = parser.parse_args()
    if args.command == "serve":
        from heatpump_control.service import main as serve

        return serve()
    return run_plan(datetime.now(UTC), args.json, args.summary)


if __name__ == "__main__":
    sys.exit(main())
