"""Cost of hot water runs by time of day; moving evening runs to that night's cheapest hours.
Args: r5.txt fi.json shift_sim.py"""
import collections, datetime as dt, io, contextlib, runpy, sys
r5, fi, sim = sys.argv[1:4]; sys.argv = [sim, r5, fi]
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(sim, run_name="lib")
price, energy = g["price"], g["energy"]
S, E = dt.date(2025, 11, 10), dt.date(2026, 11, 9)
cat = collections.defaultdict(lambda: [0.0, 0.0])
night_save = 0.0
for h, e in energy.items():
    if not (S <= h.date() < E) or e["dhw"] <= 0 or h not in price: continue
    k = "05-07 (schedule)" if 4 <= h.hour < 8 else "19-23 (evening)" if 19 <= h.hour <= 23 else "other"
    cat[k][0] += e["dhw"]; cat[k][1] += e["dhw"] * price[h] / 100
    if k.startswith("19"):
        night = [h.replace(hour=0) + dt.timedelta(days=1, hours=i) for i in range(0, 7)]
        best = min(price[x] for x in night if x in price)
        night_save += e["dhw"] * (price[h] - best * 1.0) / 100
for k, (kwh, eur) in sorted(cat.items()):
    print(f"   {k:18s} {kwh:5.0f} kWh  €{eur:5.1f}  avg {100 * eur / kwh:4.1f} c/kWh")
print(f"   moving every evening reheat to that night's cheapest hour (00-06) would save €{night_save:.1f}/yr")
