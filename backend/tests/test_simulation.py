"""Simulation scenarios run through the real engine. Synthetic data only."""

from collections import Counter

import pytest

from tablewatch.config.layout import load_layout, load_zone_file, project_root
from tablewatch.core.models import ActionType, EventType, OperatorAction, Source, TableState
from tablewatch.core.thresholds import Thresholds
from tablewatch.simulation.scenarios import load_scenarios, parse_scenario
from tablewatch.simulation.session import SimSession

LAYOUT = load_layout()
CAM, ANCHOR, ZONES = load_zone_file(project_root() / "configs" / "zones" / "demo.json")
SCENARIOS = load_scenarios({t.id for t in LAYOUT.tables})


def run(sid: str, seed=None, until=None, th=None):
    events = []
    s = SimSession(LAYOUT, SCENARIOS[sid], th or Thresholds(), ZONES, ANCHOR, run_id=f"t-{sid}", seed=seed,
                   on_event=events.append)
    s.run_headless(until)
    return s, events


def alerts_raised(events, type_):
    return [e for e in events if e.type == EventType.ALERT_RAISED and e.payload["alert"]["type"] == type_]


def per_table(events, type_):
    return Counter(e.table_id for e in events if e.type == type_)


def signature(events):
    return [(e.type.value, round(e.t, 3), e.table_id, e.key) for e in events]


def test_all_scenarios_load_and_layout_has_ten_tables():
    assert len(LAYOUT.tables) == 10
    assert {"normal", "forgotten", "slow_cleaning", "rush", "tracking", "guided"} <= set(SCENARIOS)


@pytest.mark.parametrize("sid", ["normal", "forgotten", "slow_cleaning", "rush", "tracking", "guided"])
def test_scenarios_are_reproducible(sid):
    _, a = run(sid)
    _, b = run(sid)
    assert signature(a) == signature(b)
    assert len(a) > 10


def test_different_seed_changes_details_not_rules():
    _, a = run("normal", seed=1)
    _, b = run("normal", seed=2)
    assert signature(a) != signature(b)
    assert per_table(a, EventType.GUESTS_SEATED) == per_table(b, EventType.GUESTS_SEATED)


def test_scenario_a_normal_full_lifecycle():
    s, ev = run("normal")
    seated = per_table(ev, EventType.GUESTS_SEATED)
    assert seated == per_table(ev, EventType.SERVICE_CONFIRMED)
    assert seated == per_table(ev, EventType.GUESTS_DEPARTED)
    assert seated == per_table(ev, EventType.CLEANING_CONFIRMED)
    assert len(seated) == 6 and all(v == 1 for v in seated.values())
    assert not alerts_raised(ev, "UNSERVED_TOO_LONG")
    assert all(sm.state == TableState.FREE for sm in s.engine.tables.values())
    # Every service/cleaning confirmation is honestly attributed to the simulator.
    for e in ev:
        if e.type in (EventType.SERVICE_CONFIRMED, EventType.CLEANING_CONFIRMED):
            assert e.source == Source.SIMULATOR


def test_scenario_b_forgotten_customers_one_alert():
    s, ev = run("forgotten")
    unserved = alerts_raised(ev, "UNSERVED_TOO_LONG")
    assert [e.table_id for e in unserved] == ["T4"]
    seated = next(e for e in ev if e.type == EventType.GUESTS_SEATED and e.table_id == "T4")
    th = Thresholds()
    assert unserved[0].t == pytest.approx(seated.payload["seated_t"] + th.unserved_warning_s, abs=0.51)
    resolved = [e for e in ev if e.type == EventType.ALERT_RESOLVED and e.table_id == "T4"
                and e.payload["alert_type"] == "UNSERVED_TOO_LONG"]
    assert len(resolved) == 1 and resolved[0].payload["resolution"] == "service_confirmed"
    served = next(e for e in ev if e.type == EventType.SERVICE_CONFIRMED and e.table_id == "T4")
    assert served.payload["wait_s"] > th.unserved_warning_s


def test_scenario_b_operator_can_confirm_first():
    s = SimSession(LAYOUT, SCENARIOS["forgotten"], Thresholds(), ZONES, ANCHOR, run_id="op")
    s.run_headless(until=340)
    assert s.engine.tables["T4"].state == TableState.SEATED_UNSERVED
    s.engine.act(OperatorAction(ActionType.CONFIRM_SERVICE, s.t, table_id="T4", actor="manager"))
    s.run_headless()
    assert s.rejected_sim_actions == 0  # the simulator noticed and did not double-confirm
    assert s.engine.tables["T4"].proj.session_count == 1


