"""Heating planner: in which 15-minute slots the setpoint should ask for heat.

Dynamic programming over the slots. The state is the bedroom median (on a grid), whether the
previous slot heated, and how long the current run or rest has lasted (minimum run and off
times). A slot costs its electricity at spot price; a start costs a penalty, since long runs are
healthy for the compressor; a bedroom median outside the comfort band costs a steep penalty (a
soft bound, so a house that starts outside the band still gets a plan), and below the target a
weak one (a tie-breaker: with flat prices the house stays near the target instead of drifting to
the band's bottom, while a real price spread still moves the heat); a setpoint write costs
a small penalty (EEPROM wear; the caller raises it until the writes fit the daily cap). Heat
left in the house at the end is credited at the horizon's mean price, so the plan neither drains
nor overfills it.
"""

from dataclasses import dataclass
from datetime import datetime

from heatpump_control.model import House

SLOT_H = 0.25


@dataclass(frozen=True)
class Slot:
    start: datetime  # timezone-aware
    price: float  # spot, €/MWh without VAT
    t_out: float  # °C
    radiation: float  # global radiation, W/m²


@dataclass(frozen=True)
class Limits:
    band_c: tuple[float, float] = (20.0, 22.0)
    min_run_slots: int = 4
    min_off_slots: int = 4
    start_penalty_eur: float = 0.05
    write_penalty_eur: float = 0.02
    comfort_penalty_eur_per_k_h: float = 1.0
    target_c: float = 21.0
    target_penalty_eur_per_k_h: float = 0.01
    vat: float = 0.255
    grid_k: float = 0.02


@dataclass(frozen=True)
class Plan:
    heating: list[bool]
    t_in: list[float]  # predicted bedroom median at the end of each slot
    cost_eur: float  # electricity at spot price incl. VAT, no penalties
    starts: int


def slot_cost_eur(slot: Slot, heating: bool, t_in: float, house: House, limits: Limits) -> float:
    if not heating:
        return 0.0
    kwh = house.heat_kw(t_in, slot.t_out) * SLOT_H / house.cop
    return kwh * slot.price / 1000 * (1 + limits.vat)


def simulate(
    slots: list[Slot],
    t_now: float,
    heating: list[bool],
    house: House,
    limits: Limits,
    heating_now: bool = True,
) -> Plan:
    t, temps, cost, starts, prev = t_now, [], 0.0, 0, heating_now
    for slot, h in zip(slots, heating, strict=True):
        cost += slot_cost_eur(slot, h, t, house, limits)
        t = house.step(t, slot.t_out, slot.radiation, h, SLOT_H)
        temps.append(t)
        starts += h and not prev
        prev = h
    return Plan(list(heating), temps, cost, starts)


def plan(
    slots: list[Slot],
    t_now: float,
    heating_now: bool,
    house: House = House(),
    limits: Limits = Limits(),
    since_switch: int | None = None,
    register_heats: list[bool | None] | None = None,
) -> Plan:
    """Plans `slots` from a bedroom median of `t_now`; `since_switch` is how many slots the
    current run or rest has lasted (None: long enough to switch now). `register_heats` marks
    the slots where the time program makes another setpoint active: whether that setpoint then
    asks for heat (None inside a segment, where a write is any change from the previous slot)."""
    marks = register_heats or [None] * len(slots)
    lo_band, hi_band = limits.band_c
    lo, step = lo_band - 2.0, limits.grid_k
    n = round((hi_band + 1.0 - lo) / step) + 1
    grid = [lo + j * step for j in range(n)]
    m = max(limits.min_run_slots, limits.min_off_slots, 1)

    def interp(values: list[float], t: float) -> float:
        x = min(max((t - lo) / step, 0.0), n - 1.0)
        i = min(int(x), n - 2)
        f = x - i
        return values[i] * (1 - f) + values[i + 1] * f

    def comfort(t: float) -> float:
        out = max(lo_band - t, 0.0, t - hi_band)
        below = max(limits.target_c - t, 0.0)
        return (out * limits.comfort_penalty_eur_per_k_h + below * limits.target_penalty_eur_per_k_h) * SLOT_H

    def allowed(a: bool, h: bool, k: int) -> bool:
        return a == h or k >= (limits.min_run_slots if h else limits.min_off_slots)

    def next_k(a: bool, h: bool, k: int) -> int:
        return min(k + 1, m) if a == h else 1

    def step_cost(slot: Slot, a: bool, t: float) -> tuple[float, float]:
        t_next = house.step(t, slot.t_out, slot.radiation, a, SLOT_H)
        return slot_cost_eur(slot, a, t, house, limits) + comfort(t_next), t_next

    def switch(i: int, a: bool, h: bool) -> float:
        held = h if marks[i] is None else marks[i]
        return (limits.start_penalty_eur if a and not h else 0.0) + (limits.write_penalty_eur if a != held else 0.0)

    # Heat in the house at the end, valued at the horizon's mean price.
    mean_eur_kwh = sum(s.price for s in slots) / max(len(slots), 1) / 1000 * (1 + limits.vat)
    terminal = [(lo_band - t) * house.capacity_kwh_per_k / house.cop * mean_eur_kwh for t in grid]

    # values[i][h][k-1][j]: cost from the start of slot i onwards.
    values = [None] * len(slots) + [[[terminal] * m, [terminal] * m]]
    for i in range(len(slots) - 1, -1, -1):
        slot, nxt = slots[i], values[i + 1]
        steps = {a: [step_cost(slot, a, t) for t in grid] for a in (False, True)}
        here = [[None] * m, [None] * m]
        for h in (False, True):
            for k in range(1, m + 1):
                row = []
                for j in range(n):
                    best = float("inf")
                    for a in (False, True):
                        if not allowed(a, h, k):
                            continue
                        c, t_next = steps[a][j]
                        best = min(best, c + switch(i, a, h) + interp(nxt[a][next_k(a, h, k) - 1], t_next))
                    row.append(best)
                here[h][k - 1] = row
        values[i] = here

    t, h, k, heating = t_now, heating_now, min(since_switch or m, m), []
    for i, slot in enumerate(slots):
        options = []
        for a in (False, True):
            if allowed(a, h, k):
                c, t_next = step_cost(slot, a, t)
                options.append((c + switch(i, a, h) + interp(values[i + 1][a][next_k(a, h, k) - 1], t_next), a, t_next))
        _, a, t = min(options)
        heating.append(a)
        h, k = a, next_k(a, h, k)
    return simulate(slots, t_now, heating, house, limits, heating_now)
