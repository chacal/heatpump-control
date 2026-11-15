"""Calibrate the house model on Sep-Nov 2026 data (DEV-1, 2026-11-15).

Args: r5.txt rooms_f.csv recent_f.csv tout.txt rad.txt fit.json (output: the two-node parameters)
Fits: one node on 6-h and 24-h energy balances, then two nodes (slab and room) on 12-h-ahead
room predictions driven by the measured heat, from each 07:00 and 21:00 since 2026-10-15.
Heat delivered is the compressor speed in heating mode (on, secondary flow < 40 °C), scaled so
its 52-week sum matches the heating-heat register (10,692 kWh on 2026-11-09).
"""
import collections, datetime as dt, statistics as st, sys
from zoneinfo import ZoneInfo

UTC, TZ = dt.UTC, ZoneInfo("Europe/Helsinki")
BED = ("S202", "S203", "S204", "S211")
r5, rooms_old, rooms_new, tout_f, rad_f = sys.argv[1:6]

# 5-min pump samples
samples = collections.defaultdict(dict)
for line in open(r5):
    n, t, v = line.split()
    samples[int(t)][n.strip("'")] = float(v)
heat_pct = collections.defaultdict(float)  # hour -> sum of speed% * h in heating mode
dhw_h = collections.defaultdict(float)
n5 = collections.Counter()
reg_lo, reg_hi = dt.datetime(2025, 11, 10, tzinfo=UTC).timestamp(), dt.datetime(2026, 11, 9, tzinfo=UTC).timestamp()
year_pct = 0.0
for t, d in samples.items():
    if "Compressor" not in d:
        continue
    h = t - t % 3600
    n5[h] += 1
    on = d["Compressor"] > 0
    flow = d.get("SecondaryCircuitFlowTemp", 0)
    if on and flow < 40:
        heat_pct[h] += d.get("CompressorSpeed", 0) * 5 / 60
        if reg_lo <= t < reg_hi:
            year_pct += d.get("CompressorSpeed", 0) * 5 / 60
    elif on:
        dhw_h[h] += 5 / 60
k = 10692 / year_pct  # kWh of heat per %-hour
print(f"k = {k:.4f} kWh heat per %-h (35 % for an hour = {35 * k:.2f} kWh)")

# Hourly bedroom median
rooms = collections.defaultdict(dict)
for path, cut_lo, cut_hi in ((rooms_old, None, dt.datetime(2026, 10, 1, tzinfo=UTC)), (rooms_new, dt.datetime(2026, 10, 1, tzinfo=UTC), None)):
    sec = "#ROOMS" if path == rooms_old else None
    for line in open(path):
        if line.startswith("#"):
            sec = line.strip(); continue
        if line.startswith("h,") or sec != "#ROOMS":
            continue
        h, r, t, n = line.strip().split(",")
        at = dt.datetime.fromisoformat(h).replace(tzinfo=UTC)
        if (cut_lo and at < cut_lo) or (cut_hi and at >= cut_hi) or r not in BED or int(n) < 20 or not 15 <= float(t) <= 35:
            continue
        rooms[int(at.timestamp())][r] = float(t)
tin = {h: st.median(v.values()) for h, v in rooms.items() if len(v) >= 2}
tout = {int(float(l.split()[0])): float(l.split()[1]) for l in open(tout_f)}
rad = {int(l.split()[0]): float(l.split()[1]) for l in open(rad_f)}

start, end = dt.datetime(2026, 9, 1, tzinfo=UTC), dt.datetime(2026, 11, 14, tzinfo=UTC)
hours = []
h = int(start.timestamp())
while h < end.timestamp():
    if h in tin and h in tout and h in rad and n5[h] >= 10:
        hours.append(dict(h=h, local=dt.datetime.fromtimestamp(h, TZ), tin=tin[h], tout=tout[h], rad=rad[h],
                          heat=k * heat_pct[h] * 12 / n5[h], dhw=dhw_h[h] * 12 / n5[h]))
    h += 3600
by_h = {x["h"]: x for x in hours}
print(f"{len(hours)} usable hours {hours[0]['local']:%m-%d} .. {hours[-1]['local']:%m-%d}")

