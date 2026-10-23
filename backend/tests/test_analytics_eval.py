import pytest

from tablewatch.analytics.metrics import compute
from tablewatch.evaluation.evaluate import evaluate

T0 = 1_792_756_800.0  # a fixed local-time anchor; only differences matter here


def session(sid, table, seated, left=None, wait=None, cleaning=None):
    return {"session_id": sid, "run_id": "r", "table_id": table, "seated_wall": T0 + seated,
            "left_wall": None if left is None else T0 + left, "occupied_s": None if left is None else left - seated,
            "wait_s": wait, "cleaning_s": cleaning, "turnover_s": None, "service_source": "operator" if wait else None,
            "unserved_alert": 0}


RUN = [{"id": "r", "label": "x", "kind": "test", "epoch_wall": T0, "last_wall": T0 + 1000}]


def test_offline_time_is_excluded_not_counted_as_empty():
    # Two tables, 1000 s window. T1 occupied 0-1000. Camera offline 400-600.
    events = [
        {"run_id": "r", "type": "CAMERA_OFFLINE", "t": 410, "wall": T0 + 410, "camera_id": "c", "payload": {"last_frame_t": 400}},
        {"run_id": "r", "type": "CAMERA_ONLINE", "t": 600, "wall": T0 + 600, "camera_id": "c", "payload": {}},
    ]
    a = compute("live", RUN, [session("s1", "T1", 0)], [], events, ["T1", "T2"])
    k = a["kpis"]
    assert k["observed_table_minutes"] == pytest.approx(2 * 800 / 60, abs=0.1)
    assert k["occupied_table_minutes"] == pytest.approx(800 / 60, abs=0.1)
    assert k["occupancy_rate"] == pytest.approx(0.5)
    assert k["camera_uptime"] == pytest.approx(0.8)
    assert k["camera_outages"] == 1


def test_service_stats_peak_staffing_and_small_sample_flag():
    sessions = [session("a", "T1", 0, 500, wait=60, cleaning=120), session("b", "T2", 100, 600, wait=180),
                session("c", "T3", 200, 900)]
    a = compute("simulation", RUN, sessions, [{"run_id": "r", "type": "UNSERVED_TOO_LONG", "created_wall": T0 + 1}], [],
                ["T1", "T2", "T3"], tables_per_waiter=2)
    k = a["kpis"]
    assert a["simulated"] and not a["insufficient_data"]
    assert k["service_response"]["mean_s"] == 120 and k["service_response"]["median_s"] == 120
    assert k["sessions_with_confirmed_service"] == 2 and k["sessions"] == 3
    assert k["peak_occupied_tables"] == 3
    assert a["staffing"]["estimated_servers_at_peak"] == 2
    assert k["unserved_warnings"] == 1
    empty = compute("live", [], [], [], [], ["T1"])
    assert empty["insufficient_data"] and empty["kpis"]["occupancy_rate"] is None


def test_evaluator_scores_against_ground_truth():
    truth = {"format": "tablewatch-groundtruth/v1", "source": "hand annotation", "events": [
        {"type": "seated", "table": "T1", "t": 100}, {"type": "departed", "table": "T1", "t": 900},
        {"type": "seated", "table": "T2", "t": 200}, {"type": "departed", "table": "T2", "t": 700},
        {"type": "staff_visit", "table": "T1", "start": 300, "end": 330},
    ]}
    predicted = [
        {"type": "GUESTS_SEATED", "table_id": "T1", "t": 130, "payload": {"seated_t": 100}},
        {"type": "GUESTS_DEPARTED", "table_id": "T1", "t": 990, "payload": {"left_t": 900}},
        {"type": "GUESTS_SEATED", "table_id": "T3", "t": 400, "payload": {"seated_t": 370}},  # false occupancy
        {"type": "POSSIBLE_STAFF_VISIT", "table_id": "T1", "t": 340, "payload": {"arrived_t": 302, "left_t": 329}},
    ]
    r = evaluate(predicted, truth, tolerance_s=30)
    assert r["seating"]["true_positives"] == 1 and r["seating"]["missed"] == 1 and r["seating"]["false_positives"] == 1
    assert r["seating"]["median_detection_delay_s"] == 30
    assert r["departure"]["missed_departures"] == 1 and r["departure"]["false_departures"] == 0
    assert r["staff_visits"]["recall"] == 1.0
    assert 0 < r["occupancy_state_agreement"] < 1
    with pytest.raises(ValueError):
        evaluate(predicted, {"events": []})
