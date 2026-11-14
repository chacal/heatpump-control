"""Joint optimum per day: heating and hot water share one compressor (1 h of compressor time per
hour). Hot water fills the cheapest hours first (more kWh per compressor hour), then heating.
Penalty only on kWh that land in a different hour than they actually ran."""
import sys, collections, runpy
sys.argv = [sys.argv[0]] + sys.argv[1:]
g = runpy.run_path(sys.argv[3], run_name="lib")  # reuse loaders from shift_sim.py
price, energy, days, CAP = g["price"], g["energy"], g["days"], dict(g["CAP"])
import os; CAP["heat"] *= float(os.environ.get("HEATF", "1"))
tot = collections.Counter()
hours_used = []
for day, hours in sorted(days.items()):
    e = {m: {h: (energy[h][m] if h in energy else 0.0) for h in hours} for m in ("heat", "dhw")}
    actual = sum((e["heat"][h] + e["dhw"][h]) * price[h] for h in hours) / 100
    for pen in (0.0, 0.1):
        need = {m: sum(e[m].values()) for m in e}
        time = {h: 1.0 for h in hours}
        load = {m: collections.Counter() for m in e}
        for m in ("dhw", "heat"):
            for h in sorted(hours, key=lambda h: price[h]):
                if need[m] <= 1e-9: break
                k = min(time[h] * CAP[m], need[m])
                load[m][h] += k; need[m] -= k; time[h] -= k / CAP[m]
        moved = sum(max(0, load[m][h] - e[m][h]) for m in e for h in hours)
        avgp = sum(price[h] for h in hours) / len(hours)
        cost = sum((load["heat"][h] + load["dhw"][h]) * price[h] for h in hours) / 100
        # moved kWh need 10 % more, bought at the cheapest prices the day had left (approx.: at the hour's own price)
        cost += pen * sum(max(0, load[m][h] - e[m][h]) * price[h] for m in e for h in hours) / 100
        tot[f"save|{pen}"] += actual - cost
        if pen == 0.1: hours_used.append((day, sum(1 - time[h] for h in hours)))
    tot["actual"] += actual
print(f"actual €{tot['actual']:.0f}; joint optimum saves €{tot['save|0.0']:.1f} (no penalty) / €{tot['save|0.1']:.1f} (10 % on moved kWh)")
w = sorted(hours_used, key=lambda x: -x[1])[:3]
print("most compressor hours needed in a day:", [(str(d), round(h, 1)) for d, h in w])
