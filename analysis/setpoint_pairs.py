"""Which setpoint pair should the planner switch between? Closed-loop backtest. (DEV-1)

The planner chooses per hour between an idle and a heat value of the active room setpoint. Today
they are the owner's program values (18 / 23 °C). A wider pair moves more heat into cheap hours
but runs hotter flows at a lower COP. This replays the planner hourly as the service runs it
(36-h horizon, tomorrow's prices only from 14:00 local), applies the first hour, and steps the
house model; the weather "forecast" is the actual weather (optimistic for every pair alike).
The house model is the world here: what it says about pairs it was not fitted on (26 °C) is an
extrapolation.

Electricity uses a COP that falls with the curve's flow temperature, measured 2026-11-16..18:
COP = 5.65 - 0.124 * (flow - 28.3) (power reads, heating only, flow < 40 °C). Each pair runs
twice: with the planner as it is (a fixed COP) and with a planner that prices each hour at that
flow-dependent COP ("cop-aware").

Baseline: the pump's own time program at 18 / 23 (Normal 21-07), no writes.

The planner itself is COP-aware since 2026-11-19 (with this COP), so rerunning against a later
src/ makes the plain rows COP-aware too; the results below are from cf892f2 and 9896f22.

Result 2026-11-18 (winter 2025-12..02 / autumn 2026-10-15..11-10, €): program 163.8 / 4.85;
planner 18/23 161.9 / 2.77, cop-aware 156.3 / 2.85; 16/23 159.5 / 2.25, cop-aware 157.0 / 2.48;
18/26 172.4 / 2.70, cop-aware 160.7 / 2.69; 16/26 176.2 / 2.34, cop-aware 171.1 / 2.34. A hotter
heat value buys cheaper kWh but loses more on COP; knowing the COP is worth more than any pair.

Args: dir src [pairs | gap]; dir holds prices/fi.json (Energy-Charts FI) and wx/wind.json (Open-Meteo
hourly temperature_2m and shortwave_radiation, Europe/Helsinki); src the app's src/; pairs like
18/23,16/26 (idle/heat). Runs the pairs and periods in parallel, one process each.

`gap` instead asks why the planner saves so little in winter (COP-aware, 18/23): a wider comfort
band, no target penalty, prices known 36 h ahead without start and write penalties (the
planner's own ceiling), and a comfort-blind ceiling as in shift_sim.py: each day's heat of the
program at full power in the day's cheapest slots, at the program's COP that day.

Result 2026-11-19 (winter, €, saving against the program's 163.8): band 20-22 156.3 (7.5); no
target 155.6 (8.2); its own ceiling 154.9 (8.9); band 19.5-22.5 152.4 (11.4, 212 h below 20);
19-23 152.2 (11.7, 268 h); ceiling 19-23 148.2 (15.6); comfort-blind day shift 151.1 (12.7). The
planner gets within 1.4 € of what its band allows; the band is the lever, about 4 € a winter
for the first 0.5 K below 20, little after. Idle at 18 still heats 1.3-1.9 kW at -6 °C against
the 3.2 kW the house loses, so only about half the heat can move at all.
"""

import bisect
import collections
import dataclasses
import datetime as dt
import json
import sys
from multiprocessing import Pool
from zoneinfo import ZoneInfo

D, SRC = sys.argv[1], sys.argv[2]
MODE = sys.argv[3] if len(sys.argv) > 3 else "18/23,16/23,18/26,16/26"
PAIRS = [] if MODE == "gap" else [tuple(float(x) for x in p.split("/")) for p in MODE.split(",")]
sys.path.insert(0, SRC)
from heatpump_control import planner  # noqa: E402
from heatpump_control.controller import build_slots, decide  # noqa: E402
from heatpump_control.model import Curve, House  # noqa: E402
from heatpump_control.planner import Limits  # noqa: E402
from heatpump_control.program import Program  # noqa: E402

TZ = ZoneInfo("Europe/Helsinki")
UTC = dt.UTC
CURVE = Curve(-9.0, 0.6)  # the pump's curve since 2026-10-24
HOUSE = House()
VAT = 0.255
WARMUP = dt.timedelta(days=3)
PERIODS = {
    "winter": (dt.datetime(2025, 12, 1, tzinfo=TZ), dt.datetime(2026, 3, 1, tzinfo=TZ)),
    "autumn": (dt.datetime(2026, 10, 15, tzinfo=TZ), dt.datetime(2026, 11, 11, tzinfo=TZ)),
}


def cop(flow: float) -> float:
    return 5.65 - 0.124 * (flow - 28.3)


