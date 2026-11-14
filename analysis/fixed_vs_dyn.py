"""Heating: the owner's fixed schedule (off 07-12, 16-19) vs price-based skipping of the same number
of hours. Args: r5 fi shift_sim prices_dir split.py"""
import collections, contextlib, io, runpy, sys
r5, fi, sim, pdir, split = sys.argv[1:6]
sys.argv = [split, r5, fi, sim, pdir]
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(split, run_name="lib")
price, energy, days, CAP, base, amplify, replay = g["price"], g["energy"], g["days"], g["CAP"], g["base"], g["amplify"], g["replay"]
PEN = 0.1
def cost(hours, e, skip):
    need = sum(e[h] for h in skip) * (1 + PEN); load = {h: (0.0 if h in skip else e[h]) for h in hours}
    for h in sorted((h for h in hours if h not in skip), key=lambda h: price[h]):
        a = min(CAP["heat"] - load[h], need)
        if a > 0: load[h] += a; need -= a
    if need > 1e-6: return None
    return sum(load[h] * price[h] for h in hours) / 100
print("€/yr heating, last year's consumption      fixed 07-12+16-19   price-based 8 h   both: fixed + price-based 4 h more")
for name, pr in [("last year", base), ("2022–23 prices", replay(3)), ("swings ×1.5", amplify(1.5)), ("swings ×2", amplify(2))]:
    price.clear(); price.update(pr); t = collections.Counter(); infeasible = 0
    for day, hours in days.items():
        if not all(h in price for h in hours): continue
        e = {h: energy[h]["heat"] if h in energy else 0.0 for h in hours}
        act = sum(e[h] * price[h] for h in hours) / 100
        fixed = [h for h in hours if 7 <= h.hour < 12 or 16 <= h.hour < 19]
        dyn = sorted(hours, key=lambda h: -price[h])[:8]
        rest = [h for h in sorted(hours, key=lambda h: -price[h]) if h not in fixed][:4]
        for k, sk in (("fixed", fixed), ("dyn", dyn), ("both", fixed + rest)):
            c = cost(hours, e, sk)
            if c is None: infeasible += 1; c = act
            t[k] += act - c
    print(f"{name:42s}  €{t['fixed']:4.0f}               €{t['dyn']:4.0f}             €{t['both']:4.0f}    (days that didn't fit: {infeasible})")
