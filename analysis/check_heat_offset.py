"""Closed loop as the planner runs it: heat from the curve (model.py House.heat_kw), 16 h ahead,
measured t_out and radiation, the pump's own time program. Bias of heat and room, per day. (DEV-1)

Args: dir src [heat_offset_kw]; dir as for refit_house.py plus h_{RoomTempNormal,RoomTempReduced,
HeatingCurveLevel,HeatingCurveSlope}.json (hourly); src the app's src/ directory.
"""
import collections, datetime as dt, json, statistics as st, sys

D, SRC = sys.argv[1], sys.argv[2]; ARGV = list(sys.argv)
code = open(__file__.rsplit("/", 1)[0] + "/refit_house.py").read().split("FIT_TO =")[0]
sys.argv = [ARGV[0], D]
exec(code)
sys.path.insert(0, SRC)
from heatpump_control.model import Curve, House  # noqa: E402

hs = {m: [(int(t), float(v)) for t, v in json.load(open(f"{D}/h_{m}.json"))["data"]["result"][0]["values"]]
      for m in ("RoomTempNormal", "RoomTempReduced", "HeatingCurveLevel", "HeatingCurveSlope")}


def at(m, t):
    v = None
    for tt, x in hs[m]:
        if tt <= t:
            v = x
    return v


house = House(heat_offset_kw=float(ARGV[3])) if len(ARGV) > 3 else House()
FROM = dt.datetime(2026, 10, 25, tzinfo=TZ)  # level -9 / slope 0.6 since 10-24
rows = []
for x in seq:
    if x["local"] < FROM or x["local"].hour not in (1, 7, 13, 19):
        continue
    # slab observed from 48 h of measured heat, room pinned (as the app does)
    hist = [by_h.get(x["h"] - 3600 * i) for i in range(48, 0, -1)]
    if any(h is None for h in hist):
        continue
    ts = hist[0]["tin"] + 1.0
    for h in hist:
        ts += (h["heat"] - house.slab_room_kw_per_k * (ts - h["tin"])) / house.slab_kwh_per_k
    tr = x["tin"]
    hp = hm = 0.0
    ok = True
    for i in range(16):
        cur, nxt = by_h.get(x["h"] + 3600 * i), by_h.get(x["h"] + 3600 * (i + 1))
        if cur is None or nxt is None:
            ok = False
            break
        hr = cur["local"].hour
        sp = at("RoomTempNormal", cur["h"]) if (hr >= 21 or hr < 7) else at("RoomTempReduced", cur["h"])
        curve = Curve(at("HeatingCurveLevel", cur["h"]), at("HeatingCurveSlope", cur["h"]))
        for _ in range(4):
            q = house.heat_kw(curve.flow_c(sp, cur["tout"]), ts)
            hp += q / 4
            qs = house.slab_room_kw_per_k * (ts - tr)
            ts += (q - qs) * 0.25 / house.slab_kwh_per_k
            tr += (qs + house.free_gain_kw + house.sun_aperture_m2 * cur["rad"] / 1000 - house.ua_kw_per_k * (tr - cur["tout"])) * 0.25 / house.room_kwh_per_k
        hm += cur["heat"]
    if ok:
        rows.append(dict(local=x["local"], hp=hp, hm=hm, err=tr - nxt["tin"], tout=st.mean(by_h[x["h"] + 3600 * i]["tout"] for i in range(16))))

print(f"{len(rows)} 16-h runs from {FROM:%m-%d}: heat predicted {st.mean(r['hp'] for r in rows):.1f} vs measured {st.mean(r['hm'] for r in rows):.1f} kWh;"
      f" room at 16 h bias {st.mean(r['err'] for r in rows):+.2f} K, RMSE {(st.mean(r['err'] ** 2 for r in rows)) ** .5:.2f}")
days = collections.defaultdict(list)
for r in rows:
    days[r["local"].strftime("%m-%d")].append(r)
for d, v in sorted(days.items()):
    print(f"  {d}  tout {st.mean(r['tout'] for r in v):5.1f}  heat pred/meas {st.mean(r['hp'] for r in v):5.1f}/{st.mean(r['hm'] for r in v):5.1f}  room err {st.mean(r['err'] for r in v):+.2f}")