p = json.load(open(f"{D}/prices/fi.json"))
PRICES = [(dt.datetime.fromtimestamp(t, UTC), v) for t, v in zip(p["unix_seconds"], p["price"]) if v is not None]
w = json.load(open(f"{D}/wx/wind.json"))["hourly"]
WT = [dt.datetime.fromisoformat(t).replace(tzinfo=TZ).astimezone(UTC) for t in w["time"]]
TEMPS = list(zip(WT, w["temperature_2m"]))
RAD = list(zip(WT, w["shortwave_radiation"]))
PT, TT = [t for t, _ in PRICES], [t for t, _ in TEMPS]


def known_prices(now: dt.datetime, perfect: bool = False):
    """Prices as the service would have them: through today, and tomorrow from 14:00 local
    (`perfect`: always the next 37 h)."""
    local = now.astimezone(TZ)
    days = 2 if local.hour >= 14 else 1
    end = dt.datetime.combine(local.date() + dt.timedelta(days=days), dt.time(), TZ).astimezone(UTC)
    if perfect:
        end = now + dt.timedelta(hours=37)
    i, j = bisect.bisect_left(PT, now - dt.timedelta(hours=1)), bisect.bisect_left(PT, end)
    return PRICES[i:j]


def weather(now: dt.datetime):
    i, j = bisect.bisect_left(TT, now - dt.timedelta(hours=2)), bisect.bisect_left(TT, now + dt.timedelta(hours=38))
    return TEMPS[i:j], RAD[i:j]


class World:
    """The house as the model sees it, with electricity at the flow-dependent COP."""

    def __init__(self, t_room: float, t_slab: float):
        self.tr, self.ts = t_room, t_slab
        self.eur = self.kwh = self.heat_kwh = 0.0
        self.rooms: list[float] = []
        self.starts = 0
        self.prev = None
        self.days = collections.defaultdict(list)  # local date -> [(price, heat kWh, el kWh)]

    def step(self, slot, setpoint: float, asks_heat: bool) -> None:
        flow = CURVE.flow_c(setpoint, slot.t_out)
        heat = HOUSE.heat_kw(flow, self.ts)
        kwh = heat * 0.25 / cop(flow)
        self.kwh += kwh
        self.heat_kwh += heat * 0.25
        self.eur += kwh * slot.price / 1000 * (1 + VAT)
        self.days[slot.start.astimezone(TZ).date()].append((slot.price, heat * 0.25, kwh))
        self.tr, self.ts = HOUSE.step(self.tr, self.ts, slot.t_out, slot.radiation, heat, 0.25)
        self.rooms.append(self.tr)
        if asks_heat and self.prev is False:
            self.starts += 1
        self.prev = asks_heat


def slots_at(now, perfect: bool = False):
    temps, rad = weather(now)
    return build_slots(now, known_prices(now, perfect), temps, rad)


def baseline(start, end, t_room=21.0, t_slab=25.0) -> World:
    prog, world, now = Program(), World(t_room, t_slab), start
    while now < end:
        for s in slots_at(now)[:4]:
            h = prog.heating(s.start)
            world.step(s, prog.heat_c if h else prog.idle_c, h)
        now += dt.timedelta(hours=1)
    return world


PLAIN_SIM = planner._Sim


class CopAwareSim(planner._Sim):
    """The planner's simulator with electricity at the flow-dependent COP, per action."""

    def __init__(self, slots, house, curve, setpoints, limits):
        super().__init__(slots, house, curve, setpoints, limits)
        self.eur_by_action = [[s.price / 1000 * (1 + limits.vat) / cop(f) for s, f in zip(slots, self.flow[a])] for a in (0, 1)]

    def run(self, idx, a, tr, ts, trace=None):
        h = self.h
        k_sr, ua, cs, cr = h.slab_room_kw_per_k, h.ua_kw_per_k, h.slab_kwh_per_k, h.room_kwh_per_k
        eur = pen = 0.0
        flow, price = self.flow[a], self.eur_by_action[a]
        for i in idx:
            heat = min(h.heat_max_kw, max(0.0, h.heat_kw_per_k * (flow[i] - ts) + h.heat_offset_kw))
            eur += heat * planner.SLOT_H * price[i]
            q = k_sr * (ts - tr)
            ts += (heat - q) * planner.SLOT_H / cs
            tr += (q + self.gains[i] - ua * (tr - self.t_out[i])) * planner.SLOT_H / cr
            pen += self.comfort(tr)
            if trace is not None:
                trace.append((tr, ts))
        return eur, pen, tr, ts


def day_shift_ceiling(world: World) -> float:
    """Each day's heat at full power in that day's cheapest slots, at the day's COP; no comfort."""
    eur, cap = 0.0, HOUSE.heat_max_kw * 0.25
    for slots in world.days.values():
        heat, el = sum(h for _, h, _ in slots), sum(k for _, _, k in slots)
        if heat <= 0:
            continue
        for price, _, _ in sorted(slots):
            take = min(cap, heat)
            eur += take * el / sum(h for _, h, _ in slots) * price / 1000 * (1 + VAT)
            heat -= take
            if heat <= 0:
                break
    return round(eur, 2)


