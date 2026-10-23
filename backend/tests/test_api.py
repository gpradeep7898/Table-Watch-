"""HTTP and WebSocket contracts against the real app (simulation mode)."""

import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from tablewatch.main import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=tmp_path / "api.db", run_loop=False, autoplay=False)
    with TestClient(app) as c:
        yield c


def advance(c, seconds):
    r = c.post("/api/sim/advance", json={"seconds": seconds})
    assert r.status_code == 200, r.text
    return r.json()["sim"]


def table(c, tid):
    return next(t for t in c.get("/api/state").json()["tables"] if t["table_id"] == tid)


def start(c, scenario, **kw):
    r = c.post("/api/sim/start", json={"scenario": scenario, "autoplay": False, **kw})
    assert r.status_code == 200, r.text
    return r.json()["sim"]


def test_state_contract(client):
    s = client.get("/api/state").json()
    assert s["mode"] == "simulation" and s["dataset"] == "simulation"
    assert s["clock"]["simulated"] is True
    assert s["restaurant"]["name"] == "TableWatch Demo Bistro"
    assert len(s["tables"]) == 10
    assert set(s["summary"]) >= {"total", "free", "occupied", "awaiting_service", "needs_cleaning", "active_alerts"}
    t = s["tables"][0]
    assert set(t) >= {"table_id", "label", "state", "wait_s", "occupied_s", "cleaning_s", "health", "alerts", "uncertainty"}
    assert [g["id"] for g in s["guide"]][:2] == ["enter", "seated"]
    assert s["cameras"][0]["detector"].startswith("None: synthetic")


def test_layout_and_scenarios(client):
    lay = client.get("/api/layout").json()
    assert len(lay["tables"]) == 10 and lay["sim_camera"]["zones"]
    sc = client.get("/api/sim/scenarios").json()["scenarios"]
    assert {s["id"] for s in sc} >= {"normal", "forgotten", "slow_cleaning", "rush", "tracking", "guided"}


def test_sim_controls(client):
    st = start(client, "rush")
    assert st["scenario"]["id"] == "rush" and st["paused"]
    st = client.post("/api/sim/control", json={"action": "speed", "speed": 10}).json()["sim"]
    assert st["speed"] == 10 and not st["paused"]
    st = client.post("/api/sim/control", json={"action": "pause"}).json()["sim"]
    assert st["paused"]
    r = client.post("/api/sim/control", json={"action": "speed", "speed": 3})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "bad_speed"
    assert client.post("/api/sim/start", json={"scenario": "nope"}).status_code == 400


def test_reset_is_reproducible(client):
    start(client, "normal")
    advance(client, 300)
    first = [(e["type"], e["t"], e["table_id"]) for e in client.get("/api/events?limit=500").json()["events"]]
    start(client, "normal")
    advance(client, 300)
    second = [(e["type"], e["t"], e["table_id"]) for e in client.get("/api/events?limit=500").json()["events"]]
    assert first == second and first
    runs = client.get("/api/runs?dataset=simulation").json()["runs"]
    assert len(runs) == 3  # startup run + two resets, all kept separately


