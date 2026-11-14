# heatpump-control: spec

Status: v1 draft, from the design session (DEV-1, 2026-11-14). Numbers below are from backtests
on one year of the house's own data (2025-11..2026-11); the scripts are in `analysis/`, the data
is not in this repo (see `analysis/README.md`).

## Goal

Keep the rooms inside a comfort band while moving the heat pump's heating (later hot water) out
of the priciest spot-price hours and into cheap ones, using today's and tomorrow's prices.

The heat pump is a Viessmann Vitocal 333-G (ground source, P300) reached through vcontrold on
the Optolink. Heating first: it has 3-5x the savings potential of hot water, and a planning
mistake costs less comfort than cold water does.

## Success is measured, not assumed

- **€ saved** against uncontrolled days: alternate controlled and uncontrolled days for the
  first weeks (M3), compare spot cost per heating degree day.
- **Comfort:** minutes per day with the bedroom median outside the band.
- **Compressor starts per day** (baseline ~1.4; long runs are healthy for the pump).
- **Expected value** (backtest, within-day shifting, 10 % COP loss on moved energy): the owner's
  existing time program already saves ~€49/yr; the controller's own value on top is ~€10-25/yr
  plus day-ahead storing, fireplace use and comfort guards. Savings grow almost in step with
  intra-day price swings, so the case strengthens if prices get spikier. This is a hobby with a
  modest payoff, and the spec treats safety and simplicity as more important than the last euro.

## Lever

The room setpoint `RoomTempNormal` (address 2000). On weather compensation it shifts the heating
curve: lower and the compressor idles, higher and the floor slab stores heat. Not OpMode
switching, not the heating curve itself.

- Needs a set command in vcontrold's `vito.xml` (today only the hot water setpoints are
  writable). Done on the infra side; see M1.
- The pump's own time program stays as the fallback: the controller only overrides it. A dead
  controller leaves today's behaviour. Whether to override through setpoint writes or the
  pump's party/economy modes is to be checked in M1.
- Writes go to the pump's EEPROM, so they are capped (at most 12/day) until the limit is known.

## Controller

A small deterministic Python service. Every 15 minutes:

1. Read prices (today + tomorrow), the weather forecast (temperature, solar radiation), room
   temperatures, the fireplace sensor and the pump's state.
2. Plan the next 24-36 h with a simple house model: heat loss ~140 W/K, ~15-18 kWh of heat per
   degree of indoor temperature, free gains (sun, fireplace).
3. Write the setpoint only when the plan changes.

Room temperatures come straight from MQTT. Readings outside 15-35 °C or older than a few minutes
are dropped (a dying battery reads 0-1 °C). The **median of the bedrooms** steers, so one bad
sensor doesn't move it. Nothing is steered per room: every room has its own thermostat, which
balances the rooms; the pump can only add heat to the whole house.

Runs in **shadow mode** (plans and logs, no writes) before going live.

## Comfort band

Bedroom median 20.0-22.0 °C, adjustable from Home Assistant. An "away" mode lowers the band
while nobody is home.

## Safety

- Setpoint only within fixed bounds (e.g. 18-23 °C).
- A room floor (e.g. 20 °C) overrides any plan.
- A maximum length for an idle stretch.
- The write cap above.
- A **dead-man** on the pump's side restores the default setpoint when the controller's
  heartbeat stops, because the setting persists in the pump otherwise.
- Compressor starts: a minimum off time and a daily start budget in the planner; a metric and
  alert on `CompressorStarts` per day, independent of the pump's own timers.
- Alerts: room below the floor, controller stale.

## Fireplace

A temperature probe on the fireplace's mass. Mass temperature minus room temperature is roughly
the heat still coming out; the planner counts it as free heat, so the pump backs off while the
fireplace releases heat. Steering stays on the bedrooms, so a lit fireplace doesn't make them
cold. (M4; until then a manual "fireplace lit" switch.)

## Interfaces

- **In:** MQTT sensor topics (read-only user), spot prices and forecast from public APIs, pump
  state from vcontrold.
- **Out:** setpoint writes to vcontrold; Prometheus metrics; Home Assistant entities through MQTT
  discovery (mode, current plan, setpoint offset, today's savings estimate) with a few controls
  (on/off, comfort min/max, away, fireplace lit). No web UI of its own.
- **History and analysis:** a Grafana dashboard from the metrics.

## LLM

Not in the control loop: the decision is a small optimisation that plain code does better,
cheaper and testably, every 15 minutes with no API dependency. An LLM helps beside the loop:
the plan as read-only JSON for a morning report, a daily note when tomorrow's prices arrive
(advice for human-only levers such as lighting the fireplace before an expensive evening), and a
weekly review that proposes parameter changes for the owner to approve. The reader is untrusted
and gets no credentials.

## Where it runs

A container on a home server, deployed by the infrastructure side from a deploy request
(`docs/deploy-request.md`). It needs network access to vcontrold, an MQTT user with an ACL (read
sensor topics, write its own), and no Home Assistant token. The development environment has no
access to the pump or to any deployment credential.

## Milestones

| | What | Done when |
|---|---|---|
| M1 | Write test | `vito.xml` set command; one write takes effect and the time program does not undo it |
| M2 | Shadow mode | plans, metrics, HA entities; plan in the morning report |
| M3 | Live heating | guards, alternate days, success measures reported |
| M4 | Fireplace sensor | probe in the model; family notes |
| M5 | Day-ahead storing | pre-heating before expensive periods |
| M6 | Hot water | morning run to the cheapest hour, evening boost |

## Open questions

- Setpoint write vs party/economy mode as the override (M1).
- The pump's EEPROM write limit (M1).
- Format of the deploy request (`docs/deploy-request.md`, proposal).
