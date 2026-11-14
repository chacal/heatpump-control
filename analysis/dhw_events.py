"""Hot water: heating runs, big draws, run-outs. Arg: r5.txt"""
import collections, datetime as dt, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
d = collections.defaultdict(dict)
for l in open(sys.argv[1]):
    m, t, v = l.split()
    d[int(t)][m.strip("'")] = float(v)
ts = [t for t in sorted(d) if len(d[t]) == 4 and d[t]["DHWTopTemp"] > 5]
L = lambda t: dt.datetime.fromtimestamp(t, TZ)
T = {t: d[t]["DHWTopTemp"] for t in ts}
dhw = {t: d[t]["Compressor"] > 0 and d[t]["SecondaryCircuitFlowTemp"] >= 40 for t in ts}

# --- heating runs (allow 10 min gaps)
runs, cur = [], None
for t in ts:
    if dhw[t]:
        if cur and t - cur[1] <= 900: cur[1] = t
        else:
            if cur: runs.append(cur)
            cur = [t, t]
if cur: runs.append(cur)
print(f"1. Hot water heating runs: {len(runs)} in {(ts[-1] - ts[0]) / 86400:.0f} days")
hist = collections.Counter(L(a).hour for a, b in runs)
print("   start hour:", " ".join(f"{h:02d}:{hist[h]}" for h in range(24) if hist[h]))
dur = sorted((b - a) / 60 + 5 for a, b in runs)
print(f"   duration min: median {dur[len(dur) // 2]:.0f}, p90 {dur[int(len(dur) * .9)]:.0f}")

# --- run-outs: top temp crosses below 40 from >= 44
outs, armed = [], True
for i, t in enumerate(ts):
    if T[t] >= 44: armed = True
    if armed and T[t] < 40:
        armed = False
        # draw start: walk back to the last local max (≥ 1 h back max)
        j = i
        while j > 0 and ts[i] - ts[j - 1] <= 3 * 3600 and T[ts[j - 1]] >= T[ts[j]] - 0.2: j -= 1
        mn = min(T[u] for u in ts[i:i + 36])  # lowest within 3 h
        nxt = next((a for a, b in runs if a >= ts[j] - 600), None)
        outs.append((t, ts[j], T[ts[j]], mn, nxt))
print(f"\n2. Run-outs (tank top < 40 °C): {len(outs)} events, on {len({L(t).date() for t, *_ in outs})} days")
print("   hour:", " ".join(f"{h:02d}:{c}" for h, c in sorted(collections.Counter(L(t).hour for t, *_ in outs).items())))
print("   weekday:", collections.Counter(L(t).strftime('%a') for t, *_ in outs).most_common())
print("   by month:", sorted(collections.Counter(L(t).strftime('%Y-%m') for t, *_ in outs).items()))
st = sorted(s for _, _, s, _, _ in outs)
print(f"   top temp when that draw began: median {st[len(st) // 2]:.1f}, min {st[0]:.1f}, max {st[-1]:.1f}")
lag = sorted((t - s0) / 60 for t, s0, *_ in outs)
print(f"   minutes from draw start to < 40 °C: median {lag[len(lag) // 2]:.0f}, p25 {lag[len(lag) // 4]:.0f}, p75 {lag[3 * len(lag) // 4]:.0f}")
react = sorted(((n - t) / 60) for t, s0, s, mn, n in outs if n)
print(f"   minutes from < 40 °C to compressor heating water: median {react[len(react) // 2]:.0f}, p75 {react[3 * len(react) // 4]:.0f}")
print("   last 12:")
for t, s0, s, mn, n in outs[-12:]:
    print(f"     {L(t):%Y-%m-%d %a %H:%M} draw from {L(s0):%H:%M} at {s:.1f} °C, low {mn:.1f}, heating {'+%d min' % ((n - t) / 60) if n else '-'}")

# --- runs that were not at the usual hours: triggered by a draw?
usual = {h for h, c in hist.items() if c >= max(hist.values()) * 0.25}
print(f"\n3. Usual start hours (≥25 % of the busiest): {sorted(usual)}")
off = [(a, b) for a, b in runs if L(a).hour not in usual]
trig = sum(1 for a, b in off if any(0 <= a - t <= 3 * 3600 for t, *_ in outs))
print(f"   runs at other hours: {len(off)}; of those within 3 h after a run-out: {trig}")
# --- daily heating at usual vs other hours, by temperature at 06 and 18
print("\n4. Top temp at 06:00 and 21:00 (median by month):")
by = collections.defaultdict(lambda: collections.defaultdict(list))
for t in ts:
    x = L(t)
    if x.minute < 5 and x.hour in (6, 21):
        by[x.strftime('%Y-%m')][x.hour].append(T[t])
for m in sorted(by):
    print(f"   {m}: 06 {sorted(by[m][6])[len(by[m][6]) // 2]:.1f}  21 {sorted(by[m][21])[len(by[m][21]) // 2]:.1f}")

print("\n5. Shape: top temp every 15 min, 3 h before to 1 h after crossing 40 °C")
for t, *_ in outs[-6:]:
    print(f"   {L(t):%m-%d %H:%M}: " + " ".join(f"{T.get(t + k * 900, float('nan')):.0f}" for k in range(-12, 5)))
