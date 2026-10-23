"""Full simulated restaurant session through the real app and SQLite.

Arrival → seating → waiting alert → service confirmation → departure →
cleaning alert → cleaning confirmation → available → analytics.
All in virtual time; no camera needed.
"""

import pytest
from fastapi.testclient import TestClient

from tablewatch.main import create_app
from tablewatch.storage.db import Store


@pytest.fixture()
def app_client(tmp_path):
    db = tmp_path / "it.db"
    app = create_app(db_path=db, run_loop=False, autoplay=False)
    with TestClient(app) as c:
        yield c, db


def test_full_lifecycle_persisted_and_analysed(app_client):
    c, db = app_client
    c.put("/api/settings/simulation", json={"staff_auto_confirm": False})  # the operator does everything
    run = c.post("/api/sim/start", json={"scenario": "guided", "autoplay": False}).json()
    assert run["sim"]["staff_auto_confirm"] is False

    def t6():
        return next(t for t in c.get("/api/state").json()["tables"] if t["table_id"] == "T6")

    # Arrival and seating.
    c.post("/api/sim/advance", json={"seconds": 60})
    assert t6()["state"] == "SEATED_UNSERVED"
    # Waiting alert at the threshold, exactly once.
    c.post("/api/sim/advance", json={"seconds": 280})
    table = t6()
    assert table["unserved_overdue"]

    def t6_alerts():
        return [a for a in c.get("/api/alerts?table_id=T6").json()["alerts"] if a["type"] == "UNSERVED_TOO_LONG"]

    assert len(t6_alerts()) == 1
    c.post("/api/sim/advance", json={"seconds": 30})
    assert len(t6_alerts()) == 1  # no duplicates while the guests keep waiting

    # Operator confirms service.
    r = c.post("/api/tables/T6/actions", json={"action": "confirm_service", "actor": "Host"})
    assert r.status_code == 200 and r.json()["table"]["state"] == "SERVED"

    # T1's party leaves; the table needs cleaning; nobody auto-confirms.
    c.post("/api/sim/advance", json={"seconds": 250})
    t1 = next(t for t in c.get("/api/state").json()["tables"] if t["table_id"] == "T1")
    assert t1["state"] == "NEEDS_CLEANING"
    cleaning = [a for a in c.get("/api/alerts?status=ACTIVE").json()["alerts"] if a["type"] == "TABLE_NEEDS_CLEANING"]
    assert any(a["table_id"] == "T1" for a in cleaning)

    # The busser visits but only a confirmation frees the table.
    c.post("/api/sim/advance", json={"seconds": 200})
    assert next(t for t in c.get("/api/state").json()["tables"] if t["table_id"] == "T1")["state"] == "NEEDS_CLEANING"
    r = c.post("/api/tables/T1/actions", json={"action": "confirm_cleaning", "actor": "Busser"})
    assert r.status_code == 200 and r.json()["table"]["state"] == "FREE"

    state = c.get("/api/state").json()
    assert all(g["done"] for g in state["guide"] if g["id"] in ("enter", "seated", "waiting", "alert", "served", "cleaning", "cleaned"))

    # Analytics come from the persisted history.
    a = c.get("/api/analytics").json()
    assert a["simulated"] and a["kpis"]["unserved_warnings"] >= 1
    assert a["kpis"]["service_confirmation_sources"]["operator"] >= 1
    assert a["kpis"]["cleaning_turnaround"]["count"] == 1
    assert a["kpis"]["completed_sessions"] >= 1

    # SQLite events match the actual transitions, in order, for every table.
    run_id = state["run"]["id"]
    store = Store(db)
    events = store.run_events("simulation", run_id)
    engine = c.app.state.runtime.engine
    for tid, sm in engine.tables.items():
        transitions = [e for e in events if e.table_id == tid and e.to_state is not None]
        expected_final = transitions[-1].to_state.value if transitions else "FREE"
        assert expected_final == sm.state.value, tid
        # Each transition starts where the previous ended.
        for prev, nxt in zip(transitions, transitions[1:]):
            assert prev.to_state == nxt.from_state, (tid, prev.type, nxt.type)
    t6_types = [e.type.value for e in events if e.table_id == "T6" and e.to_state is not None]
    assert t6_types == ["GUESTS_SEATED", "SERVICE_CONFIRMED"]
    t1_types = [e.type.value for e in events if e.table_id == "T1" and e.to_state is not None]
    assert t1_types == ["GUESTS_SEATED", "GUESTS_DEPARTED", "CLEANING_CONFIRMED"]
    sessions = {s["table_id"]: s for s in store.sessions("simulation", [run_id])}
    assert sessions["T1"]["status"] == "cleaned" and sessions["T1"]["cleaning_source"] == "operator"
    assert sessions["T6"]["service_source"] == "operator" and sessions["T6"]["unserved_alert"] == 1
    actions = store.actions("simulation", run_id)
    assert {a["action"] for a in actions} >= {"CONFIRM_SERVICE", "CONFIRM_CLEANING"}
    store.close()


def test_restart_restores_nothing_from_other_runs(app_client, tmp_path):
    """A new app on the same database starts a fresh simulation run and keeps old ones."""
    c, db = app_client
    c.post("/api/sim/advance", json={"seconds": 200})
    first_run = c.get("/api/state").json()["run"]["id"]
    app2 = create_app(db_path=db, run_loop=False, autoplay=False)
    with TestClient(app2) as c2:
        s = c2.get("/api/state").json()
        assert s["run"]["id"] != first_run
        assert s["summary"]["occupied"] == 0
        runs = {r["id"] for r in c2.get("/api/runs?dataset=simulation").json()["runs"]}
        assert first_run in runs
