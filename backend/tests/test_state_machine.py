"""State machine behaviour. Uses deterministic virtual time throughout."""

import pytest

from helpers import TH, feed, obs, types, zt
from tablewatch.core.models import ActionError, EventType, ObservationHealth, Source, TableState
from tablewatch.core.state_machine import TableProjection, TableStateMachine, reduce
from tablewatch.core.thresholds import Thresholds


def seated_machine(n_guests: int = 2, until: float = 40.0):
    sm = TableStateMachine("T1", "cam", "run:")
    guests = [zt(i + 1, 0.3 + 0.02 * i, 0.5) for i in range(n_guests)]
    events = feed(sm, 0, until, lambda t: guests)
    return sm, events, guests


def test_normal_lifecycle_free_to_free():
    sm, events, guests = seated_machine(2)
    assert types(events) == ["GUESTS_SEATED"]
    seated = events[0]
    assert seated.t == pytest.approx(TH.occupancy_confirm_s)
    assert seated.payload["seated_t"] == 0.0  # seated when guests sat, not when confirmed
    assert seated.payload["guest_count"] == 2
    assert seated.payload["initial_tracks"] == [1, 2]
    assert seated.session_id == "run:T1-1"
    assert sm.state == TableState.SEATED_UNSERVED

    served = sm.confirm_service(100.0, Source.OPERATOR, "alex")
    assert served.payload["wait_s"] == pytest.approx(100.0)
    assert sm.state == TableState.SERVED

    events = feed(sm, 40, 600, lambda t: guests)
    assert events == []
    events = feed(sm, 600, 800, lambda t: [])
    assert types(events) == ["GUESTS_DEPARTED"]
    dep = events[0]
    assert dep.payload["left_t"] == pytest.approx(599.5)
    assert dep.t == pytest.approx(599.5 + TH.departure_confirm_s, abs=0.6)
    assert dep.payload["served"] is True
    assert sm.state == TableState.NEEDS_CLEANING

    clean = sm.confirm_cleaning(900.0, Source.OPERATOR, "alex")
    assert sm.state == TableState.FREE
    assert clean.payload["cleaning_s"] == pytest.approx(900 - dep.t)
    assert clean.payload["turnover_s"] == pytest.approx(900 - 599.5)
    assert sm.proj.session_id is None


def test_cleaning_never_confirmed_by_timeout():
    sm, _, guests = seated_machine()
    feed(sm, 40, 200, lambda t: [])
    assert sm.state == TableState.NEEDS_CLEANING
    events = feed(sm, 200, 200 + 3 * 3600, lambda t: [], dt=5.0)
    assert events == []
    assert sm.state == TableState.NEEDS_CLEANING


def test_passerby_does_not_seat_table():
    sm = TableStateMachine("T1")
    events = feed(sm, 0, 6, lambda t: [zt(9)])
    events += feed(sm, 6, 100, lambda t: [])
    assert events == []
    assert sm.state == TableState.FREE


def test_repeated_short_visits_reset_candidate():
    """Presence that keeps disappearing for longer than the grace never seats."""
    sm = TableStateMachine("T1")

    def blink(t):
        return [zt(1)] if (t % 20) < 5 else []  # 5s present, 15s gone

    assert feed(sm, 0, 300, blink) == []
    assert sm.state == TableState.FREE


def test_short_gap_during_candidate_is_tolerated():
    sm = TableStateMachine("T1")
    present = lambda t: [] if 10 <= t < 15 else [zt(1)]  # 5s gap < 8s grace
    events = feed(sm, 0, 40, present)
    assert types(events) == ["GUESTS_SEATED"]
    assert events[0].payload["seated_t"] == 0.0


def test_staff_visit_is_only_a_candidate_never_service():
    sm, _, guests = seated_machine(2)
    staff = zt(50, 0.4, 0.45)
    events = feed(sm, 40, 60, lambda t: guests + [staff])
    events += feed(sm, 60, 80, lambda t: guests)
    assert types(events) == ["POSSIBLE_STAFF_VISIT"]
    visit = events[0]
    assert visit.payload["kind"] == "possible_service_visit"
    assert visit.payload["duration_s"] == pytest.approx(19.5)
    assert sm.state == TableState.SEATED_UNSERVED  # still unserved
    assert sm.proj.visits == 1


def test_very_short_visit_is_ignored():
    sm, _, guests = seated_machine(2)
    events = feed(sm, 40, 43, lambda t: guests + [zt(50, 0.4, 0.45)])
    events += feed(sm, 43, 70, lambda t: guests)
    assert events == []