def run(job):
    if isinstance(job, tuple):
        period, (idle, heat), aware = job
        job = {"period": period, "idle": idle, "heat": heat, "aware": aware}
    period, idle, heat, aware = job["period"], job["idle"], job["heat"], job["aware"]
    limits = Limits()
    if "band" in job:
        limits = dataclasses.replace(limits, band_c=job["band"])
    if "target_pen" in job:
        limits = dataclasses.replace(limits, target_penalty_eur_per_k_h=job["target_pen"])
    if job.get("no_pen"):
        limits = dataclasses.replace(limits, start_penalty_eur=0.0, write_penalty_eur=0.0)
    perfect = job.get("perfect", False)
    planner._Sim = CopAwareSim if aware else PLAIN_SIM  # every job: pool workers are reused
    start, end = PERIODS[period]
    warm = baseline(start - WARMUP, start)
    if (idle, heat) == (None, None):
        world = baseline(start, end, warm.tr, warm.ts)
        return period, "program", summary(world, start, end, 0, 0) | {"day_shift_ceiling_eur": day_shift_ceiling(world)}
    prog = Program(heat_c=heat, idle_c=idle)
    world, now = World(warm.tr, warm.ts), start.astimezone(UTC)
    registers = prog.defaults()
    writes, per_day = 0, {}
    while now < end:
        slots = slots_at(now, perfect)
        r = decide(slots, world.tr, world.ts, registers, CURVE, HOUSE, limits=limits, program=prog, write_cap=10**6 if job.get("no_pen") else 12)
        first = [s.start for s in slots[:4]]
        for wr in r.setpoints.writes:
            if wr.at in first:
                registers[wr.register] = wr.value
                writes += 1
                day = wr.at.astimezone(TZ).date()
                per_day[day] = per_day.get(day, 0) + 1
        for s, h in zip(slots[:4], r.plan.heating[:4]):
            world.step(s, heat if h else idle, h)
        now += dt.timedelta(hours=1)
    name = job.get("name") or f"{idle:g}/{heat:g}" + (" cop-aware" if aware else "")
    return period, name, summary(world, start, end, writes, max(per_day.values(), default=0))


def summary(world: World, start, end, writes, max_writes_day) -> dict:
    days = (end - start).total_seconds() / 86400
    rooms = world.rooms
    return {
        "eur": round(world.eur, 2), "kwh": round(world.kwh), "heat_kwh": round(world.heat_kwh),
        "c_kwh": round(100 * world.eur / world.kwh, 2) if world.kwh else None,
        "room_min": round(min(rooms), 2), "room_max": round(max(rooms), 2),
        "room_mean": round(sum(rooms) / len(rooms), 2),
        "h_below_20": round(sum(t < 20.0 for t in rooms) / 4), "h_above_22": round(sum(t > 22.0 for t in rooms) / 4),
        "starts_day": round(world.starts / days, 1), "writes_day": round(writes / days, 1), "max_writes_day": max_writes_day,
    }


GAP = [
    {"name": "band 20-22 (reference)"},
    {"name": "band 19.5-22.5", "band": (19.5, 22.5)},
    {"name": "band 19-23", "band": (19.0, 23.0)},
    {"name": "band 20-22, no target", "target_pen": 0.0},
    {"name": "band 19.5-22.5, no target", "band": (19.5, 22.5), "target_pen": 0.0},
    {"name": "ceiling 20-22: 36 h prices, no penalties", "perfect": True, "no_pen": True},
    {"name": "ceiling 19-23: 36 h prices, no penalties, no target", "band": (19.0, 23.0), "target_pen": 0.0, "perfect": True, "no_pen": True},
]

if __name__ == "__main__":
    if MODE == "gap":
        jobs = [("winter", (None, None), False)]
        jobs += [{"period": "winter", "idle": 18.0, "heat": 23.0, "aware": True} | g for g in GAP]
        jobs.sort(key=lambda j: not isinstance(j, dict) or "band" not in j)  # the slow wide bands first
    else:
        jobs = [(period, (None, None), False) for period in PERIODS]
        jobs += [(period, pair, aware) for period in PERIODS for pair in PAIRS for aware in (False, True)]
        jobs.sort(key=lambda j: j[0] != "winter")  # the long ones first
    with Pool(4) as pool:
        results = pool.map(run, jobs, chunksize=1)
    for period, name, s in results:
        print(json.dumps({"period": period, "pair": name} | s), flush=True)
