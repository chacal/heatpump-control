"""Heating planner: in which hours the setpoint should ask for heat.

Dynamic programming over hourly decisions (the first one runs to the next full hour), each
simulated in 15-minute slots with their own prices. The state is the room (bedroom median) and
slab temperatures on a grid, and whether the previous hour asked for heat. Asking for heat means
the active setpoint holds the heat value, otherwise the idle value; the pump's curve turns that
into heat (model.py), so even "idle" heats on a cold enough day.

An hour costs its electricity at spot price; a start costs a penalty (long runs are healthy for
the compressor); the room outside the comfort band costs a steep penalty (a soft bound, so a
house that starts outside the band still gets a plan), and below the target a weak one (a
tie-breaker: with flat prices the house stays near the target instead of drifting to the band's
bottom, while a real price spread still moves the heat); a setpoint write costs a small penalty
(EEPROM wear; the caller raises it until the writes fit the daily cap). Heat left in the house
at the end is credited at the horizon's mean price, so the plan neither drains nor overfills it.
"""

from dataclasses import dataclass
from datetime import datetime

from heatpump_control.model import Curve, House

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
    start_penalty_eur: float = 0.05
    write_penalty_eur: float = 0.02
    comfort_penalty_eur_per_k_h: float = 1.0
    target_c: float = 21.0
    target_penalty_eur_per_k_h: float = 0.01
    vat: float = 0.255
    room_grid_k: float = 0.05
    slab_grid_k: float = 0.5
    slab_range_c: tuple[float, float] = (16.0, 36.0)


@dataclass(frozen=True)
class Plan:
    heating: list[bool]  # per slot
    t_room: list[float]  # predicted bedroom median at the end of each slot
    t_slab: list[float]
    cost_eur: float  # electricity at spot price incl. VAT, no penalties
    starts: int


def hour_blocks(slots: list[Slot]) -> list[range]:
    """Slot ranges of the decisions: up to the next full hour, then whole hours."""
    out, i = [], 0
    while i < len(slots):
        j = i + 1
        while j < len(slots) and slots[j].start.minute != 0:
            j += 1
        out.append(range(i, j))
        i = j
    return out


class _Sim:
    """15-minute steps of the house, with the per-slot inputs precomputed."""

    def __init__(self, slots, house: House, curve: Curve, setpoints: tuple[float, float], limits: Limits):
        self.h, self.limits = house, limits
        self.flow = [[curve.flow_c(setpoints[a], s.t_out) for s in slots] for a in (0, 1)]
        self.eur_per_kwh_heat = [s.price / 1000 * (1 + limits.vat) / house.cop for s in slots]
        self.gains = [house.free_gain_kw + house.sun_aperture_m2 * s.radiation / 1000 for s in slots]
        self.t_out = [s.t_out for s in slots]

    def comfort(self, t: float) -> float:
        lim = self.limits
        out = max(lim.band_c[0] - t, 0.0, t - lim.band_c[1])
        below = max(lim.target_c - t, 0.0)
        return (out * lim.comfort_penalty_eur_per_k_h + below * lim.target_penalty_eur_per_k_h) * SLOT_H

    def run(self, idx: range, a: int, tr: float, ts: float, trace: list | None = None) -> tuple[float, float, float, float]:
        """(electricity €, penalties €, room, slab) after the slots `idx` with action `a`."""
        h = self.h
        k_sr, ua, cs, cr = h.slab_room_kw_per_k, h.ua_kw_per_k, h.slab_kwh_per_k, h.room_kwh_per_k
        eur = pen = 0.0
        flow = self.flow[a]
        for i in idx:
            heat = min(h.heat_max_kw, max(0.0, h.heat_kw_per_k * (flow[i] - ts) + h.heat_offset_kw))
            eur += heat * SLOT_H * self.eur_per_kwh_heat[i]
            q = k_sr * (ts - tr)
            ts += (heat - q) * SLOT_H / cs
            tr += (q + self.gains[i] - ua * (tr - self.t_out[i])) * SLOT_H / cr
            pen += self.comfort(tr)
            if trace is not None:
                trace.append((tr, ts))
        return eur, pen, tr, ts


