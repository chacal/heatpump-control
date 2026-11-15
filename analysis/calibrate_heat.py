"""Heat mode through the pump's heating curve (DEV-1, 2026-11-15).

Args: r5.txt rooms_f.csv recent_f.csv tout.txt rad.txt settings.json fit.json sec.json
rad.txt covering Dec 2025-Feb 2026 and Sep-Nov 2026; settings.json the hourly heatpump_RoomTempNormal,
RoomTempReduced, HeatingCurveLevel, HeatingCurveSlope; sec.json the hourly secondary return temperature
(`ret`); fit.json from calibrate_house.py.

Viessmann's curve: flow = RT + level - slope * D * (1.4347 + 0.021 D + 247.9e-6 D^2), D = tout - RT,
RT the active room setpoint. Heat = clip(h * (flow - slab) + h0, 0, max), slab from the two-node
house model (fit.json p2, Oct-Nov). Before 2026-10-17 16:00 Normal is active all day (Reduced
was 10, no schedule); from then Reduced 07-21, Normal 21-07.
"""
import collections, datetime as dt, json, statistics as st, sys
from zoneinfo import ZoneInfo

UTC, TZ = dt.UTC, ZoneInfo("Europe/Helsinki")
BED = ("S202", "S203", "S204", "S211")
r5, rooms_old, rooms_new, tout_f, rad_f, settings_f, fit_f = sys.argv[1:8]
SET = json.load(open(settings_f)); SET = {m: {int(t): v for t, v in d.items()} for m, d in SET.items()}
P2 = json.load(open(fit_f))["p2"]
SCHEDULE_FROM = dt.datetime(2026, 10, 17, 16, tzinfo=TZ)

samples = collections.defaultdict(dict)
for line in open(r5):
    n, t, v = line.split()
    samples[int(t)][n.strip("'")] = float(v)
heat_pct, dhw_h, n5, flows = collections.defaultdict(float), collections.defaultdict(float), collections.Counter(), collections.defaultdict(list)
reg_lo, reg_hi = dt.datetime(2025, 11, 10, tzinfo=UTC).timestamp(), dt.datetime(2026, 11, 9, tzinfo=UTC).timestamp()
year_pct = 0.0
for t, d in samples.items():
    if "Compressor" not in d:
        continue
    h = t - t % 3600
    n5[h] += 1
    flow = d.get("SecondaryCircuitFlowTemp", 0)
    if d["Compressor"] > 0 and flow < 40:
        heat_pct[h] += d.get("CompressorSpeed", 0) * 5 / 60
        flows[h].append(flow)
        if reg_lo <= t < reg_hi:
            year_pct += d.get("CompressorSpeed", 0) * 5 / 60
    elif d["Compressor"] > 0:
        dhw_h[h] += 5 / 60
k = 10692 / year_pct

rooms = collections.defaultdict(dict)
for path, lo, hi in ((rooms_old, None, dt.datetime(2026, 10, 1, tzinfo=UTC)), (rooms_new, dt.datetime(2026, 10, 1, tzinfo=UTC), None)):
    sec = "#ROOMS" if path == rooms_old else None
    for line in open(path):
        if line.startswith("#"):
            sec = line.strip(); continue
        if line.startswith("h,") or sec != "#ROOMS":
            continue
        h, r, t, n = line.strip().split(",")
        at = dt.datetime.fromisoformat(h).replace(tzinfo=UTC)
        if (lo and at < lo) or (hi and at >= hi) or r not in BED or int(n) < 20 or not 15 <= float(t) <= 35:
            continue
        rooms[int(at.timestamp())][r] = float(t)
tin = {h: st.median(v.values()) for h, v in rooms.items() if len(v) >= 2}
tout = {int(float(l.split()[0])): float(l.split()[1]) for l in open(tout_f)}
rad = {int(l.split()[0]): float(l.split()[1]) for l in open(rad_f)}


def curve(rt, level, slope, to):
    d = to - rt
    return rt + level - slope * d * (1.4347 + 0.021 * d + 247.9e-6 * d * d)


def setting(m, h):
    return SET[m].get(h, SET[m].get(h - 3600))


def active_rt(x):
    if x["local"] < SCHEDULE_FROM or x["local"].hour >= 21 or x["local"].hour < 7:
        return setting("RoomTempNormal", x["h"])
    return setting("RoomTempReduced", x["h"])


def build(lo, hi, skip=lambda x: False):
    seq, h = [], int(lo.timestamp())
    while h < hi.timestamp():
        if h in tin and h in tout and h in rad and n5[h] >= 10 and setting("RoomTempNormal", h) is not None:
            x = dict(h=h, local=dt.datetime.fromtimestamp(h, TZ), tin=tin[h], tout=tout[h], rad=rad[h],
                     heat=k * heat_pct[h] * 12 / n5[h], dhw=dhw_h[h] * 12 / n5[h],
                     flow=st.mean(flows[h]) if len(flows[h]) >= 10 else None)
            x["rt"] = active_rt(x)
            x["curve"] = curve(x["rt"], setting("HeatingCurveLevel", h), setting("HeatingCurveSlope", h), x["tout"])
            if not skip(x):
                seq.append(x)
        h += 3600
    return seq


AWAY = (dt.datetime(2026, 10, 8, 12, tzinfo=TZ), dt.datetime(2026, 10, 14, 12, tzinfo=TZ))
FIRE = (dt.datetime(2026, 1, 27, tzinfo=TZ), dt.datetime(2026, 2, 10, tzinfo=TZ))
autumn = build(dt.datetime(2026, 10, 15, tzinfo=TZ), dt.datetime(2026, 11, 14, tzinfo=TZ), lambda x: AWAY[0] <= x["local"] < AWAY[1])
winter = build(dt.datetime(2025, 12, 1, tzinfo=TZ), dt.datetime(2026, 3, 1, tzinfo=TZ), lambda x: FIRE[0] <= x["local"] < FIRE[1])

