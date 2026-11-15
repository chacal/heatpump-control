from datetime import UTC, datetime, timedelta

from heatpump_control.controller import decide
from heatpump_control.model import Curve, House, observe_slab
from heatpump_control.planner import Slot, hour_blocks, plan, simulate
from heatpump_control.program import Program, setpoint_writes

T0 = datetime(2026, 1, 14, 22, 0, tzinfo=UTC)  # midnight in Helsinki


def slots(prices, t_out=0.0, radiation=0.0, start=T0):
    return [Slot(start + timedelta(minutes=15 * i), p, t_out, radiation) for i, p in enumerate(prices)]


def hourly(prices, **kw):
    return slots([p for p in prices for _ in range(4)], **kw)


def test_curve_matches_the_formula():
    # 23 °C setpoint, level -9, slope 0.6 at +5 °C outdoors: ~26.3 °C flow (DEV-1 hand check).
    assert abs(Curve(-9, 0.6).flow_c(23, 5) - 26.3) < 0.1
    assert Curve(-9, 0.6).flow_c(18, 5) < Curve(-9, 0.6).flow_c(23, 5)


def test_idle_setpoint_gives_no_heat_on_a_mild_day_with_a_warm_slab():
    house, curve = House(), Curve()
    assert house.heat_kw(curve.flow_c(18, 5), 24.0) == 0.0
    assert house.heat_kw(curve.flow_c(23, 5), 24.0) > 1.0


def test_house_cools_without_heat_and_warms_with_it():
    house = House()
    assert house.step(21.0, 24.0, -10.0, 0.0, 0.0, 1.0)[1] < 24.0
    assert house.step(21.0, 24.0, -10.0, 0.0, 5.0, 1.0)[1] > 24.0


def test_observed_slab_forgets_its_start():
    a = observe_slab(20.0, [2.0] * 576, 21.0, 5 / 60)
    b = observe_slab(30.0, [2.0] * 576, 21.0, 5 / 60)
    assert abs(a - b) < 0.2
    assert abs(a - (21.0 + 2.0 / House().slab_room_kw_per_k)) < 0.2  # steady state


def test_hour_blocks_align_to_the_clock():
    s = slots([1.0] * 10, start=T0 + timedelta(minutes=30))
    assert [len(b) for b in hour_blocks(s)] == [2, 4, 4]


def test_heats_in_the_cheap_hours():
    # 18 h: 6 cheap, 6 expensive, 6 cheap.
    p = plan(hourly([20.0] * 6 + [300.0] * 6 + [20.0] * 6), 21.0, 25.0, False)
    expensive = p.heating[24:48]
    assert sum(expensive) <= 4  # at most one hour
    assert any(p.heating[:24])
    assert min(p.t_room) >= 20.0 - 0.05


def test_stays_in_band_when_prices_are_flat():
    p = plan(hourly([100.0] * 24), 20.8, 24.0, False)
    assert all(19.95 <= t <= 22.05 for t in p.t_room)


def test_recovers_from_below_band():
    p = plan(hourly([100.0] * 8), 19.0, 21.0, False)
    assert p.heating[0]
    assert p.t_room[-1] > 19.0


def test_simulate_counts_starts():
    s = slots([1.0] * 6)
    p = simulate(s, 21.0, 25.0, [True, False, False, True, True, False], heating_now=False)
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
    r = decide(hourly([20.0, 300.0] * 12), 21.0, 25.0, None, write_cap=4)
    assert not r.over_write_cap
    assert max(r.setpoints.per_day().values(), default=0) <= 4
    assert r.savings_eur > -0.5
