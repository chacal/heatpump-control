"""Which rooms to steer by. Args: rooms.csv recent.csv tin.txt"""
import collections, datetime as dt, statistics as st, sys
from zoneinfo import ZoneInfo

UTC, TZ = dt.timezone.utc, ZoneInfo("Europe/Helsinki")
NAMES = {"S202": "Bedroom A", "S203": "Bedroom B", "S204": "Bedroom C", "S211": "Master", "S205": "Living"}
BED = ["S202", "S203", "S204", "S211"]
temp = collections.defaultdict(dict)  # local hour -> room -> temp
cut = dt.datetime(2026, 10, 16, 19, tzinfo=UTC)


def add(h, room, t):
    temp[h.astimezone(TZ)][room] = t


for line in open(sys.argv[1]).readlines()[1:]:
    h, r, t, n = line.strip().split(",")
    h = dt.datetime.fromisoformat(h).replace(tzinfo=UTC)
    if h < cut and int(n) >= 20: add(h, r, float(t))
sec = None
for line in open(sys.argv[2]):
    if line.startswith("#"): sec = line.strip(); continue
    if sec == "#ROOMS":
        h, r, t, n = line.strip().split(",")
        h = dt.datetime.fromisoformat(h).replace(tzinfo=UTC)
        if h >= cut and int(n) >= 20: add(h, r, float(t))
ha = {dt.datetime.fromtimestamp(int(float(l.split()[0])), TZ): float(l.split()[1]) for l in open(sys.argv[3])}

hours = sorted(temp)
print(f"hours {len(hours)}: {hours[0]:%Y-%m-%d} .. {hours[-1]:%Y-%m-%d}")
print("1. Coverage (hours with data):", {NAMES[r]: sum(r in temp[h] for h in hours) for r in NAMES})

def season(h, s):
    return {"winter": h.month in (12, 1, 2) and h.year in (2025, 2026),
            "autumn26": h.year == 2026 and h.month in (10, 11)}[s]

for s, label in (("winter", "Dec 2025–Feb 2026 (no schedule)"), ("autumn26", "Oct–Nov 2026 (schedule)")):
    hs = [h for h in hours if season(h, s) and all(r in temp[h] for r in NAMES)]
    print(f"\n2. {label}: {len(hs)} hours with all rooms")
    for r in NAMES:
        v = [temp[h][r] for h in hs]
        print(f"   {NAMES[r]:7s} mean {st.mean(v):5.2f}  p5 {sorted(v)[len(v) // 20]:5.2f}  p95 {sorted(v)[19 * len(v) // 20]:5.2f}  sd {st.pstdev(v):.2f}")
    bed = [st.mean(temp[h][r] for r in BED) for h in hs]
    bmin = [min(temp[h][r] for r in BED) for h in hs]
    hav = [ha[h] for h in hs if h in ha]
    print(f"   bedroom avg mean {st.mean(bed):5.2f} sd {st.pstdev(bed):.2f}; coldest bedroom mean {st.mean(bmin):5.2f}; HA avg_inside mean {st.mean(hav):5.2f} sd {st.pstdev(hav):.2f}")
    # which bedroom is coldest how often
    c = collections.Counter(min(BED, key=lambda r: temp[h][r]) for h in hs)
    print("   coldest bedroom share:", {NAMES[r]: f"{100 * n / len(hs):.0f}%" for r, n in c.most_common()})
    # daily profile
    prof = collections.defaultdict(list)
    for h, b in zip(hs, bed): prof[h.hour].append((b, temp[h]["S205"]))
    print("   hour: bedroom avg / living  " + "  ".join(f"{k:02d}:{st.mean(x[0] for x in prof[k]):.1f}/{st.mean(x[1] for x in prof[k]):.1f}" for k in range(0, 24, 3)))

