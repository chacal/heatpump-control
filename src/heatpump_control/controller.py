"""One planning round: the slots ahead, the plan, the setpoint writes and the baseline.

The baseline is the pump's own time program as the model sees it: heat while Normal is active,
and in the Reduced hours only if the bedrooms fall below the band. The savings estimate is the
baseline's cost minus the plan's, with the difference in heat left in the house at the end
valued at the horizon's mean price.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from heatpump_control.model import House
from heatpump_control.planner import SLOT_H, Limits, Plan, Slot, plan, simulate
from heatpump_control.program import Program, Setpoints, setpoint_writes
from heatpump_control.sources import Series, interpolate, step_value

SLOT = timedelta(hours=SLOT_H)
MAX_HORIZON = timedelta(hours=36)
WRITE_CAP_PER_DAY = 12


@dataclass(frozen=True)
class Round:
    slots: list[Slot]
    plan: Plan
    baseline: Plan
    setpoints: Setpoints
    write_penalty_eur: float
    over_write_cap: bool
    savings_eur: float


def slot_start(now: datetime) -> datetime:
    return now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)


def build_slots(now: datetime, prices: Series, temps: Series, radiation: Series) -> list[Slot]:
    """15-minute slots from now while there is a price and a temperature forecast, up to 36 h.
    Radiation past MEPS's horizon counts as 0 (less free heat: errs towards warm)."""
    slots, at = [], slot_start(now)
    while at < now + MAX_HORIZON:
        price = step_value(prices, at)
        t_out = interpolate(temps, at)
        if price is None or t_out is None:
            break
        slots.append(Slot(at, price, t_out, max(interpolate(radiation, at) or 0.0, 0.0)))
        at += SLOT
    return slots


def baseline(slots: list[Slot], t_now: float, house: House, limits: Limits, program: Program) -> Plan:
    t, heating = t_now, []
    for s in slots:
        h = program.heating(s.start) or t < limits.band_c[0]
        heating.append(h)
        t = house.step(t, s.t_out, s.radiation, h, SLOT_H)
    return simulate(slots, t_now, heating, house, limits)


def decide(
    slots: list[Slot],
    t_now: float,
    heating_now: bool,
    since_switch: int | None,
    registers: dict[str, float] | None,
    house: House = House(),
    limits: Limits = Limits(),
    program: Program = Program(),
    write_cap: int = WRITE_CAP_PER_DAY,
) -> Round:
    """Plans, doubling the write penalty until the writes fit the daily cap (at most 8 times)."""
    registers = dict(registers or program.defaults())
    marks, active = [], None
    for s in slots:
        now_active = program.active(s.start)
        if now_active != active:
            # Inside the horizon a register is assumed to hold the program's own value again.
            held = registers[now_active] if active is None else program.defaults()[now_active]
            marks.append(held == program.heat_c)
        else:
            marks.append(None)
        active = now_active
    lim = limits
    for _ in range(8):
        p = plan(slots, t_now, heating_now, house, lim, since_switch, marks)
        sp = setpoint_writes([s.start for s in slots], p.heating, program, registers)
        over = max(sp.per_day(program.tz).values(), default=0) > write_cap
        if not over:
            break
        lim = replace(lim, write_penalty_eur=lim.write_penalty_eur * 2)
    base = baseline(slots, t_now, house, limits, program)
    mean_eur_kwh = sum(s.price for s in slots) / max(len(slots), 1) / 1000 * (1 + limits.vat)
    left = (p.t_in[-1] - base.t_in[-1]) * house.capacity_kwh_per_k / house.cop * mean_eur_kwh if slots else 0.0
    return Round(slots, p, base, sp, lim.write_penalty_eur, over, base.cost_eur - p.cost_eur + left)
