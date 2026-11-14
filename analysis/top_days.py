import sys, runpy
g = runpy.run_path(sys.argv[3], run_name="lib")
price, energy, days = g["price"], g["energy"], g["days"]
EXTRA = 6.278  # c/kWh flat: margin + transfer + tax, incl. VAT
rows = []
for day, hours in days.items():
    e = {h: (energy[h]["heat"] + energy[h]["dhw"]) if h in energy else 0.0 for h in hours}
    kwh = sum(e.values()); spot = sum(e[h] * price[h] for h in hours) / 100
    top = max(hours, key=lambda h: e[h] * price[h])
    rows.append((spot, day, kwh, spot / kwh * 100 if kwh else 0, max(price[h] for h in hours),
                 sum(price[h] for h in hours) / len(hours), top.hour, e[top] * price[top] / 100))
rows.sort(reverse=True)
tot = sum(r[0] for r in rows)
print(f"year spot €{tot:.0f}; top 10 days €{sum(r[0] for r in rows[:10]):.0f}, top 30 €{sum(r[0] for r in rows[:30]):.0f}; median day €{sorted(r[0] for r in rows)[len(rows)//2]:.2f}")
for spot, day, kwh, paid, mx, avg, th, tc in rows[:12]:
    print(f"{day} {day.strftime('%a')} spot €{spot:5.2f} +flat €{kwh*EXTRA/100:4.2f}  {kwh:5.1f} kWh  paid {paid:5.1f} c  day avg {avg:5.1f} max {mx:5.1f}  costliest hour {th:02d}: €{tc:.2f}")