# 3. Fireplace: living room rises ≥ 1.0 °C within 3 h, more than bedrooms by ≥ 0.8
print("\n3. Fireplace signature (living room +≥1.0 °C in 3 h, ≥0.8 more than bedrooms):")
ev = []
for i, h in enumerate(hours[:-3]):
    h3 = h + dt.timedelta(hours=3)
    if h3 not in temp or not all(r in temp[h] and r in temp[h3] for r in NAMES): continue
    dl = temp[h3]["S205"] - temp[h]["S205"]
    db = st.mean(temp[h3][r] - temp[h][r] for r in BED)
    if dl >= 1.0 and dl - db >= 0.8 and (not ev or h - ev[-1][0] > dt.timedelta(hours=12)):
        ev.append((h, dl, db))
print(f"   {len(ev)} events; by month:", sorted(collections.Counter(f"{h:%Y-%m}" for h, *_ in ev).items()))
print("   start hour:", sorted(collections.Counter(h.hour for h, *_ in ev).items()))
# effect on bedrooms over the next 12 h vs the living room
eff = []
for h, dl, db in ev:
    a, b = h + dt.timedelta(hours=6), h + dt.timedelta(hours=12)
    if all(x in temp and all(r in temp[x] for r in NAMES) for x in (h, a, b)):
        eff.append((temp[a]["S205"] - temp[h]["S205"], st.mean(temp[a][r] - temp[h][r] for r in BED),
                    temp[b]["S205"] - temp[h]["S205"], st.mean(temp[b][r] - temp[h][r] for r in BED)))
if eff:
    print(f"   after 6 h: living {st.mean(e[0] for e in eff):+.2f}, bedrooms {st.mean(e[1] for e in eff):+.2f}; after 12 h: living {st.mean(e[2] for e in eff):+.2f}, bedrooms {st.mean(e[3] for e in eff):+.2f}")
    print("   per bedroom after 6 h:", {NAMES[r]: round(st.mean(temp[h + dt.timedelta(hours=6)][r] - temp[h][r] for h, *_ in ev if (h + dt.timedelta(hours=6)) in temp and r in temp[h + dt.timedelta(hours=6)] and r in temp[h]), 2) for r in BED})

# 4. Window airing / sudden drops in bedrooms (−1 °C within 1 h)
print("\n4. Sudden bedroom drops (≥1 °C in 1 h, e.g. window open):")
for r in BED:
    n = sum(1 for h in hours if (h + dt.timedelta(hours=1)) in temp and r in temp[h] and r in temp[h + dt.timedelta(hours=1)]
            and temp[h][r] - temp[h + dt.timedelta(hours=1)][r] >= 1.0 and h.month in (10, 11, 12, 1, 2, 3))
    print(f"   {NAMES[r]}: {n} hours")

print("\n5. Bedroom C drops by hour and month:", sorted(collections.Counter(h.hour for h in hours if (h + dt.timedelta(hours=1)) in temp and "S204" in temp[h] and "S204" in temp[h + dt.timedelta(hours=1)] and temp[h]["S204"] - temp[h + dt.timedelta(hours=1)]["S204"] >= 1.0).items()),
      sorted(collections.Counter(f"{h:%Y-%m}" for h in hours if (h + dt.timedelta(hours=1)) in temp and "S204" in temp[h] and "S204" in temp[h + dt.timedelta(hours=1)] and temp[h]["S204"] - temp[h + dt.timedelta(hours=1)]["S204"] >= 1.0).items()))
print("6. Daily living − bedroom avg (°C), Jan 20 – Feb 15 (fires reported in cold spells):")
by = collections.defaultdict(list)
for h in hours:
    if dt.date(2026, 1, 20) <= h.date() <= dt.date(2026, 2, 15) and all(r in temp[h] for r in NAMES):
        by[h.date()].append((temp[h]["S205"] - st.mean(temp[h][r] for r in BED), temp[h]["S205"]))
print("   " + " ".join(f"{d:%m-%d}:{st.mean(x[0] for x in v):+.1f}/max{max(x[1] for x in v):.1f}" for d, v in sorted(by.items())))
