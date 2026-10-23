"""Engine-level behaviour: alerts, cameras, zones, actions, replay."""

import pytest

from helpers import TH, make_engine, person, run_engine, types
from tablewatch.core.engine import RestaurantEngine, TableInfo
from tablewatch.core.models import (
    ActionError,
    ActionType,
    AlertStatus,
    AlertType,
    EventType,
    OperatorAction,
    Source,
    TableState,
)

GUESTS_T1 = [person(1, 0.2, 0.5), person(2, 0.25, 0.6)]
GUESTS_T2 = [person(11, 0.8, 0.5)]


def alerts_of(eng, type_):
    return [a for a in eng.alerts.alerts.values() if a.type == type_]


def test_unserved_alert_fires_exactly_once_at_threshold():
    eng = make_engine()
    out = run_engine(eng, 0, 1500, lambda t: GUESTS_T1)
    raised = [e for e in out if e.type == EventType.ALERT_RAISED and e.payload["alert"]["type"] == "UNSERVED_TOO_LONG"]
    assert len(raised) == 1
    # seated_t = 0, so the alert fires at the first tick >= 300s
    assert raised[0].t == pytest.approx(TH.unserved_warning_s)
    assert eng.tables["T1"].state == TableState.SEATED_UNSERVED


def test_service_confirmation_resolves_alert_and_it_never_refires():
    eng = make_engine()
    run_engine(eng, 0, 400, lambda t: GUESTS_T1)
    [alert] = alerts_of(eng, AlertType.UNSERVED_TOO_LONG)
    assert alert.status == AlertStatus.ACTIVE
    out = eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 400, table_id="T1"))
    assert types(out) == ["SERVICE_CONFIRMED", "ALERT_RESOLVED"]
    assert alert.status == AlertStatus.RESOLVED and alert.resolution == "service_confirmed"
    run_engine(eng, 400, 2000, lambda t: GUESTS_T1)
    assert len(alerts_of(eng, AlertType.UNSERVED_TOO_LONG)) == 1


def test_unserved_alert_resolves_when_guests_leave():
    eng = make_engine()
    run_engine(eng, 0, 400, lambda t: GUESTS_T1)
    run_engine(eng, 400, 600, lambda t: [])
    [alert] = alerts_of(eng, AlertType.UNSERVED_TOO_LONG)
    assert alert.status == AlertStatus.RESOLVED and alert.resolution == "guests_departed"
    [clean] = alerts_of(eng, AlertType.TABLE_NEEDS_CLEANING)
    assert clean.status == AlertStatus.ACTIVE


def test_cleaning_escalation_once_and_never_auto_cleans():
    eng = make_engine()
    run_engine(eng, 0, 100, lambda t: GUESTS_T1)
    eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 100, table_id="T1"))
    run_engine(eng, 100, 300, lambda t: GUESTS_T1)
    out = run_engine(eng, 300, 2000, lambda t: [])
    dep = next(e for e in out if e.type == EventType.GUESTS_DEPARTED)
    overdue = [e for e in out if e.type == EventType.ALERT_RAISED and e.payload["alert"]["type"] == "CLEANING_OVERDUE"]
    assert len(overdue) == 1
    assert overdue[0].t == pytest.approx(dep.t + TH.cleaning_overdue_s, abs=0.6)
    assert eng.tables["T1"].state == TableState.NEEDS_CLEANING
    out = eng.act(OperatorAction(ActionType.CONFIRM_CLEANING, 2000, table_id="T1"))
    assert types(out) == ["CLEANING_CONFIRMED", "ALERT_RESOLVED", "ALERT_RESOLVED"]
    assert eng.tables["T1"].state == TableState.FREE
    assert not eng.alerts.active()


def test_two_tables_are_independent():
    eng = make_engine()
    run_engine(eng, 0, 100, lambda t: GUESTS_T1 + GUESTS_T2)
    assert eng.tables["T1"].state == TableState.SEATED_UNSERVED
    assert eng.tables["T2"].state == TableState.SEATED_UNSERVED
    eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 100, table_id="T2"))
    run_engine(eng, 100, 300, lambda t: GUESTS_T1)  # T2 guests leave
    assert eng.tables["T1"].state == TableState.SEATED_UNSERVED
    assert eng.tables["T2"].state == TableState.NEEDS_CLEANING
    assert eng.tables["T1"].proj.guest_count == 2
    assert eng.tables["T2"].proj.guest_count == 0