def test_operator_flow_service_and_cleaning(client):
    start(client, "forgotten")
    advance(client, 340)
    t4 = table(client, "T4")
    assert t4["state"] == "SEATED_UNSERVED" and t4["unserved_overdue"]
    [alert] = [a for a in t4["alerts"] if a["type"] == "UNSERVED_TOO_LONG"]

    r = client.post(f"/api/alerts/{alert['id']}/acknowledge", json={"actor": "Maria"})
    assert r.status_code == 200 and r.json()["alert"]["status"] == "ACKNOWLEDGED"
    assert client.post(f"/api/alerts/{alert['id']}/acknowledge", json={}).status_code == 409

    r = client.post("/api/tables/T4/actions", json={"action": "confirm_service", "actor": "Maria", "request_id": "x1"})
    assert r.status_code == 200
    assert r.json()["table"]["state"] == "SERVED"
    assert [e["type"] for e in r.json()["events"]] == ["SERVICE_CONFIRMED", "ALERT_RESOLVED"]
    # A retried request is idempotent; a new request is rejected.
    assert client.post("/api/tables/T4/actions", json={"action": "confirm_service", "request_id": "x1"}).status_code == 200
    r = client.post("/api/tables/T4/actions", json={"action": "confirm_service"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_confirmed"

    alerts = client.get("/api/alerts?status=RESOLVED").json()["alerts"]
    assert any(a["id"] == alert["id"] and a["resolution"] == "service_confirmed" for a in alerts)
    detail = client.get("/api/tables/T4").json()
    assert detail["events"][0]["source"] in ("operator", "system")


def test_invalid_actions_rejected(client):
    r = client.post("/api/tables/T1/actions", json={"action": "confirm_cleaning"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invalid_state"
    r = client.post("/api/tables/T99/actions", json={"action": "confirm_service"})
    assert r.status_code == 404
    r = client.post("/api/tables/T1/actions", json={"action": "correct_status", "to_state": "SEATED_UNSERVED"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "reason_required"
    r = client.post("/api/tables/T1/actions", json={"action": "explode"})
    assert r.status_code == 422
    r = client.post("/api/tables/T1/actions", json={"action": "correct_status", "to_state": "SEATED_UNSERVED", "reason": "walk-in"})
    assert r.status_code == 200 and r.json()["table"]["state"] == "SEATED_UNSERVED"
    r = client.post("/api/tables/T1/actions", json={"action": "correct_status", "to_state": "FREE", "reason": "mistake"})
    assert r.status_code == 200
    events = client.get("/api/events?table_id=T1").json()["events"]
    assert [e["type"] for e in events][:2] == ["STATUS_CORRECTED", "STATUS_CORRECTED"]


def test_cleaning_flow_via_api(client):
    start(client, "slow_cleaning")
    advance(client, 700)
    t5 = table(client, "T5")
    assert t5["state"] == "NEEDS_CLEANING" and t5["cleaning_overdue"]
    r = client.post("/api/tables/T5/actions", json={"action": "confirm_cleaning", "actor": "Sam"})
    assert r.status_code == 200 and r.json()["table"]["state"] == "FREE"
    types = [e["type"] for e in r.json()["events"]]
    assert types[0] == "CLEANING_CONFIRMED" and types.count("ALERT_RESOLVED") == 2


def test_settings_validation_and_persistence(client):
    s = client.get("/api/settings").json()
    assert s["thresholds"]["unserved_warning_s"] == 300
    assert s["bind_host"] == "127.0.0.1"
    r = client.put("/api/settings/thresholds", json={"unserved_warning_s": 5})
    assert r.status_code == 400 and "between" in r.json()["detail"]["message"]
    r = client.put("/api/settings/thresholds", json={"min_visit_s": 500, "max_visit_s": 100})
    assert r.status_code == 400
    r = client.put("/api/settings/thresholds", json={"rm -rf": 1})
    assert r.status_code == 400
    r = client.put("/api/settings/thresholds", json={"unserved_warning_s": 120})
    assert r.status_code == 200 and r.json()["version"] >= 1
    assert client.get("/api/state").json()["thresholds"]["unserved_warning_s"] == 120
    assert client.get("/api/settings").json()["thresholds"]["unserved_warning_s"] == 120
    # New threshold applies to the running engine.
    start(client, "forgotten")
    advance(client, 200)
    assert table(client, "T4")["unserved_overdue"]
    assert client.post("/api/settings/thresholds/reset").json()["thresholds"]["unserved_warning_s"] == 300
    assert client.put("/api/settings/simulation", json={"speed": 7}).status_code == 400
    assert client.put("/api/settings/simulation", json={"default_scenario": "rush", "speed": 10}).status_code == 200
    assert client.put("/api/settings/privacy", json={"event_retention_days": 0}).status_code == 400
    assert client.put("/api/settings/privacy", json={"event_retention_days": 30}).json()["privacy"]["event_retention_days"] == 30


def test_zones_validate_save_reload_and_test(client):
    z = client.get("/api/zones/sim-cam-1").json()
    assert len(z["zones"]) == 10 and z["anchor"] == "centroid"
    bad = {"anchor": "centroid", "zones": [{"table_id": "T1", "polygon": [[0.1, 0.1], [0.2, 0.2]]}]}
    r = client.post("/api/zones/sim-cam-1/validate", json=bad).json()
    assert not r["ok"] and r["errors"]
    assert client.put("/api/zones/sim-cam-1", json=bad).status_code == 400
    assert client.put("/api/zones/nope", json=z | {"zones": z["zones"]}).status_code == 400
    edited = [dict(x) for x in z["zones"]]
    edited[0] = {"table_id": "T1", "polygon": [[0.05, 0.05], [0.25, 0.05], [0.25, 0.3], [0.05, 0.3]]}
    r = client.put("/api/zones/sim-cam-1", json={"anchor": "centroid", "zones": edited})
    assert r.status_code == 200 and r.json()["version"] == 1
    again = client.get("/api/zones/sim-cam-1").json()
    assert again["version"] == 1 and again["zones"][0]["polygon"][2] == [0.25, 0.3]
    advance(client, 60)
    test = client.post("/api/zones/sim-cam-1/test", json={"anchor": "centroid", "zones": edited}).json()
    assert test["tracks"] > 0 and "per_table" in test
    r = client.post("/api/zones/sim-cam-1/reset-demo")
    assert r.status_code == 200
    assert client.get("/api/zones/sim-cam-1").json()["zones"] == z["zones"]


def test_analytics_and_exports(client):
    start(client, "normal")
    advance(client, 1700)
    a = client.get("/api/analytics?tables_per_waiter=3").json()
    assert a["simulated"] is True and a["dataset"] == "simulation"
    k = a["kpis"]
    assert k["completed_sessions"] == 6 and k["sessions_with_confirmed_service"] == 6
    assert 0 < k["occupancy_rate"] < 1
    assert k["camera_uptime"] == 1.0
    assert a["staffing"]["tables_per_server"] == 3
    assert a["staffing"]["estimated_servers_at_peak"] == -(-k["peak_occupied_tables"] // 3)
    assert a["charts"]["occupancy_by_hour"] and a["charts"]["service_response_hist"]
    for kind in ("events", "sessions", "alerts"):
        r = client.get(f"/api/export/{kind}.csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert "SIMULATED DATA" in r.text.splitlines()[0]
    live = client.get("/api/analytics?dataset=live").json()
    assert live["insufficient_data"] and live["kpis"]["sessions"] == 0


def test_camera_registration_is_validated(client, tmp_path):
    r = client.post("/api/cameras", json={"id": "cam1", "name": "x", "kind": "rtsp", "url": "http://evil", "authorized": True})
    assert r.status_code == 400
    r = client.post("/api/cameras", json={"id": "cam1", "name": "x", "kind": "rtsp", "url": "rtsp://user:secret@10.0.0.5/s"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "not_authorized"
    r = client.post("/api/cameras", json={"id": "cam1", "name": "Dining", "kind": "rtsp",
                                          "url": "rtsp://user:secret@10.0.0.5:554/stream", "authorized": True})
    assert r.status_code == 200
    listed = client.get("/api/cameras").json()["cameras"]
    cam = next(c for c in listed if c["id"] == "cam1")
    assert "secret" not in str(cam) and cam["config"]["url"].startswith("rtsp://***@")
    r = client.post("/api/cameras", json={"id": "vid", "name": "v", "kind": "file", "path": "../../etc/passwd.mp4", "authorized": True})
    assert r.status_code == 400
    r = client.post("/api/cameras", json={"id": "Bad Id", "name": "v", "kind": "webcam", "authorized": True})
    assert r.status_code == 422
    assert client.delete("/api/cameras/sim-cam-1").status_code == 400
    assert client.delete("/api/cameras/cam1").status_code == 200


def test_cv_mode_errors_are_explicit(client):
    r = client.post("/api/mode", json={"mode": "recorded", "camera_ids": ["sim-cam-1"]})
    assert r.status_code == 409
    assert client.get("/api/state").json()["mode"] == "simulation"
    assert client.post("/api/video/control", json={"action": "play"}).status_code == 409


def test_data_deletion_requires_confirmation_and_protects_active_run(client):
    assert client.post("/api/data/delete", json={"dataset": "live", "confirm": "yes"}).status_code == 400
    r = client.post("/api/data/delete", json={"dataset": "simulation", "confirm": "DELETE simulation"})
    assert r.status_code == 409  # the active simulation run is in use
    assert client.post("/api/data/delete", json={"dataset": "live", "confirm": "DELETE live"}).status_code == 200


def test_websocket_streams_snapshot_frames_and_events(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and len(snap["tables"]) == 10
        frames = ws.receive_json()
        assert frames["type"] == "frames"
        client.post("/api/tables/T2/actions", json={"action": "correct_status", "to_state": "SEATED_UNSERVED", "reason": "walk-in"})
        msg = ws.receive_json()
        while msg["type"] == "reset":  # startup notice still queued from app creation
            msg = ws.receive_json()
        assert msg["type"] == "event" and msg["event"]["type"] == "STATUS_CORRECTED"
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert next(t for t in snap["tables"] if t["table_id"] == "T2")["state"] == "SEATED_UNSERVED"


def test_websocket_rejects_foreign_origin(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_json()


def test_host_header_and_cors(client):
    assert client.get("/api/health", headers={"host": "attacker.example"}).status_code == 400
    r = client.options("/api/state", headers={"origin": "http://evil.example", "access-control-request-method": "GET"})
    assert "access-control-allow-origin" not in r.headers
    r = client.options("/api/state", headers={"origin": "http://127.0.0.1:5173", "access-control-request-method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
