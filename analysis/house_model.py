"""House heat loss (UA), heat storage (time constant) and extra heat days (fireplace?).
Args: r5.txt tin.txt tout.txt shift_sim.py fi.json"""
import collections, datetime as dt, runpy, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
r5, tin_f, tout_f, sim, prices = sys.argv[1:6]
sys.argv = [sim, r5, prices]
g = runpy.run_path(sim, run_name="lib")
energy, price = g["energy"], g["price"]
COP_HEAT = 10692 / 2061

H = lambda t: dt.datetime.fromtimestamp(int(float(t)), TZ).replace(minute=0, second=0)
tin = {H(t): float(m) for t, m, *_ in (l.split() for l in open(tin_f))}
tout = {H(int(float(t)) - 3600): float(v) for t, v in (l.split() for l in open(tout_f))}

# compressor heating time per hour (5-min samples)
d = collections.defaultdict(dict)
for line in open(r5):
    m, t, v = line.split()
    d[int(t)][m.strip("'")] = float(v)
run = collections.Counter()
for t, x in d.items():
    if len(x) == 4 and x["Compressor"] > 0:
        run[H(t)] += 5 / 60

# ---- 1. daily balance in the heating season
days = collections.defaultdict(lambda: {"heat": 0.0, "tin": [], "tout": []})
for h in tin:
    if h in tout:
        dd = days[h.date()]
        dd["heat"] += energy[h]["heat"] * COP_HEAT if h in energy else 0.0
        dd["tin"].append(tin[h]); dd["tout"].append(tout[h])
rows = []
for day, v in sorted(days.items()):
    if len(v["tin"]) < 22 or not (day.month in (11, 12, 1, 2, 3)):
        continue
    ti, to = sum(v["tin"]) / len(v["tin"]), sum(v["tout"]) / len(v["tout"])
    rows.append((day, v["heat"], ti, to, ti - to))
n = len(rows)
xs, ys = [r[4] for r in rows], [r[1] for r in rows]
mx, my = sum(xs) / n, sum(ys) / n
b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
a = my - b * mx
r2 = 1 - sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / sum((y - my) ** 2 for y in ys)
print(f"1. Heating-season days {n}: heat/day = {b:.2f} kWh × ΔT {a:+.1f}  (R² {r2:.2f})")
print(f"   UA ≈ {b / 24 * 1000:.0f} W/K; free gains (people, appliances, sun) ≈ {-a:.0f} kWh/day; balance point ΔT {-a / b:.1f} K")
cold = sorted(rows, key=lambda r: r[3])[:5]
print("   coldest days:", [(str(r[0]), round(r[3], 1), round(r[1])) for r in cold])
print(f"   indoor temp (heating season): mean {sum(r[2] for r in rows) / n:.1f}, min {min(r[2] for r in rows):.1f}, max {max(r[2] for r in rows):.1f}")

# ---- 2. cool-down: nights (20-08) Oct-Apr with no compressor run for >= 3 h
print("2. Cool-down episodes (no compressor for ≥3 h, 20:00-08:00, Oct-Apr):")
hours = sorted(h for h in tin if h in tout)
eps, cur = [], []
for h in hours:
    ok = run[h] < 1e-9 and (h.hour >= 20 or h.hour < 8) and h.month in (10, 11, 12, 1, 2, 3, 4)
    if ok and (not cur or h - cur[-1] == dt.timedelta(hours=1)):
        cur.append(h)
    else:
        if len(cur) >= 3: eps.append(cur)
        cur = [h] if ok else []
taus = []
for ep in eps:
    t0, t1 = ep[0], ep[-1]
    drop = tin[t0] - tin[t1]; hrs = len(ep) - 1
    dT = sum(tin[h] - tout[h] for h in ep) / len(ep)
    rate = drop / hrs
    tau = dT / rate if rate > 0.005 else None
    if tau: taus.append(tau)
    print(f"   {t0:%Y-%m-%d %H}h +{hrs}h: {tin[t0]:.2f}→{tin[t1]:.2f} °C ({rate:.3f} K/h), outdoor {sum(tout[h] for h in ep) / len(ep):5.1f}, τ {'%.0f h' % tau if tau else 'n/a'}")
if taus:
    taus.sort(); tau = taus[len(taus) // 2]
    C = tau * b / 24
    print(f"   median τ {tau:.0f} h → heat stored per 1 °C indoor ≈ {C:.0f} kWh (heat) = {C / COP_HEAT:.1f} kWh el")

# ---- 3. extra heat days: pump delivered much less than the model says (Nov-Mar)
print("3. Days with the most heat from elsewhere (model − pump, kWh heat), Nov-Mar:")
res = sorted(((a + b * r[4]) - r[1], r) for r in rows)
for extra, (day, heat, ti, to, dT) in res[::-1][:10]:
    print(f"   {day} {day:%a}: extra {extra:5.0f} kWh  pump {heat:5.0f}  indoor {ti:.1f}  outdoor {to:5.1f}")
