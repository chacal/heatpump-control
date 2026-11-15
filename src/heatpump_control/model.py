"""House model: one thermal mass, heated by the pump, losing heat to the outdoors.

Numbers from the DEV-1 backtests on the house's own data (docs/spec.md): heat per day is about
3.4 kWh per K of indoor-outdoor difference minus 19 kWh of free gains (UA ~140 W/K); one K
indoors stores ~15-18 kWh of heat (time constant ~120 h); sun saves ~0.13 kWh of heat per day
per W/m² of daily mean radiation (~5.4 m² of effective aperture); seasonal COP 5.2.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class House:
    ua_kw_per_k: float = 0.141
    capacity_kwh_per_k: float = 16.5
    free_gain_kw: float = 0.79
    sun_aperture_m2: float = 5.4
    cop: float = 5.2
    # Heat mode: weather compensation drives the house towards an equilibrium (t_heat_c) and
    # modulates rather than cycles, so the output is what holds t_heat_c plus a pull towards it,
    # capped at the compressor's maximum (the coldest days need 14-15 h at full power for
    # ~110-125 kWh of heat, so ~8 kW). t_heat_c and the pull are guesses until calibrated against
    # the night runs of Sep-Nov 2026 (Normal active) and the shadow-mode logs.
    t_heat_c: float = 22.0
    pull_kw_per_k: float = 4.0  # closes a gap in ~4 h (capacity / pull)
    heat_max_kw: float = 8.0

    def heat_kw(self, t_in: float, t_out: float) -> float:
        hold = self.ua_kw_per_k * (self.t_heat_c - t_out) - self.free_gain_kw
        return min(self.heat_max_kw, max(0.0, hold + self.pull_kw_per_k * (self.t_heat_c - t_in)))

    def step(self, t_in: float, t_out: float, radiation: float, heating: bool, hours: float) -> float:
        """Indoor temperature after `hours` (radiation in W/m²)."""
        gains = self.free_gain_kw + self.sun_aperture_m2 * radiation / 1000
        heat = self.heat_kw(t_in, t_out) if heating else 0.0
        return t_in + (heat + gains - self.ua_kw_per_k * (t_in - t_out)) * hours / self.capacity_kwh_per_k
