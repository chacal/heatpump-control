"""One planning round: the slots ahead, the plan, the setpoint writes and the baseline.

The baseline is the pump's own time program as the model sees it: the active setpoint keeps its
program value (Normal asks for heat, Reduced is idle, which the curve may still turn into heat). The savings estimate is the
baseline's cost minus the plan's plus `heat_left_eur`: the difference in heat left in the house
at the end, valued at the horizon's mean price (a plan that ends warmer has bought heat ahead).
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from heatpump_control.model import Curve, House
from heatpump_control.planner import SLOT_H, Limits, Plan, Slot, mean_eur_per_kwh_heat, plan, simulate
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
    savings_eur: float  # baseline.cost_eur - plan.cost_eur + heat_left_eur
    heat_left_eur: float


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


def baseline(slots: list[Slot], t_room: float, t_slab: float, house: House, curve: Curve, limits: Limits, program: Program) -> Plan:
    heating = [program.heating(s.start) for s in slots]
    return simulate(slots, t_room, t_slab, heating, house, curve, (program.idle_c, program.heat_c), limits)


def decide(
    slots: list[Slot],
    t_room: float,
    t_slab: float,
    registers: dict[str, float] | None,
    curve: Curve = Curve(),
    house: House = House(),
    limits: Limits = Limits(),
    program: Program = Program(),
    write_cap: int = WRITE_CAP_PER_DAY,
) -> Round:
    """Plans, doubling the write penalty until the writes fit the daily cap (at most 8 times)."""
    registers = dict(registers or program.defaults())
    heating_now = registers[program.active(slots[0].start)] == program.heat_c
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
    setpoints = (program.idle_c, program.heat_c)
    lim = limits
    for _ in range(8):
        p = plan(slots, t_room, t_slab, heating_now, house, curve, setpoints, lim, marks)
        sp = setpoint_writes([s.start for s in slots], p.heating, program, registers)
        over = max(sp.per_day(program.tz).values(), default=0) > write_cap
        if not over:
            break
        lim = replace(lim, write_penalty_eur=lim.write_penalty_eur * 2)
    base = baseline(slots, t_room, t_slab, house, curve, limits, program)
    mean_eur_kwh = mean_eur_per_kwh_heat(slots, house, curve, program.heat_c, limits)
    left = (
        house.room_kwh_per_k * (p.t_room[-1] - base.t_room[-1]) + house.slab_kwh_per_k * (p.t_slab[-1] - base.t_slab[-1])
    ) * mean_eur_kwh
    return Round(slots, p, base, sp, lim.write_penalty_eur, over, base.cost_eur - p.cost_eur + left, left)
