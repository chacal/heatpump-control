"""House model: floor slab and room, heated through the pump's weather-compensation curve.

Two nodes: the pump heats the slab, the slab heats the room, the room loses heat to the outdoors
and gains it from people, appliances and the sun. The bedroom median is the room node; the slab
isn't measured (it is estimated from the pump's history, `observe_slab`).

The pump's heat follows its own heating curve: the active room setpoint, curve level and slope
set a flow temperature (Viessmann's published formula, which matched the measured flow within
~2 K over both periods), and the heat delivered grows with flow minus slab temperature.

Fitted on the house's own data (analysis/calibrate_house.py, calibrate_heat.py): Oct-Nov 2026
for the house, Oct-Nov 2026 and Dec 2025-Feb 2026 for the heat. Predicting the bedroom median
24 h ahead from the pump's settings alone: RMSE 0.42 K (autumn), 0.53 K (winter).

Checked 2026-11-17 on 10-15..11-17 (analysis/refit_house.py, check_heat_offset.py): driven by the
measured heat, the house nodes have ~no bias 16 h ahead (-0.05 K, RMSE 0.28); a free refit of all
six overfits (slab 61 kWh/K, no better on a held-out week), household electricity as a gain adds
0.03 K and would need its own forecast, so neither is used. The curve under-predicted heat by
~8 % this autumn: the offset went 1.50 -> 1.70 kW (16-h room bias -0.10 -> -0.05 K).
"""

from dataclasses import dataclass

# Heat from the compressor speed while heating (on, secondary flow < 40 °C): kWh per %-hour,
# scaled so the 52-week sum matches the pump's heating-heat register (10,692 kWh, 2026-11-09).
HEAT_KW_PER_SPEED_PCT = 0.0892
# While the compressor rests the secondary circuit keeps circulating, and its return temperature
# reads the slab (+0.46 K, sd 0.5 K, Oct-Nov 2026).
RETURN_MINUS_SLAB_K = 0.46


@dataclass(frozen=True)
class Curve:
    level: float = -9.0
    slope: float = 0.6

    def flow_c(self, setpoint_c: float, t_out: float) -> float:
        d = t_out - setpoint_c
        return setpoint_c + self.level - self.slope * d * (1.4347 + 0.021 * d + 247.9e-6 * d * d)


@dataclass(frozen=True)
class House:
    slab_kwh_per_k: float = 12.2
    room_kwh_per_k: float = 7.9
    slab_room_kw_per_k: float = 1.13
    ua_kw_per_k: float = 0.150
    free_gain_kw: float = 0.83
    sun_aperture_m2: float = 8.5
    cop: float = 5.2  # mean COP (analyses; the planner uses cop_at)
    # COP at a flow temperature: power reads 2026-11-16..18, heating only, flow < 40 °C
    # (-2.2 %/K; analysis/setpoint_pairs.py).
    cop_at_28: float = 5.65
    cop_per_k: float = 0.124
    heat_kw_per_k: float = 0.428
    heat_offset_kw: float = 1.70  # 1.50 fitted 11-15; raised to the measured heat 10-25..11-17
    heat_max_kw: float = 5.41

    def cop_at(self, flow_c: float) -> float:
        return max(1.5, self.cop_at_28 - self.cop_per_k * (flow_c - 28.3))

    def heat_kw(self, flow_c: float, t_slab: float) -> float:
        return min(self.heat_max_kw, max(0.0, self.heat_kw_per_k * (flow_c - t_slab) + self.heat_offset_kw))

    def step(
        self, t_room: float, t_slab: float, t_out: float, radiation: float, heat_kw: float, hours: float
    ) -> tuple[float, float]:
        """(room, slab) after `hours` (radiation in W/m²)."""
        q = self.slab_room_kw_per_k * (t_slab - t_room)
        gains = self.free_gain_kw + self.sun_aperture_m2 * radiation / 1000
        t_slab += (heat_kw - q) * hours / self.slab_kwh_per_k
        t_room += (q + gains - self.ua_kw_per_k * (t_room - t_out)) * hours / self.room_kwh_per_k
        return t_room, t_slab


def observe_slab(t_slab_start: float, heat_kw: list[float], t_room: float, hours: float, house: House = House()) -> float:
    """The slab now, integrated from `t_slab_start` over the measured heat (one value per
    `hours`), with the room held at `t_room`. Its time constant is ~11 h, so 48 h of history
    forgets the start value."""
    t = t_slab_start
    for q in heat_kw:
        t += (q - house.slab_room_kw_per_k * (t - t_room)) * hours / house.slab_kwh_per_k
    return t