def test_late_guest_who_stays_joins_the_party():
    sm, _, guests = seated_machine(2)
    late = zt(60, 0.35, 0.6)
    events = feed(sm, 40, 400, lambda t: guests + [late])
    assert events == []
    assert 60 in sm.ev.guests and 60 not in sm.ev.visitors
    # The late guest leaving later is not a "staff visit".
    events = feed(sm, 400, 450, lambda t: guests)
    assert events == []


def test_multiple_guests_partial_departure_keeps_table():
    sm, _, guests = seated_machine(4)
    events = feed(sm, 40, 400, lambda t: guests[:1])  # three leave, one stays
    assert EventType.GUESTS_DEPARTED not in [e.type for e in events]
    assert sm.state == TableState.SEATED_UNSERVED


def test_temporary_occlusion_does_not_end_session():
    sm, _, guests = seated_machine(2)
    events = feed(sm, 40, 100, lambda t: [])  # 60s gone, under 90s
    events += feed(sm, 100, 300, lambda t: guests)
    assert events == []
    assert sm.state == TableState.SEATED_UNSERVED


def test_single_missed_detection_is_harmless():
    sm, _, guests = seated_machine(2)
    events = feed(sm, 40, 300, lambda t: [] if t == 120.0 else guests)
    assert events == []
    assert sm.ev.absent_accum == 0.0


def test_track_id_change_is_reassociated_not_a_visit():
    sm, _, guests = seated_machine(1)
    # Guest 1 vanishes for 3 seconds and comes back as track 77 at the same spot.
    pos = guests[0].position
    events = feed(sm, 40, 43, lambda t: [])
    events += feed(sm, 43, 200, lambda t: [zt(77, pos[0] + 0.005, pos[1])])
    events += feed(sm, 200, 230, lambda t: [])  # leaves briefly
    assert events == []
    assert sm.ev.reassociations == 1
    assert 77 in sm.ev.guests


def test_low_confidence_tracks_never_seat_and_never_free():
    sm = TableStateMachine("T1")
    events = feed(sm, 0, 120, lambda t: [zt(1, conf=0.2)])
    assert EventType.GUESTS_SEATED not in [e.type for e in events]
    assert "LOW_CONFIDENCE_STARTED" in types(events)

    sm, _, guests = seated_machine(2)
    events = feed(sm, 40, 400, lambda t: [zt(1, conf=0.2)])  # only blurry evidence left
    assert EventType.GUESTS_DEPARTED not in [e.type for e in events]
    assert sm.health == ObservationHealth.LOW_CONFIDENCE
    events = feed(sm, 400, 430, lambda t: guests)
    assert "LOW_CONFIDENCE_ENDED" in types(events)
    assert sm.health == ObservationHealth.OK


def test_ambiguous_tracks_block_departure_but_do_not_seat():
    sm = TableStateMachine("T1")
    events = feed(sm, 0, 120, lambda t: [zt(1, ambiguous=True)])
    assert EventType.GUESTS_SEATED not in [e.type for e in events]
    sm, _, _ = seated_machine(1)
    events = feed(sm, 40, 400, lambda t: [zt(5, ambiguous=True)])
    assert EventType.GUESTS_DEPARTED not in [e.type for e in events]


def test_degraded_camera_freezes_absence():
    sm, _, _ = seated_machine(1)
    events = []
    t = 40.0
    while t < 400:
        events += sm.observe(obs(t, [], ObservationHealth.DEGRADED), TH)
        t += 0.5
    assert EventType.GUESTS_DEPARTED not in [e.type for e in events]


def test_offline_observations_change_nothing():
    sm, _, _ = seated_machine(1)
    for i in range(1000):
        assert sm.observe(obs(40 + i, [], ObservationHealth.OFFLINE), TH) == []
    assert sm.state == TableState.SEATED_UNSERVED


def test_unobserved_gaps_do_not_count_as_absence():
    sm, _, _ = seated_machine(1)
    events = []
    # One empty observation every 30 seconds: each gap exceeds max_observation_gap_s.
    for i in range(50):
        events += sm.observe(obs(40 + 30 * i, []), TH)
    assert events == []
    assert sm.state == TableState.SEATED_UNSERVED


def test_cleaning_visit_logged_as_possible_cleaning_visit():
    sm, _, _ = seated_machine(1)
    feed(sm, 40, 200, lambda t: [])
    assert sm.state == TableState.NEEDS_CLEANING
    events = feed(sm, 200, 260, lambda t: [zt(90)])
    events += feed(sm, 260, 280, lambda t: [])
    assert types(events) == ["POSSIBLE_STAFF_VISIT"]
    assert events[0].payload["kind"] == "possible_cleaning_visit"
    assert sm.state == TableState.NEEDS_CLEANING  # a visit does not clean the table


