from datetime import UTC, datetime, timedelta

from heatpump_control.controller import decide
from heatpump_control.model import House
from heatpump_control.planner import Limits, Slot, plan, simulate
from heatpump_control.program import Program, setpoint_writes

T0 = datetime(2026, 1, 14, 22, 0, tzinfo=UTC)  # midnight in Helsinki


def slots(prices, t_out=-5.0, radiation=0.0):
    return [Slot(T0 + timedelta(minutes=15 * i), p, t_out, radiation) for i, p in enumerate(prices)]


def test_model_cools_without_heat_and_warms_with_it():
    house = House()
    assert house.step(21.0, -10.0, 0.0, False, 1.0) < 21.0
    assert house.step(21.0, -10.0, 0.0, True, 1.0) > 21.0


def test_heats_in_the_cheap_hours():
    # 12 h: cheap first 4 h, expensive 4 h, cheap again.
    s = slots([20.0] * 16 + [300.0] * 16 + [20.0] * 16)
    p = plan(s, 21.0, False)
    assert not any(p.heating[16:32])
    assert any(p.heating[:16])
    assert min(p.t_in) >= 20.0 - 0.05


def test_stays_in_band_when_prices_are_flat():
    p = plan(slots([100.0] * 96), 20.5, False)
    assert all(19.95 <= t <= 22.05 for t in p.t_in)


def test_recovers_from_below_band():
    p = plan(slots([100.0] * 16), 19.0, False)
    assert p.heating[0]
    assert p.t_in[-1] > 19.0


def test_minimum_run_and_off_times():
    p = plan(slots([10.0, 500.0] * 48), 21.0, False)
    runs, prev, length = [], None, 0
    for h in p.heating:
        if h == prev:
            length += 1
        else:
            if prev is not None:
                runs.append(length)
            prev, length = h, 1
    assert all(r >= 4 for r in runs)  # the last run may be cut by the horizon, so not counted


def test_simulate_counts_starts():
    s = slots([1.0] * 6)
    p = simulate(s, 21.0, [True, False, False, True, True, False], House(), Limits(), heating_now=False)
    assert p.starts == 2


def test_writes_only_where_plan_departs_from_the_program():
    program = Program()
    starts = [T0 + timedelta(hours=h) for h in range(24)]  # 00..23 Helsinki
    following = [program.heating(t) for t in starts]
    assert setpoint_writes(starts, following, program).writes == []
    departs = list(following)
    departs[13] = departs[14] = True  # heat 13-15 in the Reduced hours
    writes = setpoint_writes(starts, departs, program).writes
    assert [(w.register, w.value) for w in writes] == [("reduced", 23.0), ("reduced", 18.0)]


def test_decide_keeps_writes_under_the_cap():
    # Prices that flip every hour tempt the planner into many switches.
    r = decide(slots([20.0, 20.0, 20.0, 20.0, 300.0, 300.0, 300.0, 300.0] * 18), 21.0, False, None, None, write_cap=4)
    assert not r.over_write_cap
    assert max(r.setpoints.per_day().values(), default=0) <= 4
    assert r.savings_eur > -0.5


def test_flat_prices_keep_the_house_in_the_band_and_back_at_the_target():
    p = plan(slots([100.0] * 96), 21.5, False)
    assert min(p.t_in) >= 20.4
    assert p.t_in[-1] >= 21.0