# When did the schedule start? Heating share 07-12 local per week.
print("\nheating share of hours 07-12 / 21-07, per week:")
weeks = collections.defaultdict(lambda: [[], []])
for x in hours:
    wk = x["local"].strftime("%m-%d") if x["local"].weekday() == 0 else None
    key = (x["local"] - dt.timedelta(days=x["local"].weekday())).strftime("%m-%d")
    if 7 <= x["local"].hour < 12:
        weeks[key][0].append(x["heat"] > 0.2)
    elif x["local"].hour >= 21 or x["local"].hour < 7:
        weeks[key][1].append(x["heat"] > 0.2)
for wk, (day, night) in sorted(weeks.items()):
    print(f"  {wk}: day {sum(day) / max(len(day), 1):4.0%}  night {sum(night) / max(len(night), 1):4.0%}")


def solve(rows, ys):
    """Least squares by normal equations (small n)."""
    n = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) for j in range(n)] + [sum(r[i] * y for r, y in zip(rows, ys))] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        for r in range(n):
            if r != c:
                f = a[r][c] / a[c][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [a[i][n] / a[i][i] for i in range(n)]


AWAY = (dt.datetime(2026, 10, 8, 12, tzinfo=TZ), dt.datetime(2026, 10, 14, 12, tzinfo=TZ))
def usable(x):
    return not AWAY[0] <= x["local"] < AWAY[1]

# House model: sum(heat) = C*dT + UA*sum(tin - tout) - g0*hours - a*sum(G)/1000, 6-h blocks.
rows, ys = [], []
for x in hours:
    blk = [by_h.get(x["h"] + 3600 * i) for i in range(7)]
    if any(b is None or not usable(b) for b in blk) or x["local"].hour % 6:
        continue
    rows.append([blk[6]["tin"] - blk[0]["tin"], sum(b["tin"] - b["tout"] for b in blk[:6]), -6.0, -sum(b["rad"] for b in blk[:6]) / 1000])
    ys.append(sum(b["heat"] for b in blk[:6]))
C, UA, g0, ap = solve(rows, ys)
pred = [sum(c * r for c, r in zip((C, UA, g0, ap), row)) for row in rows]
ss = sum((y - p) ** 2 for y, p in zip(ys, pred)); tot = sum((y - st.mean(ys)) ** 2 for y in ys)
print(f"\nhouse model, {len(rows)} 6-h blocks: C {C:.1f} kWh/K  UA {UA:.3f} kW/K  gains {g0:.2f} kW  aperture {ap:.1f} m²  R² {1 - ss / tot:.2f}")
print("  prior:                C 16.5         UA 0.141        gains 0.79       aperture 5.4")

# 24-h blocks from 07:00 (end of the night's heating) - the slab's lag washes out over a day.
for start_hour in (7, 21):
    rows, ys = [], []
    for x in hours:
        if x["local"].hour != start_hour:
            continue
        blk = [by_h.get(x["h"] + 3600 * i) for i in range(25)]
        if any(b is None or not usable(b) for b in blk) or x["local"] < dt.datetime(2026, 10, 15, tzinfo=TZ):
            continue
        rows.append([blk[24]["tin"] - blk[0]["tin"], sum(b["tin"] - b["tout"] for b in blk[:24]), -24.0, -sum(b["rad"] for b in blk[:24]) / 1000])
        ys.append(sum(b["heat"] for b in blk[:24]))
    C, UA, g0, ap = solve(rows, ys)
    pred = [sum(c * r for c, r in zip((C, UA, g0, ap), row)) for row in rows]
    ss = sum((y - p) ** 2 for y, p in zip(ys, pred)); tot = sum((y - st.mean(ys)) ** 2 for y in ys)
    print(f"24-h blocks from {start_hour:02d}, {len(rows)} days since 10-15: C {C:.1f}  UA {UA:.3f}  gains {g0:.2f}  aperture {ap:.1f}  R² {1 - ss / tot:.2f}  mean heat {st.mean(ys):.1f} kWh/day")
    # Fixed C and aperture from the year fit; UA and gains only.
    rows2 = [[r[1], r[2]] for r in rows]; ys2 = [y - 16.5 * r[0] + 5.4 * (-r[3]) for y, r in zip(ys, rows)]
    UA2, g02 = solve(rows2, ys2)
    print(f"   with C 16.5, aperture 5.4 fixed: UA {UA2:.3f}  gains {g02:.2f}")

# Two nodes: heat -> slab -(K)-> room -(UA)-> outdoors; gains and sun into the room.
FIT_FROM = dt.datetime(2026, 10, 15, tzinfo=TZ)
seq = [x for x in hours if x["local"] >= FIT_FROM]
SUB = 4


def step2(ts, tr, x, p, heat):
    Cs, Cr, K, UA, g0, ap = p
    for _ in range(SUB):
        q = K * (ts - tr)
        ts += (heat - q) / Cs / SUB
        tr += (q + g0 + ap * x["rad"] / 1000 - UA * (tr - x["tout"])) / Cr / SUB
    return ts, tr


def observe(p):
    """Slab temperature per hour, with the room pinned to the measurement."""
    Cs, Cr, K, UA, g0, ap = p
    ts, slab, prev = None, {}, None
    for x in seq:
        if prev is None or x["h"] - prev["h"] != 3600:
            ts = x["tin"] + (x["heat"] / K if K else 0)
        slab[x["h"]] = ts
        q = K * (ts - x["tin"])
        ts += (x["heat"] - q) / Cs
        prev = x
    return slab


def score(p, horizon=12, starts=(7, 21), detail=False):
    slab = observe(p)
    errs = collections.defaultdict(list)
    for x in seq:
        if x["local"].hour not in starts or not usable(x):
            continue
        ts, tr = slab[x["h"]], x["tin"]
        for i in range(horizon):
            cur = by_h.get(x["h"] + 3600 * i)
            nxt = by_h.get(x["h"] + 3600 * (i + 1))
            if cur is None or nxt is None:
                break
            ts, tr = step2(ts, tr, cur, p, cur["heat"])
            errs[i + 1].append(tr - nxt["tin"])
    allerr = [e for v in errs.values() for e in v]
    rmse = (sum(e * e for e in allerr) / len(allerr)) ** 0.5
    if detail:
        return rmse, {h: ((sum(e * e for e in v) / len(v)) ** 0.5, st.mean(v)) for h, v in sorted(errs.items())}
    return rmse


# One node (today's model) as the reference: Cs tiny, K huge is awkward, so simulate it directly.
def score1(C, UA, g0, ap, horizon=12):
    errs = []
    for x in seq:
        if x["local"].hour not in (7, 21) or not usable(x):
            continue
        tr = x["tin"]
        for i in range(horizon):
            cur, nxt = by_h.get(x["h"] + 3600 * i), by_h.get(x["h"] + 3600 * (i + 1))
            if cur is None or nxt is None:
                break
            tr += (cur["heat"] + g0 + ap * cur["rad"] / 1000 - UA * (tr - cur["tout"])) / C
            errs.append(tr - nxt["tin"])
    return (sum(e * e for e in errs) / len(errs)) ** 0.5


print(f"\none node, prior params, 12-h-ahead RMSE {score1(16.5, 0.141, 0.79, 5.4):.3f} K")
best = (None, [10.0, 3.0, 1.0, 0.15, 0.9, 5.4])
steps = [3.0, 1.0, 0.3, 0.02, 0.15, 2.0]
p = list(best[1]); cur = score(p)
for it in range(60):
    improved = False
    for i in range(len(p)):
        for sgn in (1, -1):
            q = list(p); q[i] = max(q[i] + sgn * steps[i], 0.05 if i < 4 else 0.0)
            s = score(q)
            if s < cur - 1e-5:
                p, cur, improved = q, s, True
    if not improved:
        steps = [s / 2 for s in steps]
        if max(steps) < 0.01:
            break
rmse, det = score(p, detail=True)
print(f"two nodes fitted: Cs {p[0]:.1f} kWh/K  Cr {p[1]:.1f}  K {p[2]:.2f} kW/K  UA {p[3]:.3f}  gains {p[4]:.2f}  aperture {p[5]:.1f}  RMSE {rmse:.3f} K")
print("  by horizon (RMSE, bias):", ", ".join(f"{h}h {r:.2f}/{b:+.2f}" for h, (r, b) in det.items() if h in (1, 3, 6, 9, 12)))


def descend(f, p, steps, lo):
    cur = f(p)
    for _ in range(200):
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

p1, s1 = descend(lambda q: score1(*q), [16.5, 0.141, 0.79, 5.4], [3, 0.02, 0.2, 2], [1, 0.05, 0, 0])
print(f"one node refitted: C {p1[0]:.1f}  UA {p1[1]:.3f}  gains {p1[2]:.2f}  aperture {p1[3]:.1f}  RMSE {s1:.3f} K")
import json
json.dump({"p2": p}, open(sys.argv[6], "w"))
