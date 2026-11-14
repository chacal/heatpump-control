"""Heating vs hot water savings per price scenario. Args: r5.txt fi.json shift_sim.py prices_dir"""
import collections, contextlib, datetime as dt, io, json, runpy, statistics, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
r5, fi, sim, pdir = sys.argv[1:5]
sys.argv = [sim, r5, fi]
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(sim, run_name="lib")
price, energy, days, CAP, shifted = g["price"], g["energy"], g["days"], g["CAP"], g["shifted_cost"]
base = dict(price)
PEN = 0.1


def load(path):
    p = json.load(open(path)); acc = collections.defaultdict(list)
    for t, v in zip(p["unix_seconds"], p["price"]):
        if v is not None: acc[dt.datetime.fromtimestamp(t, TZ).replace(minute=0)].append(v)
    return {h: sum(v) / len(v) / 10 * 1.255 for h, v in acc.items()}


allp = dict(base)
for y in (2022, 2023, 2024): allp.update(load(f"{pdir}/fi_{y}.json"))


def replay(n):
    off = dt.timedelta(days=364 * n)
    return {h: allp[h - off] for h in base if (h - off) in allp}


def amplify(k, level=1.0):
    byday = collections.defaultdict(list)
    for h, p in base.items(): byday[h.date()].append(p)
    mean = {d: statistics.mean(v) for d, v in byday.items()}
    return {h: max(-1.0, level * (mean[h.date()] + k * (p - mean[h.date()]))) for h, p in base.items()}


def opt_one(hours, e, cap):
    """Per-mode optimum: fill the cheapest hours; 10 % extra only on kWh that moved."""
    need, load = sum(e.values()), collections.Counter()
    for h in sorted(hours, key=lambda h: price[h]):
        k = min(cap, need); load[h] += k; need -= k
        if need <= 1e-9: break
    return sum(load[h] * price[h] + PEN * max(0, load[h] - e[h]) * price[h] for h in hours) / 100


def evaluate(pr):
    price.clear(); price.update(pr)
    t = collections.Counter()
    for day, hours in days.items():
        if not all(h in price for h in hours): continue
        for mode in ("heat", "dhw"):
            e = {h: energy[h][mode] if h in energy else 0.0 for h in hours}
            act = sum(e[h] * price[h] for h in hours) / 100
            t[mode + "_cost"] += act
            t[mode + "_opt"] += act - opt_one(hours, e, CAP[mode])
            if mode == "heat":
                for n in (4, 6):
                    c = shifted(hours, e, CAP[mode], n, PEN)
                    t[f"heat_skip{n}"] += act - (act if c is None else c)
        # hot water, realistic: the morning run (04-08) moves to the cheapest hour 00-16 of the
        # same day; evening reheats (19-24) move to the next night's cheapest hour 00-06
        for h in hours:
            e = energy[h]["dhw"] if h in energy else 0.0
            if e <= 0: continue
            if 4 <= h.hour < 8:
                cand = [x for x in hours if x.hour < 16]
                t["dhw_morning"] += e * (price[h] - min(price[x] for x in cand) * (1 + PEN)) / 100
            elif h.hour >= 19:
                nxt = [h.replace(hour=0) + dt.timedelta(days=1, hours=i) for i in range(7)]
                nxt = [x for x in nxt if x in price]
                if nxt: t["dhw_evening"] += max(0, e * (price[h] - min(price[x] for x in nxt) * (1 + PEN)) / 100)
    return t


print("€/yr, last year's consumption, 10 % COP loss on moved energy")
print("prices                        | HEATING cost  skip4  skip6  optimum | HOT WATER cost  morning run  +evening reheats  optimum")
for name, pr in [("last year (actual)", base), ("2022–23 crisis prices", replay(3)),
                 ("daily swings ×1.5", amplify(1.5)), ("daily swings ×2", amplify(2)),
                 ("swings ×2, level +30 %", amplify(2, 1.3))]:
    t = evaluate(dict(pr))
    print(f"{name:30s}|   €{t['heat_cost']:4.0f}      €{t['heat_skip4']:4.0f}  €{t['heat_skip6']:4.0f}  €{t['heat_opt']:4.0f}"
          f"   |      €{t['dhw_cost']:4.0f}     €{t['dhw_morning']:4.0f}        €{t['dhw_evening']:4.0f}           €{t['dhw_opt']:4.0f}")
