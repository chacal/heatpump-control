"""Refit the two-node house model on 2026-10-15..11-17, with household electricity as gains (DEV-1).

Args: dir, holding: r5_{Compressor,CompressorSpeed,SecondaryCircuitFlowTemp,TempOutdoor}.json (5-min
Prometheus range queries of heatpump_*), {Compressor,CompressorSpeed,SecondaryCircuitFlowTemp,
ElecPowerHeating,HeatPowerHeating}.json (1-min, since the power reads exist), rooms_h.txt (hourly
bedroom means from the sensor archive: `<UTC hour> <sensor> <mean> <n>`), rad.txt
(fetch_radiation.py), i100_min.txt (the whole-house meter: `<UTC minute> <Wh counter>`).
Heat in model units (speed x 0.0892 kWh/%-h in heating mode, flow < 40 °C), as in calibrate_house.py.
Household electricity = I100 (whole house) - the pump's electricity (speed x W/% per mode, from the
live ElecPowerHeating, which covers both modes).
"""
import collections, datetime as dt, json, statistics as st, sys
from zoneinfo import ZoneInfo

D = sys.argv[1]
UTC, TZ = dt.UTC, ZoneInfo("Europe/Helsinki")
K = 0.0892


def prom(name):
    return {int(t): float(v) for t, v in json.load(open(f"{D}/{name}.json"))["data"]["result"][0]["values"]}


# Pump electricity per % speed, per mode, from the live readings (1-min).
c1, sp1, fl1, e1, h1 = (prom(n) for n in ("Compressor", "CompressorSpeed", "SecondaryCircuitFlowTemp", "ElecPowerHeating", "HeatPowerHeating"))
acc = {m: [0.0, 0.0, 0.0] for m in ("heat", "dhw")}
for t, e in e1.items():
    if c1.get(t, 0) > 0 and sp1.get(t, 0) > 0:
        m = "heat" if fl1.get(t, 0) < 40 else "dhw"
        acc[m][0] += e; acc[m][1] += sp1[t]; acc[m][2] += h1[t]
W_PER_PCT = {m: a[0] / a[1] for m, a in acc.items()}
print("pump electricity W per %:", {m: round(v, 1) for m, v in W_PER_PCT.items()},
      " heat/el:", {m: round(a[2] / a[0], 2) for m, a in acc.items()})
print(f"electricity per model-unit heat (heating): {W_PER_PCT['heat'] / 1000 / K:.3f}  (model: 1/cop = {1 / 5.2:.3f})")

c5, sp5, fl5, to5 = (prom(f"r5_{n}") for n in ("Compressor", "CompressorSpeed", "SecondaryCircuitFlowTemp", "TempOutdoor"))
heat, pel, n5, tout = collections.defaultdict(float), collections.defaultdict(float), collections.Counter(), collections.defaultdict(list)
for t in c5:
    h = t - t % 3600
    n5[h] += 1
    if t in to5:
        tout[h].append(to5[t])
    if c5[t] > 0:
        sp = sp5.get(t, 0)
        if fl5.get(t, 0) < 40:
            heat[h] += sp * K / 12
            pel[h] += sp * W_PER_PCT["heat"] / 1000 / 12
        else:
            pel[h] += sp * W_PER_PCT["dhw"] / 1000 / 12

i100 = {}
for line in open(f"{D}/i100_min.txt"):
    ts, wh = line.split()
    i100[int(dt.datetime.fromisoformat(ts).replace(tzinfo=UTC).timestamp())] = int(wh)
house_kw = {}
for h in {t - t % 3600 for t in i100}:
    a, b = i100.get(h), i100.get(h + 3600)
    if a is not None and b is not None and 0 <= b - a < 20000:
        house_kw[h] = (b - a) / 1000

rooms = collections.defaultdict(dict)
for line in open(f"{D}/rooms_h.txt"):
    hh, r, t, n = line.split()
    if int(n) >= 20:
        rooms[int(dt.datetime.fromisoformat(hh).replace(tzinfo=UTC).timestamp())][r] = float(t)
tin = {h: st.median(v.values()) for h, v in rooms.items() if len(v) >= 2}
rad = {int(l.split()[0]): float(l.split()[1]) for l in open(f"{D}/rad.txt")}

start, end = dt.datetime(2026, 10, 14, tzinfo=UTC), dt.datetime(2026, 11, 17, 17, tzinfo=UTC)
by_h = {}
h = int(start.timestamp())
while h < end.timestamp():
    if h in tin and tout.get(h) and h in rad and n5[h] >= 10 and h in house_kw:
        s = 12 / n5[h]
        by_h[h] = dict(h=h, local=dt.datetime.fromtimestamp(h, TZ), tin=tin[h], tout=st.mean(tout[h]), rad=rad[h],
                       heat=heat[h] * s, other=max(house_kw[h] - pel[h] * s, 0.0))
    h += 3600
