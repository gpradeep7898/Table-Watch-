"""SQLite persistence, idempotency, replay and dataset separation."""

from helpers import make_engine, person, run_engine
from tablewatch.config.layout import load_layout, load_zone_file, project_root
from tablewatch.core.models import ActionType, Event, EventType, OperatorAction, Source, TableState
from tablewatch.core.thresholds import Thresholds
from tablewatch.simulation.scenarios import load_scenarios
from tablewatch.simulation.session import SimSession
from tablewatch.storage.db import Store

GUESTS = [person(1, 0.2, 0.5), person(2, 0.25, 0.6)]


def store_engine(tmp_path, run_id="run-1", dataset="simulation"):
    store = Store(tmp_path / "tw.db")
    store.create_run(run_id, dataset, "test", epoch_wall=1_700_000_000)
    events = []
    eng = make_engine(events=events)
    eng.subscribe(lambda ev: store.append_event(ev, dataset, run_id))
    return store, eng, events


def test_migrations_are_idempotent(tmp_path):
    s1 = Store(tmp_path / "a.db")
    s1.close()
    s2 = Store(tmp_path / "a.db")  # reopening does not re-run or wipe anything
    assert s2.q("SELECT COUNT(*) AS n FROM schema_version")[0]["n"] == 1


def test_events_persist_and_projections_match(tmp_path):
    store, eng, events = store_engine(tmp_path)
    run_engine(eng, 0, 400, lambda t: GUESTS)
    eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 400, table_id="T1", actor="maria"))
    run_engine(eng, 400, 700, lambda t: GUESTS if t < 500 else [])
    eng.act(OperatorAction(ActionType.CONFIRM_CLEANING, 700, table_id="T1"))
    rows = store.events("simulation", "run-1", ascending=True, limit=1000)
    assert [r["type"] for r in rows] == [e.type.value for e in events]
    [session] = store.sessions("simulation")
    assert session["status"] == "cleaned"
    assert session["wait_s"] == 400.0
    assert session["unserved_alert"] == 1
    assert session["seated_wall"] == 1_700_000_000.0
    assert session["occupied_s"] == 499.5
    alerts = store.alerts("simulation")
    assert {a["type"]: a["status"] for a in alerts} == {
        "UNSERVED_TOO_LONG": "RESOLVED",
        "TABLE_NEEDS_CLEANING": "RESOLVED",
    }


def test_duplicate_events_are_ignored(tmp_path):
    store, eng, events = store_engine(tmp_path)
    run_engine(eng, 0, 100, lambda t: GUESTS)
    n = len(store.events("simulation", "run-1", limit=1000))
    for ev in events:  # replaying the same events (e.g. a retry) changes nothing
        assert store.append_event(ev, "simulation", "run-1") is False
    clone = Event.from_dict({**events[0].to_dict(), "id": "different-id"})
    assert store.append_event(clone, "simulation", "run-1") is False  # same idempotency key
    assert len(store.events("simulation", "run-1", limit=1000)) == n
    assert len(store.sessions("simulation")) == 1


def test_state_reconstructed_from_sqlite_after_restart(tmp_path):
    store, eng, events = store_engine(tmp_path)
    run_engine(eng, 0, 400, lambda t: GUESTS + [person(11, 0.8, 0.5)])
    eng.act(OperatorAction(ActionType.CONFIRM_SERVICE, 400, table_id="T2"))
    before = {k: v.proj.to_dict() for k, v in eng.tables.items()}
    store.close()

    reopened = Store(tmp_path / "tw.db")
    restarted = make_engine()
    restarted.restore(reopened.run_events("simulation", "run-1"))
    assert {k: v.proj.to_dict() for k, v in restarted.tables.items()} == before
    assert {a.id for a in restarted.alerts.active()} == {a.id for a in eng.alerts.active()}


def test_rebuild_projections_from_events(tmp_path):
    store, eng, _ = store_engine(tmp_path)
    run_engine(eng, 0, 400, lambda t: GUESTS)
    eng.act(OperatorAction(ActionType.CORRECT_STATUS, 400, table_id="T1", to_state=TableState.NEEDS_CLEANING, reason="left"))
    sessions, alerts = store.sessions("simulation"), store.alerts("simulation")
    store.rebuild_projections()
    assert store.sessions("simulation") == sessions
    assert sorted(a["id"] for a in store.alerts("simulation")) == sorted(a["id"] for a in alerts)


def test_simulation_reset_never_touches_live_data(tmp_path):
    store = Store(tmp_path / "tw.db")
    store.create_run("live-1", "live", "camera", epoch_wall=0)
    store.create_run("sim-1", "simulation", "scenario", epoch_wall=0)
    live = make_engine()
    live.subscribe(lambda ev: store.append_event(ev, "live", "live-1"))
    sim = make_engine()
    sim.subscribe(lambda ev: store.append_event(ev, "simulation", "sim-1"))
    run_engine(live, 0, 100, lambda t: GUESTS)
    run_engine(sim, 0, 100, lambda t: GUESTS)
    live_n = len(store.events("live", limit=1000))
    assert live_n > 0
    store.delete_dataset("simulation")
    assert store.events("simulation", limit=10) == []
    assert len(store.events("live", limit=1000)) == live_n
    assert store.sessions("live")


def test_simulation_runs_are_kept_separate(tmp_path):
    store = Store(tmp_path / "tw.db")
    layout = load_layout()
    _, anchor, zones = load_zone_file(project_root() / "configs" / "zones" / "demo.json")
    sc = load_scenarios()["slow_cleaning"]
    for rid in ("a", "b"):
        store.create_run(rid, "simulation", "scenario", epoch_wall=sc.start_time.timestamp(), scenario=sc.id)
        s = SimSession(layout, sc, Thresholds(), zones, anchor, run_id=rid, store=store)
        with store.batch():
            s.run_headless(until=600)
    a = store.events("simulation", "a", limit=5000)
    b = store.events("simulation", "b", limit=5000)
    assert len(a) == len(b) > 0
    assert {r["session_id"] for r in a if r["session_id"]}.isdisjoint({r["session_id"] for r in b if r["session_id"]})
    store.delete_dataset("simulation", run_id="a")
    assert store.events("simulation", "a", limit=10) == []
    assert len(store.events("simulation", "b", limit=5000)) == len(b)


def test_config_versions_and_zones(tmp_path):
    store = Store(tmp_path / "tw.db")
    assert store.latest_config("thresholds") == (None, 0)
    store.save_config("thresholds", {"unserved_warning_s": 240})
    v = store.save_config("thresholds", {"unserved_warning_s": 200})
    assert store.latest_config("thresholds") == ({"unserved_warning_s": 200}, v)
    zones = [{"table_id": "T1", "polygon": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}]
    assert store.save_zones("cam", "centroid", zones) == 1
    assert store.save_zones("cam", "centroid", zones) == 2
    anchor, loaded, version = store.zones("cam")
    assert (anchor, loaded, version) == ("centroid", zones, 2)


def test_operator_actions_logged_once_per_request(tmp_path):
    store = Store(tmp_path / "tw.db")
    rec = {"request_id": "r1", "action": "CONFIRM_SERVICE", "table_id": "T1", "source": "operator", "t": 1, "wall": 1,
           "result": "ok"}
    store.log_action("simulation", "run", rec)
    store.log_action("simulation", "run", rec)
    assert len(store.actions("simulation")) == 1
