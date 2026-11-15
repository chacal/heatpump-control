# Analysis scripts

Backtests and house-model fits behind `docs/spec.md`. Plain Python 3 (stdlib only), no
dependencies. `shift_sim.py` is the base; most others load it with `runpy` for its loaders.

**The data is not in this repo.** Reasons: the weather file carries exact coordinates, the price
data's license is internal use only, and heat pump, room and whole-house meter series reveal
when the family is home. Put the inputs in an untracked `analysis/data/` (git-ignored):

| Input | What | Where from |
|---|---|---|
| `hp/r5.txt` | 5-min heat pump readings, `'Name' unix value` per line | Prometheus `heatpump_*` range queries |
| `hp/tin.txt`, `hp/tout.txt` | hourly indoor average / outdoor temperature | Prometheus / Home Assistant statistics |
| `hp/rooms.csv`, `hp/recent.csv` | room temperatures (hourly, recent raw) | the sensor archive / MQTT |
| `prices/fi.json` (+ `fi_<year>.json`) | hourly FI spot price | Energy-Charts `price?bzn=FI` |
| `wx/wind.json` | hourly temperature, wind, radiation | Open-Meteo archive API |
| `cal/rad.txt` | hourly measured global radiation | `fetch_radiation.py` (FMI, Kumpula) |
| `cal/settings.json`, `cal/sec.json` | hourly setpoints, curve level/slope; secondary return temperature | Prometheus `heatpump_*` range queries |

Run, for example: `python3 analysis/shift_sim.py data/hp/r5.txt data/prices/fi.json`. Each script's
docstring lists its arguments.

| Script | Question |
|---|---|
| `shift_sim.py` | Savings from moving energy to cheaper hours within the day |
| `split.py`, `joint_opt.py` | Heating vs hot water; one shared compressor |
| `scenarios.py` | Other years' prices, spikier prices |
| `sched_vs_dyn.py`, `fixed_vs_dyn.py` | Fixed time program vs price-based choice of hours |
| `house_model.py` | Heat loss (UA), storage time constant, extra-heat days |
| `wind_fit.py` | Do wind and sun explain the heat need? |
| `dhw_events.py`, `dhw_cost.py` | Hot water run-outs and the cost of evening reheats |
| `rooms.py` | Which rooms to steer by |
| `top_days.py` | The costliest days |
| `calibrate_house.py` | The planner's house model: slab and room, fitted on 12-h predictions |
| `calibrate_heat.py` | The pump's heat through its heating curve; slab vs return temperature |

Findings are summarised in `docs/spec.md`; the figures there come from these scripts.