# 1. Does the formula match the measured flow while heating?
for name, seq in (("autumn", autumn), ("winter", winter)):
    pairs = [(x["flow"], x["curve"]) for x in seq if x["flow"] is not None]
    diff = [f - c for f, c in pairs]
    print(f"{name}: measured flow - curve, {len(pairs)} running hours: mean {st.mean(diff):+.2f} K, sd {st.stdev(diff):.2f}; "
          f"flow mean {st.mean(f for f, _ in pairs):.1f}, curve mean {st.mean(c for _, c in pairs):.1f}")

SUB = 4
def step2(ts, tr, x, heat, p=P2):
    Cs, Cr, K, UA, g0, ap = p
    for _ in range(SUB):
        q = K * (ts - tr)
        ts += (heat - q) / Cs / SUB
        tr += (q + g0 + ap * x["rad"] / 1000 - UA * (tr - x["tout"])) / Cr / SUB
    return ts, tr


def observe(seq, p=P2):
    Cs, Cr, K, UA, g0, ap = p
    ts, slab, prev = None, {}, None
    for x in seq:
        if prev is None or x["h"] - prev["h"] != 3600:
            ts = x["tin"] + x["heat"] / K
        slab[x["h"]] = ts
        ts += (x["heat"] - K * (ts - x["tin"])) / Cs
        prev = x
    return slab


slabs = {**observe(autumn), **observe(winter)}
def hm(q, x, ts):
    h, h0, mx = q
    return min(mx, max(0.0, h * (x["curve"] - ts) + h0)) * (1 - min(x["dhw"], 1))


def descend(f, p, steps, lo):
    cur = f(p)
    for _ in range(300):
        improved = False
        for i in range(len(p)):
            for sgn in (1, -1):
                q = list(p); q[i] = max(q[i] + sgn * steps[i], lo[i])
                s = f(q)
                if s < cur - 1e-6:
                    p, cur, improved = q, s, True
        if not improved:
            steps = [s / 2 for s in steps]
            if max(steps) < 1e-3:
                break
    return p, cur


def heat_rmse(q, seqs):
    e = [(hm(q, x, slabs[x["h"]]) - x["heat"]) for s in seqs for x in s if x["dhw"] < 0.25]
    return (sum(v * v for v in e) / len(e)) ** 0.5


def closed_loop(q, seq, start_hour=21, horizon=24):
    by_h = {x["h"]: x for x in seq}
    errs, hb = collections.defaultdict(list), []
    for x in seq:
        if x["local"].hour != start_hour:
            continue
        ts, tr = slabs[x["h"]], x["tin"]
        for i in range(horizon):
            cur, nxt = by_h.get(x["h"] + 3600 * i), by_h.get(x["h"] + 3600 * (i + 1))
            if cur is None or nxt is None:
                break
            heat = hm(q, cur, ts)
            hb.append(heat - cur["heat"])
            ts, tr = step2(ts, tr, cur, heat)
            errs[i + 1].append(tr - nxt["tin"])
    return {h: ((sum(e * e for e in v) / len(v)) ** 0.5, st.mean(v), len(v)) for h, v in errs.items()}, st.mean(hb)


for label, fit_on in (("autumn only", [autumn]), ("winter only", [winter]), ("both", [autumn, winter])):
    q, e = descend(lambda q: heat_rmse(q, fit_on), [1.0, 0.0, 6.0], [0.5, 0.5, 1.0], [0.0, -5.0, 1.0])
    print(f"\nfit on {label}: heat = clip({q[0]:.3f} * (curve - slab) + {q[1]:+.2f}, 0, {q[2]:.2f}) kW; heat RMSE {e:.2f} kW")
    for name, seq in (("autumn", autumn), ("winter", winter)):
        out, hb = closed_loop(q, seq)
        print(f"  {name} closed loop from 21:00: heat bias {hb:+.2f} kW; room " +
              ", ".join(f"{h}h {r:.2f}/{b:+.2f}" for h, (r, b, n) in sorted(out.items()) if h in (6, 12, 24)), f"(n={out[24][2]})")

# Is the secondary return temperature (circulation never stops) a measurement of the slab?
SEC = json.load(open(sys.argv[8]))
ret = {int(h): v for h, v in SEC["ret"].items()}
for name, seq in (("autumn", autumn), ("winter", winter)):
    pairs = [(slabs[x["h"]], ret[x["h"]], x) for x in seq if x["h"] in ret]
    idle = [(s, r) for s, r, x in pairs if x["heat"] < 0.1 and x["dhw"] == 0]
    run = [(s, r) for s, r, x in pairs if x["heat"] >= 0.1]
    for lab, pp in (("compressor off", idle), ("heating", run)):
        if len(pp) > 10:
            d = [r - s for s, r in pp]
            ms, mr = st.mean(s for s, _ in pp), st.mean(r for _, r in pp)
            cov = sum((s - ms) * (r - mr) for s, r in pp); corr = cov / (sum((s - ms) ** 2 for s, _ in pp) * sum((r - mr) ** 2 for _, r in pp)) ** 0.5
            print(f"{name} {lab}: return - model slab mean {st.mean(d):+.2f} sd {st.stdev(d):.2f}, corr {corr:.2f}, n {len(pp)}")