def test_scenario_c_cleaning_overdue_escalates_never_auto_cleans():
    s, ev = run("slow_cleaning")
    overdue = alerts_raised(ev, "CLEANING_OVERDUE")
    assert [e.table_id for e in overdue] == ["T5"]
    dep = next(e for e in ev if e.type == EventType.GUESTS_DEPARTED and e.table_id == "T5")
    assert overdue[0].t == pytest.approx(dep.t + Thresholds().cleaning_overdue_s, abs=0.51)
    clean = next(e for e in ev if e.type == EventType.CLEANING_CONFIRMED and e.table_id == "T5")
    assert clean.t > overdue[0].t
    # Between departure and cleaning, T5 never left NEEDS_CLEANING.
    t5 = [e for e in ev if e.table_id == "T5" and e.to_state is not None and dep.t <= e.t < clean.t]
    assert [e.type for e in t5] == [EventType.GUESTS_DEPARTED]


def test_scenario_d_rush_fills_tables_and_raises_several_alerts():
    s, ev = run("rush")
    unserved = alerts_raised(ev, "UNSERVED_TOO_LONG")
    assert len({e.table_id for e in unserved}) >= 3
    # Several tables were occupied at once.
    occupied, peak = set(), 0
    for e in ev:
        if e.type == EventType.GUESTS_SEATED:
            occupied.add(e.table_id)
        elif e.type == EventType.GUESTS_DEPARTED:
            occupied.discard(e.table_id)
        peak = max(peak, len(occupied))
    assert peak >= 9
    assert alerts_raised(ev, "CLEANING_OVERDUE")


def test_scenario_e_tracking_problems_do_not_flip_states():
    s, ev = run("tracking")
    seated = per_table(ev, EventType.GUESTS_SEATED)
    departed = per_table(ev, EventType.GUESTS_DEPARTED)
    assert seated == Counter({"T6": 1, "T2": 1, "T9": 1, "T10": 1})  # no flip-flopping, no T3
    assert all(v == 1 for v in departed.values())
    assert [e.type for e in ev if e.camera_id == "sim-cam-1" and e.type.value.startswith("CAMERA")] == [
        EventType.CAMERA_OFFLINE, EventType.CAMERA_ONLINE,
    ]
    # T2 left during the outage: departure only after the camera returned, flagged uncertain.
    t2 = next(e for e in ev if e.type == EventType.GUESTS_DEPARTED and e.table_id == "T2")
    online = next(e for e in ev if e.type == EventType.CAMERA_ONLINE)
    assert t2.t >= online.t + Thresholds().departure_confirm_s
    assert t2.payload["during_observation_gap"] is True
    lowq = {e.table_id for e in alerts_raised(ev, "LOW_CONFIDENCE_OBSERVATION")}
    assert {"T9", "T10"} <= lowq
    # The ID switches on T6 at ~230 s did not produce phantom staff visits.
    t6_visits = [e for e in ev if e.type == EventType.POSSIBLE_STAFF_VISIT and e.table_id == "T6" and 225 <= e.t <= 260]
    assert t6_visits == []


def test_camera_offline_freezes_states_in_simulation():
    s = SimSession(LAYOUT, SCENARIOS["tracking"], Thresholds(), ZONES, ANCHOR, run_id="cam")
    s.run_headless(until=445)
    states = {k: v.state for k, v in s.engine.tables.items()}
    assert s.engine.cameras["sim-cam-1"].offline
    s.run_headless(until=489)
    assert {k: v.state for k, v in s.engine.tables.items()} == states


def test_guided_demo_tells_the_story():
    s, ev = run("guided")
    kinds = [e.type for e in ev]
    assert EventType.GUESTS_SEATED in kinds
    alert = alerts_raised(ev, "UNSERVED_TOO_LONG")
    assert [a.table_id for a in alert] == ["T6"]
    dep = next(e for e in ev if e.type == EventType.GUESTS_DEPARTED)
    assert dep.table_id == "T1" and dep.t > alert[0].t
    assert any(e.type == EventType.CLEANING_CONFIRMED and e.table_id == "T1" for e in ev)


def test_clock_pause_resume_drives_steps():
    s = SimSession(LAYOUT, SCENARIOS["normal"], Thresholds(), ZONES, ANCHOR, run_id="clk")
    assert s.catch_up() == 0  # paused at start
    s.clock.resume()
    s.clock.advance(1.0)  # 1 real second at 1x = 1 sim second = 2 steps
    assert s.catch_up() == 2
    s.clock.set_speed(10)
    s.clock.advance(1.0)
    assert s.catch_up() == 20
    s.clock.pause()
    s.clock.advance(5.0)
    assert s.catch_up() == 0
    assert s.t == pytest.approx(10.5)


def test_scenario_validation():
    with pytest.raises(ValueError):
        parse_scenario({"id": "x", "parties": [{"table": "T99", "size": 2, "arrive_s": 0, "dine_s": 10}]}, {"T1"})
    with pytest.raises(ValueError):
        parse_scenario({"id": "x", "parties": [], "disturbances": [{"type": "meteor", "at_s": 1}]})


def test_ground_truth_export_format():
    s, _ = run("normal")
    gt = s.sim.ground_truth()
    assert gt["format"] == "tablewatch-groundtruth/v1"
    assert "synthetic" in gt["source"]
    assert Counter(e["type"] for e in gt["events"])["seated"] == 6
