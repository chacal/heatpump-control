"""Backtest: move heat pump energy to cheaper hours within the same day.
Args: r5.txt (5-min heat pump data), fi.json (Energy-Charts FI prices)."""
import collections, datetime as dt, json, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
KH, KD = 2060.9 / 119786, 838.1 / 23080          # kWh per speed%-hour (calibrated to registers)
CAP = {"heat": 1.72, "dhw": 3.63}                 # kWh/h at 100 % speed
VAT = 1.255
START, END = dt.date(2025, 11, 10), dt.date(2026, 11, 9)   # the registers' 52 weeks

# --- prices: hourly mean, c/kWh incl. VAT
p = json.load(open(sys.argv[2]))
acc = collections.defaultdict(list)
for t, v in zip(p["unix_seconds"], p["price"]):
    if v is not None:
        acc[dt.datetime.fromtimestamp(t, TZ).replace(minute=0)].append(v)
price = {h: sum(v) / len(v) / 10 * VAT for h, v in acc.items()}

# --- energy per hour and mode
d = collections.defaultdict(dict)
for line in open(sys.argv[1]):
    m, t, v = line.split()
    d[int(t)][m.strip("'")] = float(v)
energy = collections.defaultdict(lambda: {"heat": 0.0, "dhw": 0.0})
for t, x in d.items():
    if len(x) == 4 and x["Compressor"] > 0:
        mode = "dhw" if x["SecondaryCircuitFlowTemp"] >= 40 else "heat"
        h = dt.datetime.fromtimestamp(t, TZ).replace(minute=0)
        energy[h][mode] += x["CompressorSpeed"] * 5 / 60 * (KH if mode == "heat" else KD)

days = collections.defaultdict(list)
for h in price:
    if START <= h.date() < END:
        days[h.date()].append(h)


def shifted_cost(hours, e, cap, skip_n, penalty):
    """Skip the skip_n priciest hours (None = optimal: fill cheapest hours), move energy to the
    cheapest hours of the day with room left; moved kWh grow by `penalty`."""
    total = sum(e[h] for h in hours)
    if skip_n is None:
        need, load = total * (1 + penalty), {h: 0.0 for h in hours}
    else:
        top = sorted(hours, key=lambda h: -price[h])[:skip_n]
        need = sum(e[h] for h in top) * (1 + penalty)
        load = {h: (0.0 if h in top else e[h]) for h in hours}
        hours = [h for h in hours if h not in top]
    for h in sorted(hours, key=lambda h: price[h]):
        add = min(cap - load[h], need)
        if add > 0:
            load[h] += add
            need -= add
    if need > 1e-9:  # no room: leave it where it was (rare)
        return None
    return sum(load[h] * price[h] for h in load) / 100


SCEN = [("skip 2 h", 2), ("skip 4 h", 4), ("skip 6 h", 6), ("optimal", None)]
res = collections.defaultdict(lambda: collections.Counter())
daily = []
for day, hours in sorted(days.items()):
    for mode in ("heat", "dhw"):
        e = {h: energy[h][mode] if h in energy else 0.0 for h in hours}
        kwh = sum(e.values())
        actual = sum(e[h] * price[h] for h in hours) / 100
        mo = day.strftime("%Y-%m")
        res[(mo, mode)]["kwh"] += kwh
        res[(mo, mode)]["actual"] += actual
        for name, n in SCEN:
            for pen in (0.0, 0.1):
                c = shifted_cost(hours, e, CAP[mode], n, pen)
                res[(mo, mode)][f"{name}|{pen}"] += actual - (actual if c is None else c)
        if mode == "heat":
            c4 = shifted_cost(hours, e, CAP[mode], 4, 0.1)
            daily.append((actual - c4, day, kwh, max(price[h] for h in hours), min(price[h] for h in hours)))

months = sorted({m for m, _ in res})
for mode in ("heat", "dhw"):
    print(f"\n== {mode}: savings € per month (penalty 0 % / 10 % on moved kWh)")
    print("month    kWh  cost€  " + "  ".join(f"{n:>13}" for n, _ in SCEN))
    tot = collections.Counter()
    for m in months:
        r = res[(m, mode)]
        tot.update(r)
        print(f"{m} {r['kwh']:5.0f} {r['actual']:6.1f}  " + "  ".join(
            f"{r[f'{n}|0.0']:5.1f} /{r[f'{n}|0.1']:6.1f}" for n, _ in SCEN))
    print(f"TOTAL   {tot['kwh']:5.0f} {tot['actual']:6.1f}  " + "  ".join(
        f"{tot[f'{n}|0.0']:5.1f} /{tot[f'{n}|0.1']:6.1f}" for n, _ in SCEN))
    print(f"avg price paid {100 * tot['actual'] / tot['kwh']:.2f} c/kWh (spot incl. VAT)")

daily.sort(reverse=True)
s = sum(x[0] for x in daily)
print(f"\nheating, skip 4 h, 10 % penalty: total {s:.1f} €; top 10 days give {sum(x[0] for x in daily[:10]):.1f} €, top 30 {sum(x[0] for x in daily[:30]):.1f} €")
for sv, day, kwh, mx, mn in daily[:10]:
    print(f"  {day} saved {sv:5.2f} €  heat {kwh:4.1f} kWh  price max {mx:6.1f} min {mn:5.1f} c/kWh")