# ---------------------------------------------------------------- actions


def test_invalid_actions_are_rejected():
    sm = TableStateMachine("T1")
    with pytest.raises(ActionError) as e:
        sm.confirm_cleaning(1, Source.OPERATOR, "x")
    assert e.value.code == "invalid_state"
    with pytest.raises(ActionError):
        sm.confirm_service(1, Source.OPERATOR, "x")
    sm, _, _ = seated_machine()
    sm.confirm_service(50, Source.OPERATOR, "x")
    with pytest.raises(ActionError) as e:
        sm.confirm_service(51, Source.OPERATOR, "x")
    assert e.value.code == "already_confirmed"
    with pytest.raises(ActionError):
        sm.confirm_cleaning(52, Source.OPERATOR, "x")


def test_corrections_need_reason_and_follow_rules():
    sm, _, _ = seated_machine()
    with pytest.raises(ActionError) as e:
        sm.correct_status(TableState.FREE, "", 50, Source.OPERATOR, "x")
    assert e.value.code == "reason_required"
    with pytest.raises(ActionError) as e:
        sm.correct_status(TableState.SERVED, "they got drinks", 50, Source.OPERATOR, "x")
    assert e.value.code == "use_confirm_service"
    with pytest.raises(ActionError) as e:
        sm.correct_status(TableState.SEATED_UNSERVED, "same", 50, Source.OPERATOR, "x")
    assert e.value.code == "no_change"
    sm.confirm_service(60, Source.OPERATOR, "x")
    with pytest.raises(ActionError) as e:
        sm.correct_status(TableState.SEATED_UNSERVED, "oops", 61, Source.OPERATOR, "x")
    assert e.value.code == "cannot_unserve"


def test_correction_keeps_session_history():
    sm, events, guests = seated_machine()
    session = sm.proj.session_id
    ev = sm.correct_status(TableState.NEEDS_CLEANING, "guests left through patio", 120, Source.OPERATOR, "sam")
    assert ev.payload["previous_session_id"] == session
    assert ev.session_id == session
    assert sm.state == TableState.NEEDS_CLEANING
    assert sm.proj.session_id == session  # not erased
    # False departure: reopen the party.
    ev2 = sm.correct_status(TableState.SEATED_UNSERVED, "they are still here", 130, Source.OPERATOR, "sam")
    assert sm.state == TableState.SEATED_UNSERVED
    assert sm.proj.session_id == session
    assert sm.proj.seated_t == 0.0  # original seating time survives
    assert sm.proj.corrected


def test_manual_seating_waits_for_camera_before_auto_departure():
    sm = TableStateMachine("T1", "cam", "run:")
    sm.correct_status(TableState.SEATED_UNSERVED, "walk-in at blind corner", 10, Source.OPERATOR, "sam")
    assert sm.proj.session_id == "run:T1-1"
    events = feed(sm, 10, 500, lambda t: [])  # camera never sees them
    assert events == []
    assert sm.state == TableState.SEATED_UNSERVED


def test_reduce_rebuilds_projection_exactly():
    sm, events, guests = seated_machine(2)
    events.append(sm.confirm_service(70, Source.OPERATOR, "x"))
    events += feed(sm, 40, 300, lambda t: guests)
    events += feed(sm, 300, 500, lambda t: [])
    events.append(sm.confirm_cleaning(520, Source.OPERATOR, "x"))
    events += feed(sm, 520, 600, lambda t: guests)
    rebuilt = TableProjection("T1", "cam")
    for e in events:
        reduce(rebuilt, e)
    assert rebuilt.to_dict() == sm.proj.to_dict()
    assert rebuilt.state == TableState.SEATED_UNSERVED
    assert rebuilt.session_count == 2


def test_thresholds_are_respected():
    th = Thresholds(occupancy_confirm_s=10, departure_confirm_s=20, lost_track_grace_s=2)
    sm = TableStateMachine("T1")
    events = feed(sm, 0, 15, lambda t: [zt(1)], th=th)
    assert events[0].t == pytest.approx(10.0)
    events = feed(sm, 15, 50, lambda t: [], th=th)
    assert events[0].type == EventType.GUESTS_DEPARTED
    assert events[0].t == pytest.approx(14.5 + 20, abs=0.6)
