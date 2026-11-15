"""Hourly mean global radiation (W/m²) measured at FMI Kumpula (fmisid 101004, ~11 km from the house).

Args: out.txt first-day last-day (YYYY-MM-DD)
"""
import collections, datetime as dt, re, sys, urllib.request
out = collections.defaultdict(list)
t = dt.datetime.fromisoformat(sys.argv[2]).replace(tzinfo=dt.UTC)
while t < dt.datetime.fromisoformat(sys.argv[3]).replace(tzinfo=dt.UTC):
    e = t + dt.timedelta(days=6, hours=23, minutes=50)
    u = ("https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature&storedquery_id="
         "fmi::observations::radiation::simple&fmisid=101004&timestep=10&parameters=GLOB_1MIN"
         f"&starttime={t:%Y-%m-%dT%H:%M:%SZ}&endtime={e:%Y-%m-%dT%H:%M:%SZ}")
    x = urllib.request.urlopen(u, timeout=120).read().decode()
    for ts, v in re.findall(r"<BsWfs:Time>([^<]*)</BsWfs:Time>\s*<BsWfs:ParameterName>[^<]*</BsWfs:ParameterName>\s*<BsWfs:ParameterValue>([^<]*)", x):
        if v != "NaN":
            h = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(minute=0)
            out[int(h.timestamp())].append(max(float(v), 0.0))
    t += dt.timedelta(days=7)
with open(sys.argv[1], "w") as f:
    for h in sorted(out):
        f.write(f"{h} {sum(out[h]) / len(out[h]):.1f}\n")
print(len(out), "hours")