def test_person_in_overlap_is_not_counted_twice():
    eng = make_engine()
    middle = [person(5, 0.5, 0.5)]  # inside both zones, equally deep
    run_engine(eng, 0, 300, lambda t: middle)
    assert eng.tables["T1"].state == TableState.FREE
    assert eng.tables["T2"].state == TableState.FREE
    assert eng.cameras["cam"].last_association["ambiguous"] == [5]
    lowq = alerts_of(eng, AlertType.LOW_CONFIDENCE_OBSERVATION)
    assert {a.table_id for a in lowq} == {"T1", "T2"}


def test_point_clearly_deeper_in_one_zone_is_assigned_there():
    eng = make_engine()
    run_engine(eng, 0, 100, lambda t: [person(5, 0.47, 0.5)])  # in overlap, deeper in T1
    assert eng.tables["T1"].state == TableState.SEATED_UNSERVED
    assert eng.tables["T2"].state == TableState.FREE


def test_guest_walking_past_other_table_does_not_seat_it():
    eng = make_engine()
    run_engine(eng, 0, 100, lambda t: GUESTS_T1)

    def walk(t):
        # guest 1 visits T2's zone for 6 seconds (e.g. on the way to the restroom)
        if 100 <= t < 106:
            return [person(1, 0.8, 0.5), GUESTS_T1[1]]
        return GUESTS_T1

    run_engine(eng, 100, 400, walk)
    assert eng.tables["T2"].state == TableState.FREE
    assert eng.tables["T1"].state == TableState.SEATED_UNSERVED


def test_camera_offline_never_frees_a_table():
    eng = make_engine()
    run_engine(eng, 0, 100, lambda t: GUESTS_T1)
    out = run_engine(eng, 100, 1000, lambda t: [], camera_up=lambda t: False)
    assert "CAMERA_OFFLINE" in types(out)
    [cam_alert] = alerts_of(eng, AlertType.CAMERA_OFFLINE)
    assert cam_alert.status == AlertStatus.ACTIVE
    assert eng.tables["T1"].state == TableState.SEATED_UNSERVED
    assert eng.table_view("T1", 1000)["health"] == "OFFLINE"
    # Camera comes back and the table is empty. Departure needs a full healthy window.
    out = run_engine(eng, 1000, 1200, lambda t: [])
    assert out[0].type == EventType.CAMERA_ONLINE
    assert cam_alert.status == AlertStatus.RESOLVED
    dep = next(e for e in out if e.type == EventType.GUESTS_DEPARTED)
    assert dep.t >= 1000 + TH.departure_confirm_s
    assert dep.payload["during_observation_gap"] is True
    assert dep.payload["left_t"] == pytest.approx(99.5)


def test_guests_still_there_after_outage_keep_session():
    eng = make_engine()
    run_engine(eng, 0, 100, lambda t: GUESTS_T1)
    run_engine(eng, 100, 400, lambda t: [], camera_up=lambda t: False)
    # Tracker restarted: new IDs at the same positions.
    new_ids = [person(101, 0.2, 0.5), person(102, 0.25, 0.6)]
    out = run_engine(eng, 400, 900, lambda t: new_ids)
    assert EventType.GUESTS_DEPARTED not in [e.type for e in out]
    assert EventType.POSSIBLE_STAFF_VISIT not in [e.type for e in out]
    assert eng.tables["T1"].ev.reassociations == 2


def test_alert_acknowledgement_rules():
    eng = make_engine()
    run_engine(eng, 0, 400, lambda t: GUESTS_T1)
    [alert] = alerts_of(eng, AlertType.UNSERVED_TOO_LONG)
    out = eng.act(OperatorAction(ActionType.ACKNOWLEDGE_ALERT, 401, alert_id=alert.id, actor="maria"))
    assert types(out) == ["ALERT_ACKNOWLEDGED"]
    assert alert.status == AlertStatus.ACKNOWLEDGED and alert.acknowledged_by == "maria"
    with pytest.raises(ActionError):
        eng.act(OperatorAction(ActionType.ACKNOWLEDGE_ALERT, 402, alert_id=alert.id))
    # Acknowledging does not resolve. Only service does.
    assert alert in eng.alerts.active()
    eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 410, table_id="T1"))
    assert alert.status == AlertStatus.RESOLVED
    with pytest.raises(ActionError) as e:
        eng.act(OperatorAction(ActionType.ACKNOWLEDGE_ALERT, 420, alert_id=alert.id))
    assert e.value.code == "already_resolved"


