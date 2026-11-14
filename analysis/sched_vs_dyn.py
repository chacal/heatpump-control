"""Heating: the owner's schedule (reduced 07-21, normal 21-07) vs price-based choice of the heating
hours. Energy from the 'reduced' hours moves into the 'normal' hours, cheapest first, up to the
compressor's capacity; what doesn't fit stays where it was. Args: r5 fi shift_sim prices_dir split.py"""
import collections, contextlib, io, runpy, sys
r5, fi, sim, pdir, split = sys.argv[1:6]
sys.argv = [split, r5, fi, sim, pdir]
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(split, run_name="lib")
price, energy, days, CAP, base, amplify, replay = g["price"], g["energy"], g["days"], g["CAP"], g["base"], g["amplify"], g["replay"]
PEN = 0.1


def run(hours, e, allowed):
    allowed = set(allowed); out = [h for h in hours if h not in allowed]
    load = {h: e[h] for h in hours}; movable = sum(e[h] for h in out)
    room = 0.0
    for h in sorted(allowed, key=lambda h: price[h]):
        room += CAP["heat"] - e[h]
    moved = min(movable, room / (1 + PEN)); need = moved * (1 + PEN)
    for h in sorted(allowed, key=lambda h: price[h]):
        a = min(CAP["heat"] - load[h], need); load[h] += a; need -= a
    keep = 1 - (moved / movable if movable else 0)
    for h in out: load[h] = e[h] * keep
    return sum(load[h] * price[h] for h in hours) / 100, keep


print("€/yr heating saved vs no schedule (last year's consumption)")
print("prices              your schedule (night 21-07)   price-based (10 cheapest h)   price-based 14 h")
for name, pr in [("last year", base), ("2022–23 prices", replay(3)), ("swings ×1.5", amplify(1.5)), ("swings ×2", amplify(2))]:
    price.clear(); price.update(pr); t = collections.Counter(); left = []
    for day, hours in days.items():
        if not all(h in price for h in hours): continue
        e = {h: energy[h]["heat"] if h in energy else 0.0 for h in hours}
        act = sum(e[h] * price[h] for h in hours) / 100
        night = [h for h in hours if h.hour >= 21 or h.hour < 7]
        c, keep = run(hours, e, night); t["sched"] += act - c
        if keep > 0.05 and sum(e.values()) > 5: left.append(keep)
        t["dyn10"] += act - run(hours, e, sorted(hours, key=lambda h: price[h])[:len(night)])[0]
        t["dyn14"] += act - run(hours, e, sorted(hours, key=lambda h: price[h])[:14])[0]
    print(f"{name:20s}   €{t['sched']:4.0f}                          €{t['dyn10']:4.0f}                        €{t['dyn14']:4.0f}")
print(f"days when the night couldn't hold all heating (last scenario): {len(left)}")