seq = sorted(by_h.values(), key=lambda x: x["h"])
oth = [x["other"] for x in seq]
print(f"{len(seq)} hours {seq[0]['local']:%m-%d %H} .. {seq[-1]['local']:%m-%d %H}; household el mean {st.mean(oth):.2f} kW, "
      f"p10 {sorted(oth)[len(oth) // 10]:.2f}, p90 {sorted(oth)[len(oth) * 9 // 10]:.2f}")
FIT_TO = dt.datetime(2026, 11, 10, tzinfo=TZ)  # fit before, test after
SUB = 4


def run(p, x, ts, tr):
    Cs, Cr, Kr, UA, g0, ap, f = p
    gains = g0 + ap * x["rad"] / 1000 + f * x["other"]
    for _ in range(SUB):
        q = Kr * (ts - tr)
        ts += (x["heat"] - q) / Cs / SUB
        tr += (q + gains - UA * (tr - x["tout"])) / Cr / SUB
    return ts, tr


def observe(p):
    Cs, Kr = p[0], p[2]
    ts, slab, prev = None, {}, None
    for x in seq:
        if prev is None or x["h"] - prev["h"] != 3600:
            ts = x["tin"] + x["heat"] / Kr
        slab[x["h"]] = ts
        ts += (x["heat"] - Kr * (ts - x["tin"])) / Cs
        prev = x
    return slab


def score(p, period, horizon=16, starts=(1, 7, 13, 19), detail=False):
    slab = observe(p)
    errs = collections.defaultdict(list)
    for x in seq:
        if x["local"].hour not in starts or not period(x["local"]):
            continue
        ts, tr = slab[x["h"]], x["tin"]
        for i in range(horizon):
            cur, nxt = by_h.get(x["h"] + 3600 * i), by_h.get(x["h"] + 3600 * (i + 1))
            if cur is None or nxt is None:
                break
            ts, tr = run(p, cur, ts, tr)
            errs[i + 1].append(tr - nxt["tin"])
    allerr = [e for v in errs.values() for e in v]
    rmse = (sum(e * e for e in allerr) / len(allerr)) ** 0.5
    if detail:
        return rmse, {k: ((sum(e * e for e in v) / len(v)) ** 0.5, st.mean(v), len(v)) for k, v in errs.items()}
    return rmse


def descend(p, steps, lo, free, period):
    cur = score(p, period)
    for _ in range(60):
        improved = False
        for i in free:
            for sgn in (1, -1):
                q = list(p); q[i] = max(q[i] + sgn * steps[i], lo[i])
                s = score(q, period)
                if s < cur - 1e-6:
                    p, cur, improved = q, s, True
        if not improved:
            steps = [s / 2 for s in steps]
            if max(steps[i] for i in free) < 5e-3:
                break
    return p


fit = lambda t: dt.datetime(2026, 10, 15, tzinfo=TZ) <= t < FIT_TO
test = lambda t: t >= FIT_TO
allp = lambda t: t >= dt.datetime(2026, 10, 15, tzinfo=TZ)
NOW = [12.2, 7.9, 1.13, 0.150, 0.83, 8.5, 0.0]
steps, lo = [2.0, 1.0, 0.2, 0.02, 0.1, 2.0, 0.1], [1, 1, 0.1, 0.05, 0, 0, 0]


def report(name, p):
    out = [name]
    for lab, per in (("fit", fit), ("test", test), ("all", allp)):
        r, det = score(p, per, detail=True)
        out.append(f"{lab} {r:.2f}" + " [" + " ".join(f"{k}h {det[k][0]:.2f}/{det[k][1]:+.2f}" for k in (4, 8, 12, 16) if k in det) + "]")
    print("  " + "  ".join(out))


print("\nRMSE K, [horizon RMSE/bias], fit 10-15..11-09, test 11-10..11-17")
report("now (no refit)         ", NOW)
pB = descend(list(NOW), steps, lo, range(6), fit)
report("refit, no household el ", pB)
pC = descend(list(NOW[:6]) + [0.5], steps, lo, range(7), fit)
report("refit with household el", pC)
pD = descend(list(NOW[:3]) + [0.150, 0.3, 8.5, 0.5], steps, lo, (3, 4, 6), fit)
report("UA, gains, f only      ", pD)
for n, p in (("B", pB), ("C", pC), ("D", pD)):
    print(f"  {n}: Cs {p[0]:.1f} Cr {p[1]:.1f} K {p[2]:.2f} UA {p[3]:.3f} g0 {p[4]:.2f} ap {p[5]:.1f} f {p[6]:.2f}")
json.dump({"B": pB, "C": pC, "D": pD}, open(f"{D}/fit.json", "w"))