def test_duplicate_requests_are_idempotent():
    eng = make_engine()
    events = []
    eng.subscribe(events.append)
    run_engine(eng, 0, 100, lambda t: GUESTS_T1)
    a = OperatorAction(ActionType.CONFIRM_SERVICE, 100, table_id="T1", request_id="req-1")
    first = eng.act(a)
    second = eng.act(a)  # network retry
    assert [e.id for e in first] == [e.id for e in second]
    assert types(events).count("SERVICE_CONFIRMED") == 1


def test_unknown_table_and_verify_without_uncertainty():
    eng = make_engine()
    with pytest.raises(ActionError) as e:
        eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 1, table_id="T99"))
    assert e.value.code == "not_found"
    with pytest.raises(ActionError) as e:
        eng.act(OperatorAction(ActionType.VERIFY_OBSERVATION, 1, table_id="T1"))
    assert e.value.code == "nothing_to_verify"


def test_verify_resolves_low_confidence_alert():
    eng = make_engine()
    blurry = [person(1, 0.2, 0.5, conf=0.2)]
    run_engine(eng, 0, 100, lambda t: blurry)
    [a] = alerts_of(eng, AlertType.LOW_CONFIDENCE_OBSERVATION)
    out = eng.act(OperatorAction(ActionType.VERIFY_OBSERVATION, 100, table_id="T1", reason="table is empty"))
    assert types(out) == ["OBSERVATION_VERIFIED", "ALERT_RESOLVED"]
    assert a.resolution == "operator_verified"


def test_corrections_resolve_alerts_and_raise_cleaning():
    eng = make_engine()
    run_engine(eng, 0, 400, lambda t: GUESTS_T1)
    out = eng.act(OperatorAction(ActionType.CORRECT_STATUS, 400, table_id="T1", to_state=TableState.NEEDS_CLEANING, reason="left via patio"))
    assert types(out) == ["STATUS_CORRECTED", "ALERT_RESOLVED", "ALERT_RAISED"]
    assert eng.tables["T1"].state == TableState.NEEDS_CLEANING


def test_replay_after_restart_rebuilds_state_and_alerts():
    events = []
    eng = make_engine(events=events)
    run_engine(eng, 0, 400, lambda t: GUESTS_T1 + GUESTS_T2)
    eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 400, table_id="T2"))
    run_engine(eng, 400, 700, lambda t: GUESTS_T1)
    before = {tid: sm.proj.to_dict() for tid, sm in eng.tables.items()}
    alerts_before = {a.id: a.to_dict() for a in eng.alerts.alerts.values()}

    restarted = make_engine()
    restarted.restore(events)
    assert {tid: sm.proj.to_dict() for tid, sm in restarted.tables.items()} == before
    assert {a.id: a.to_dict() for a in restarted.alerts.alerts.values()} == alerts_before
    # Dedup survives restart: the T1 unserved alert does not fire again.
    out = run_engine(restarted, 700, 800, lambda t: GUESTS_T1)
    assert not [e for e in out if e.type == EventType.ALERT_RAISED and e.payload["alert"]["type"] == "UNSERVED_TOO_LONG"]
    # Sequence numbers continue after the restored history.
    assert out == [] or out[0].seq > max(e.seq for e in events)


def test_same_inputs_give_identical_event_streams():
    def run():
        evs = []
        eng = make_engine(events=evs)
        run_engine(eng, 0, 500, lambda t: GUESTS_T1 if t < 300 else [])
        return [(e.type.value, e.t, e.table_id, e.key) for e in evs if e.type != EventType.ALERT_RAISED] + [
            (e.type.value, e.t, e.payload["alert"]["dedup_key"]) for e in evs if e.type == EventType.ALERT_RAISED
        ]

    assert run() == run()


def test_table_without_zone_reports_unknown_health():
    eng = RestaurantEngine([TableInfo("T1", "Table 1")])
    view = eng.table_view("T1", 0)
    assert view["health"] == "UNKNOWN"
    assert view["uncertainty"]
    eng.act(OperatorAction(ActionType.CORRECT_STATUS, 5, table_id="T1", to_state=TableState.SEATED_UNSERVED, reason="walk in"))
    assert eng.tables["T1"].state == TableState.SEATED_UNSERVED
