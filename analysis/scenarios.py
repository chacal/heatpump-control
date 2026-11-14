"""Savings under other price patterns, last year's consumption.
Args: r5.txt fi.json shift_sim.py prices_dir"""
import collections, datetime as dt, json, os, runpy, statistics, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
r5, fi, sim, pdir = sys.argv[1:5]
sys.argv = [sim, r5, fi]
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(sim, run_name="lib")
price, energy, days, CAP, shifted_cost = g["price"], g["energy"], g["days"], g["CAP"], g["shifted_cost"]
base = dict(price)


def load(path):
    p = json.load(open(path)); acc = collections.defaultdict(list)
    for t, v in zip(p["unix_seconds"], p["price"]):
        if v is not None:
            acc[dt.datetime.fromtimestamp(t, TZ).replace(minute=0)].append(v)
    return {h: sum(v) / len(v) / 10 * 1.255 for h, v in acc.items()}


allp = dict(base)
for y in (2022, 2023, 2024):
    allp.update(load(f"{pdir}/fi_{y}.json"))

# ---- 1. intraday spread by autumn (Sep 1 - Nov 13) and winter (Dec-Feb)
def spread(lo, hi):
    byday = collections.defaultdict(list)
    for h, p in allp.items():
        if lo <= h.date() < hi:
            byday[h.date()].append(p)
    rng = [max(v) - min(v) for v in byday.values() if len(v) >= 23]
    mean = [statistics.mean(v) for v in byday.values() if len(v) >= 23]
    return (statistics.mean(mean), statistics.median(rng), sum(r > 20 for r in rng), sum(r > 40 for r in rng), len(rng))
print("1. Daily price spread (max−min within a day), c/kWh incl. VAT")
print("   period              avg price  median spread  days >20c  days >40c")
for y in (2022, 2023, 2024, 2025, 2026):
    m, r, n20, n40, n = spread(dt.date(y, 9, 1), dt.date(y, 11, 14))
    print(f"   autumn {y} (Sep–Nov13) {m:6.1f}      {r:6.1f}        {n20:3d}       {n40:3d}   /{n}")
for y in (2022, 2023, 2024, 2025):
    m, r, n20, n40, n = spread(dt.date(y, 12, 1), dt.date(y + 1, 3, 1))
    print(f"   winter {y}-{(y + 1) % 100} (Dec–Feb) {m:6.1f}      {r:6.1f}        {n20:3d}       {n40:3d}   /{n}")


# ---- 2. savings under a price dict
def joint_opt(hours, pen=0.1):
    e = {m: {h: (energy[h][m] if h in energy else 0.0) for h in hours} for m in ("heat", "dhw")}
    need = {m: sum(e[m].values()) for m in e}; time = {h: 1.0 for h in hours}
    load = {m: collections.Counter() for m in e}
    for m in ("dhw", "heat"):
        for h in sorted(hours, key=lambda h: price[h]):
            if need[m] <= 1e-9: break
            k = min(time[h] * CAP[m], need[m]); load[m][h] += k; need[m] -= k; time[h] -= k / CAP[m]
    cost = sum((load["heat"][h] + load["dhw"][h]) * price[h] for h in hours)
    cost += pen * sum(max(0, load[m][h] - e[m][h]) * price[h] for m in e for h in hours)
    return cost / 100


def evaluate(newprice):
    price.clear(); price.update(newprice)
    tot = collections.Counter()
    for day, hours in days.items():
        if not all(h in price for h in hours): continue
        for mode in ("heat", "dhw"):
            e = {h: energy[h][mode] if h in energy else 0.0 for h in hours}
            act = sum(e[h] * price[h] for h in hours) / 100
            tot["actual"] += act
            for n in (4, 6):
                c = shifted_cost(hours, e, CAP[mode], n, 0.1)
                tot[f"skip{n}"] += act - (act if c is None else c)
        act = sum(((energy[h]["heat"] + energy[h]["dhw"]) if h in energy else 0) * price[h] for h in hours) / 100
        tot["opt"] += act - joint_opt(hours)
    return tot


def replay(n_years):  # same weekday, 52*n weeks earlier
    off = dt.timedelta(days=364 * n_years)
    return {h: allp[h - off] for h in base if (h - off) in allp}


def amplify(k, level=1.0):
    byday = collections.defaultdict(list)
    for h, p in base.items(): byday[h.date()].append(p)
    mean = {d: statistics.mean(v) for d, v in byday.items()}
    return {h: max(-1.0, level * (mean[h.date()] + k * (p - mean[h.date()]))) for h, p in base.items()}


print("\n2. Savings per year (heating + hot water, 10 % COP loss on moved kWh), last year's consumption")
print("   prices                                     pump spot cost   skip 4 h   skip 6 h   optimum")
for name, pr in [("last year (actual)", dict(base)),
                 ("replay prices 1 y earlier (2024–25)", replay(1)),
                 ("replay prices 2 y earlier (2023–24)", replay(2)),
                 ("replay prices 3 y earlier (2022–23)", replay(3)),
                 ("last year, intraday swings ×1.5", amplify(1.5)),
                 ("last year, intraday swings ×2", amplify(2)),
                 ("last year, intraday swings ×3", amplify(3)),
                 ("swings ×2 and level +30 %", amplify(2, 1.3))]:
    t = evaluate(pr)
    print(f"   {name:42s}  €{t['actual']:5.0f}        €{t['skip4']:4.0f}      €{t['skip6']:4.0f}     €{t['opt']:4.0f}")