def simulate(
    slots: list[Slot],
    t_room: float,
    t_slab: float,
    heating: list[bool],
    house: House = House(),
    curve: Curve = Curve(),
    setpoints: tuple[float, float] = (18.0, 23.0),
    limits: Limits = Limits(),
    heating_now: bool = True,
) -> Plan:
    sim, trace, cost, starts, prev = _Sim(slots, house, curve, setpoints, limits), [], 0.0, 0, heating_now
    tr, ts = t_room, t_slab
    for i, a in enumerate(heating):
        eur, _, tr, ts = sim.run(range(i, i + 1), int(a), tr, ts, trace)
        cost += eur
        starts += a and not prev
        prev = a
    return Plan(list(heating), [r for r, _ in trace], [s for _, s in trace], cost, starts)


def plan(
    slots: list[Slot],
    t_room: float,
    t_slab: float,
    heating_now: bool,
    house: House = House(),
    curve: Curve = Curve(),
    setpoints: tuple[float, float] = (18.0, 23.0),
    limits: Limits = Limits(),
    register_heats: list[bool | None] | None = None,
) -> Plan:
    """Plans `slots` from the room and slab temperatures. `setpoints` are the (idle, heat) values
    of the active setpoint. `register_heats` marks the slots where the time program makes
    another setpoint active: whether that setpoint then asks for heat (None inside a segment,
    where a write is any change from the previous hour)."""
    marks = register_heats or [None] * len(slots)
    sim = _Sim(slots, house, curve, setpoints, limits)
    blocks = hour_blocks(slots)
    r_lo, r_step = limits.band_c[0] - 1.5, limits.room_grid_k
    s_lo, s_step = limits.slab_range_c[0], limits.slab_grid_k
    nr = round((limits.band_c[1] + 1.0 - r_lo) / r_step) + 1
    ns = round((limits.slab_range_c[1] - s_lo) / s_step) + 1
    rooms = [r_lo + i * r_step for i in range(nr)]
    slabs = [s_lo + j * s_step for j in range(ns)]

    def interp(v: list[list[float]], tr: float, ts: float) -> float:
        x = min(max((tr - r_lo) / r_step, 0.0), nr - 1.0)
        y = min(max((ts - s_lo) / s_step, 0.0), ns - 1.0)
        i, j = min(int(x), nr - 2), min(int(y), ns - 2)
        fx, fy = x - i, y - j
        a, b = v[i], v[i + 1]
        return (a[j] * (1 - fy) + a[j + 1] * fy) * (1 - fx) + (b[j] * (1 - fy) + b[j + 1] * fy) * fx

    def switch(b: int, a: int, h: int) -> float:
        mark = marks[blocks[b].start]
        held = h if mark is None else int(mark)
        return (limits.start_penalty_eur if a and not h else 0.0) + (limits.write_penalty_eur if a != held else 0.0)

    # Heat left in the house at the end, valued at the horizon's mean price.
    mean = sum(sim.eur_per_kwh_heat) / max(len(slots), 1)
    lo = limits.band_c[0]
    terminal = [[(house.room_kwh_per_k * (lo - r) + house.slab_kwh_per_k * (lo - s)) * mean for s in slabs] for r in rooms]

    # values[b][h]: cost from the start of decision b onwards, h = the previous decision.
    values: list = [None] * len(blocks) + [[terminal, terminal]]
    for b in range(len(blocks) - 1, -1, -1):
        nxt = values[b + 1]
        outcome = [[[0.0] * ns for _ in range(nr)] for _ in (0, 1)]
        for a in (0, 1):
            for i, tr in enumerate(rooms):
                row = outcome[a][i]
                for j, ts in enumerate(slabs):
                    eur, pen, tr2, ts2 = sim.run(blocks[b], a, tr, ts)
                    row[j] = eur + pen + interp(nxt[a], tr2, ts2)
        values[b] = [
            [[min(outcome[a][i][j] + switch(b, a, h) for a in (0, 1)) for j in range(ns)] for i in range(nr)]
            for h in (0, 1)
        ]

    heating, tr, ts, h = [], t_room, t_slab, int(heating_now)
    for b, idx in enumerate(blocks):
        best = None
        for a in (0, 1):
            eur, pen, tr2, ts2 = sim.run(idx, a, tr, ts)
            c = eur + pen + switch(b, a, h) + interp(values[b + 1][a], tr2, ts2)
            if best is None or c < best[0]:
                best = (c, a, tr2, ts2)
        _, h, tr, ts = best
        heating += [bool(h)] * len(idx)
    return simulate(slots, t_room, t_slab, heating, house, curve, setpoints, limits, heating_now)
