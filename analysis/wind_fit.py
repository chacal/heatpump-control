"""Does wind explain heat need? Args: r5 tin tout shift_sim fi wind.json"""
import collections, contextlib, datetime as dt, io, json, runpy, sys
from zoneinfo import ZoneInfo
import statistics
def solve(A, b):
    n = len(A); M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i])); M[i], M[piv] = M[piv], M[i]
        for r in range(n):
            if r != i:
                f = M[r][i] / M[i][i]; M[r] = [a - f * c for a, c in zip(M[r], M[i])]
    return [M[i][n] / M[i][i] for i in range(n)]
TZ = ZoneInfo("Europe/Helsinki")
r5, tin_f, tout_f, sim, fi, wf = sys.argv[1:7]
sys.argv = [sim, r5, fi]
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(sim, run_name="lib")
energy = g["energy"]; COP = 10692 / 2061
H = lambda t: dt.datetime.fromtimestamp(int(float(t)), TZ).replace(minute=0, second=0)
tin = {H(t): float(m) for t, m, *_ in (l.split() for l in open(tin_f))}
tout = {H(int(float(t)) - 3600): float(v) for t, v in (l.split() for l in open(tout_f))}
w = json.load(open(wf))["hourly"]
wind = {dt.datetime.fromisoformat(t).replace(tzinfo=TZ): (v, s) for t, v, s in zip(w["time"], w["wind_speed_10m"], w["shortwave_radiation"]) if v is not None}
days = collections.defaultdict(lambda: collections.defaultdict(list))
for h in tin:
    if h in tout and h in wind:
        dd = days[h.date()]
        dd["heat"].append(energy[h]["heat"] * COP if h in energy else 0.0)
        dd["dT"].append(tin[h] - tout[h]); dd["wind"].append(wind[h][0]); dd["sun"].append(wind[h][1])
X, y, info = [], [], []
for day, v in sorted(days.items()):
    if len(v["dT"]) < 22 or day.month not in (11, 12, 1, 2, 3): continue
    dT, wd, sun = statistics.mean(v["dT"]), statistics.mean(v["wind"]), statistics.mean(v["sun"])
    X.append((1, dT, wd * dT, sun)); y.append(sum(v["heat"])); info.append((day, dT, wd))
def fit(cols):
    A = [[row[c] for c in cols] for row in X]
    AtA = [[sum(a[i] * a[j] for a in A) for j in range(len(cols))] for i in range(len(cols))]
    Aty = [sum(a[i] * yy for a, yy in zip(A, y)) for i in range(len(cols))]
    c = solve(AtA, Aty); r = [yy - sum(ci * ai for ci, ai in zip(c, a)) for a, yy in zip(A, y)]
    my = statistics.mean(y); ss = sum((yy - my) ** 2 for yy in y)
    return [round(x, 3) for x in c], 1 - sum(x * x for x in r) / ss, (sum(x * x for x in r) / len(y)) ** 0.5
for name, cols in [("ΔT only", [0, 1]), ("ΔT + wind×ΔT", [0, 1, 2]), ("ΔT + wind×ΔT + sun", [0, 1, 2, 3])]:
    c, r2, rmse = fit(cols)
    print(f"{name:22s} R² {r2:.3f}  RMSE {rmse:4.1f} kWh/day  coef {c}")
c, *_ = fit([0, 1, 2, 3])
ws = sorted(i[2] for i in info)
print(f"days {len(y)}; daily mean wind {ws[0]:.1f}–{ws[-1]:.1f} m/s, median {ws[len(ws)//2]:.1f}")
for wd in (2, 4, 6, 8):
    print(f"   wind {wd} m/s: heat per K {c[1] + c[2] * wd:.2f} kWh/day → at ΔT 30 K {30 * (c[1] + c[2] * wd):.0f} kWh/day")
