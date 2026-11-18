"""One planning round, shared by the one-shot `plan` command and the `serve` loop: fetch the
inputs, estimate the slab, plan, and describe the result for the log, Home Assistant and metrics."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from heatpump_control.controller import MAX_HORIZON, Round, build_slots, decide
from heatpump_control.model import HEAT_KW_PER_SPEED_PCT, RETURN_MINUS_SLAB_K, Curve, observe_slab
from heatpump_control.planner import Limits
from heatpump_control.program import HELSINKI
from heatpump_control.settings import Settings
from heatpump_control.sources import Series, fmi_forecast, heat_history, pump_state, spot_prices


class RoundError(Exception):
    """A round can't plan (missing inputs); the message says which."""


@dataclass(frozen=True)
class Inputs:
    prices: Series
    temps: Series
    radiation: Series
    pump: dict
    ret_start: float | None
    speeds: Series


def fetch_inputs(now: datetime, prometheus: str, latlon: str) -> Inputs:
    local = now.astimezone(HELSINKI).date()
    prices = spot_prices(local - timedelta(days=1), local + timedelta(days=1))
    forecast = fmi_forecast(latlon, now + MAX_HORIZON)
    ret_start, speeds = heat_history(prometheus, now)
    return Inputs(prices, forecast.get("Temperature", []), forecast.get("RadiationGlobal", []), pump_state(prometheus), ret_start, speeds)


@dataclass(frozen=True)
class Result:
    at: datetime
    t_room: float
    t_slab: float
    inputs: Inputs
    round: Round
    settings: Settings


def plan_round(now: datetime, t_room: float | None, inputs: Inputs, settings: Settings = Settings()) -> Result:
    if t_room is None:
        raise RoundError("no fresh bedroom readings")
    if inputs.ret_start is None or len(inputs.speeds) < 400:
        raise RoundError("no pump history for the slab estimate")
    pump = inputs.pump
    if any(pump.get(k) is None for k in ("normal_c", "reduced_c", "curve_level", "curve_slope")):
        raise RoundError("no pump settings in Prometheus")
    t_slab = observe_slab(inputs.ret_start - RETURN_MINUS_SLAB_K, [v * HEAT_KW_PER_SPEED_PCT for _, v in inputs.speeds], t_room, 5 / 60)
    slots = build_slots(now, inputs.prices, inputs.temps, inputs.radiation)
    if not slots:
        raise RoundError("no prices or forecast for the next slot")
    registers = {"normal": pump["normal_c"], "reduced": pump["reduced_c"]}
    limits = replace(Limits(), band_c=settings.band())
    r = decide(slots, t_room, t_slab, registers, Curve(pump["curve_level"], pump["curve_slope"]), limits=limits)
    return Result(now, t_room, t_slab, inputs, r, settings)


def round_log(res: Result) -> dict:
    """One round as a JSON-able dict: inputs and predictions per slot, for comparing the
    predictions with what the sensors measure later."""
    r, prices = res.round, res.inputs.prices
    # When Energy-Charts has tomorrow's prices shows as the first round whose prices_until jumps.
    prices_until = prices[-1][0] + (prices[-1][0] - prices[-2][0]) if len(prices) > 1 else None
    return {
        "at": res.at.isoformat(), "t_room": res.t_room, "t_slab": res.t_slab, "pump": res.inputs.pump,
        "band": list(res.settings.band()),
        "prices_until": prices_until.isoformat() if prices_until else None,
        "plan_eur": r.plan.cost_eur, "baseline_eur": r.baseline.cost_eur, "savings_eur": r.savings_eur,
        "starts": r.plan.starts, "write_penalty_eur": r.write_penalty_eur, "over_write_cap": r.over_write_cap,
        "writes": [[w.at.isoformat(), w.register, w.value] for w in r.setpoints.writes],
        "slots": {
            "start": r.slots[0].start.isoformat(),
            "price": [s.price for s in r.slots],
            "t_out": [round(s.t_out, 2) for s in r.slots],
            "radiation": [round(s.radiation) for s in r.slots],
            "heating": "".join("1" if h else "0" for h in r.plan.heating),
            "t_room": [round(t, 3) for t in r.plan.t_room],
            "t_slab": [round(t, 2) for t in r.plan.t_slab],
            "program": "".join("1" if h else "0" for h in r.baseline.heating),
            "program_t_room": [round(t, 3) for t in r.baseline.t_room],
        },
    }


def next_change(res: Result) -> datetime | None:
    h = res.round.plan.heating
    for s, a in zip(res.round.slots[1:], h[1:]):
        if a != h[0]:
            return s.start
    return None


def ha_state(res: Result, mode: str) -> dict:
    nc = next_change(res)
    return {
        "mode": mode,
        "bedrooms": round(res.t_room, 2),
        "slab": round(res.t_slab, 1),
        "heat_now": bool(res.round.plan.heating[0]),
        "next_change": nc.isoformat() if nc else None,
        "plan_eur": round(res.round.plan.cost_eur, 2),
        "savings_eur": round(res.round.savings_eur, 2),
        "last_round": res.at.isoformat(),
    }
