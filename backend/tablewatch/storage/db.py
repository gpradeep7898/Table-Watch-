"""SQLite persistence: append-only events plus read projections.

* ``events`` is the source of truth. Inserts are idempotent per
  (dataset, run_id, key).
* ``table_sessions`` and ``alerts`` are projections, updated from events as
  they are appended, and rebuildable with :meth:`Store.rebuild_projections`.
* Simulation, recorded-video and live data are kept apart by ``dataset``
  and ``run_id``. Nothing here deletes live data unless a caller passes
  ``dataset="live"`` explicitly.
* No video, images, or per-frame tracks are ever stored.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core.models import Event, EventType

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE restaurants (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_wall REAL NOT NULL
    );
    CREATE TABLE cameras (
        id TEXT PRIMARY KEY,
        restaurant_id TEXT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        config_json TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_wall REAL NOT NULL,
        updated_wall REAL NOT NULL
    );
    CREATE TABLE table_zones (
        camera_id TEXT NOT NULL,
        table_id TEXT NOT NULL,
        polygon_json TEXT NOT NULL,
        anchor TEXT NOT NULL,
        version INTEGER NOT NULL,
        updated_wall REAL NOT NULL,
        PRIMARY KEY (camera_id, table_id)
    );
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        dataset TEXT NOT NULL CHECK (dataset IN ('simulation','recorded','live')),
        kind TEXT NOT NULL,
        label TEXT,
        scenario TEXT,
        seed INTEGER,
        epoch_wall REAL NOT NULL,
        started_wall REAL NOT NULL,
        last_t REAL NOT NULL DEFAULT 0,
        last_wall REAL,
        ended_wall REAL,
        status TEXT NOT NULL,
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE VIEW simulation_runs AS SELECT * FROM runs WHERE dataset = 'simulation';
    CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        dataset TEXT NOT NULL,
        run_id TEXT NOT NULL,
        engine_seq INTEGER,
        key TEXT NOT NULL,
        type TEXT NOT NULL,
        t REAL NOT NULL,
        wall REAL NOT NULL,
        table_id TEXT,
        camera_id TEXT,
        session_id TEXT,
        source TEXT NOT NULL,
        from_state TEXT,
        to_state TEXT,
        payload_json TEXT NOT NULL,
        UNIQUE (dataset, run_id, key)
    );
    CREATE INDEX events_run ON events (dataset, run_id, seq);
    CREATE INDEX events_table ON events (dataset, table_id, seq);
    CREATE TABLE table_sessions (
        session_id TEXT PRIMARY KEY,
        dataset TEXT NOT NULL,
        run_id TEXT NOT NULL,
        table_id TEXT NOT NULL,
        camera_id TEXT,
        status TEXT NOT NULL,
        seated_wall REAL NOT NULL,
        seated_source TEXT,
        confirmed_wall REAL,
        guest_count INTEGER,
        evidence_quality REAL,
        service_wall REAL,
        service_source TEXT,
        wait_s REAL,
        left_wall REAL,
        occupied_s REAL,
        cleaning_required_wall REAL,
        cleaned_wall REAL,
        cleaning_source TEXT,
        cleaning_s REAL,
        turnover_s REAL,
        visits INTEGER NOT NULL DEFAULT 0,
        corrected INTEGER NOT NULL DEFAULT 0,
        departed_during_gap INTEGER NOT NULL DEFAULT 0,
        unserved_alert INTEGER NOT NULL DEFAULT 0,
        cleaning_overdue INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX sessions_run ON table_sessions (dataset, run_id);
    CREATE TABLE alerts (
        id TEXT PRIMARY KEY,
        dataset TEXT NOT NULL,
        run_id TEXT NOT NULL,
        type TEXT NOT NULL,
        severity TEXT NOT NULL,
        status TEXT NOT NULL,
        dedup_key TEXT NOT NULL,
        table_id TEXT,
        camera_id TEXT,
        session_id TEXT,
        message TEXT,
        next_step TEXT,
        created_wall REAL NOT NULL,
        created_t REAL NOT NULL,
        acknowledged_wall REAL,
        acknowledged_by TEXT,
        resolved_wall REAL,
        resolution TEXT
    );
    CREATE INDEX alerts_run ON alerts (dataset, run_id, status);
    CREATE TABLE operator_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset TEXT NOT NULL,
        run_id TEXT NOT NULL,
        request_id TEXT,
        action TEXT NOT NULL,
        table_id TEXT,
        alert_id TEXT,
        source TEXT NOT NULL,
        actor TEXT,
        reason TEXT,
        t REAL NOT NULL,
        wall REAL NOT NULL,
        result TEXT NOT NULL,
        error TEXT,
        UNIQUE (dataset, run_id, request_id)
    );
    CREATE TABLE configuration_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        version INTEGER NOT NULL,
        data_json TEXT NOT NULL,
        created_wall REAL NOT NULL,
        created_by TEXT NOT NULL,
        UNIQUE (kind, version)
    );
    """,
]


def _j(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True)


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._batch = 0
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    # ------------------------------------------------------------------
    def migrate(self) -> int:
        """Apply pending migrations. Never drops data."""
        with self._lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
                self.conn.execute("BEGIN")
                try:
                    for stmt in sql.split(";"):
                        if stmt.strip():
                            self.conn.execute(stmt)
                    self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
            return len(MIGRATIONS)

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Group many writes into one transaction (used by fast simulations)."""
        with self._lock:
            if self._batch == 0:
                self.conn.execute("BEGIN")
            self._batch += 1
            try:
                yield
            except Exception:
                self._batch -= 1
                if self._batch == 0:
                    self.conn.execute("ROLLBACK")
                raise
            self._batch -= 1
            if self._batch == 0:
                self.conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(args))

    # ------------------------------------------------------------------
    # restaurants, cameras, zones
    # ------------------------------------------------------------------
    def ensure_restaurant(self, rid: str, name: str) -> None:
        self.x("INSERT OR IGNORE INTO restaurants (id, name, created_wall) VALUES (?,?,?)", (rid, name, time.time()))
        self.x("UPDATE restaurants SET name=? WHERE id=?", (name, rid))

    def upsert_camera(self, cid: str, restaurant_id: str, name: str, kind: str, config: dict[str, Any], enabled: bool = True) -> None:
        now = time.time()
        self.x(
            """INSERT INTO cameras (id, restaurant_id, name, kind, config_json, enabled, created_wall, updated_wall)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,
               config_json=excluded.config_json, enabled=excluded.enabled, updated_wall=excluded.updated_wall""",
            (cid, restaurant_id, name, kind, _j(config), int(enabled), now, now),
        )

    def cameras(self) -> list[dict[str, Any]]:
        rows = self.q("SELECT * FROM cameras ORDER BY created_wall")
        for r in rows:
            r["config"] = json.loads(r.pop("config_json"))
            r["enabled"] = bool(r["enabled"])
        return rows

    def delete_camera(self, cid: str) -> None:
        with self._lock:
            self.x("DELETE FROM cameras WHERE id=?", (cid,))
            self.x("DELETE FROM table_zones WHERE camera_id=?", (cid,))

    def save_zones(self, camera_id: str, anchor: str, zones: list[dict[str, Any]], actor: str = "operator") -> int:
        with self._lock:
            row = self.q("SELECT MAX(version) AS v FROM configuration_versions WHERE kind=?", (f"zones:{camera_id}",))
            version = (row[0]["v"] or 0) + 1
            now = time.time()
            own = self._batch == 0
            if own:
                self.conn.execute("BEGIN")
            try:
                self.conn.execute("DELETE FROM table_zones WHERE camera_id=?", (camera_id,))
                for z in zones:
                    self.conn.execute(
                        "INSERT INTO table_zones (camera_id, table_id, polygon_json, anchor, version, updated_wall) VALUES (?,?,?,?,?,?)",
                        (camera_id, z["table_id"], _j(z["polygon"]), anchor, version, now),
                    )
                self.conn.execute(
                    "INSERT INTO configuration_versions (kind, version, data_json, created_wall, created_by) VALUES (?,?,?,?,?)",
                    (f"zones:{camera_id}", version, _j({"anchor": anchor, "zones": zones}), now, actor),
                )
                if own:
                    self.conn.execute("COMMIT")
            except Exception:
                if own:
                    self.conn.execute("ROLLBACK")
                raise
            return version

    def zones(self, camera_id: str) -> tuple[str | None, list[dict[str, Any]], int]:
        rows = self.q("SELECT * FROM table_zones WHERE camera_id=? ORDER BY table_id", (camera_id,))
        if not rows:
            return None, [], 0
        return (
            rows[0]["anchor"],
            [{"table_id": r["table_id"], "polygon": json.loads(r["polygon_json"])} for r in rows],
            rows[0]["version"],
        )

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    def save_config(self, kind: str, data: dict[str, Any], actor: str = "operator") -> int:
        with self._lock:
            row = self.q("SELECT MAX(version) AS v FROM configuration_versions WHERE kind=?", (kind,))
            version = (row[0]["v"] or 0) + 1
            self.x(
                "INSERT INTO configuration_versions (kind, version, data_json, created_wall, created_by) VALUES (?,?,?,?,?)",
                (kind, version, _j(data), time.time(), actor),
            )
            return version

    def latest_config(self, kind: str) -> tuple[dict[str, Any] | None, int]:
        rows = self.q("SELECT * FROM configuration_versions WHERE kind=? ORDER BY version DESC LIMIT 1", (kind,))
        if not rows:
            return None, 0
        return json.loads(rows[0]["data_json"]), rows[0]["version"]

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------
    def create_run(self, run_id: str, dataset: str, kind: str, epoch_wall: float, label: str | None = None,
                   scenario: str | None = None, seed: int | None = None, meta: dict[str, Any] | None = None) -> None:
        self.x(
            """INSERT INTO runs (id, dataset, kind, label, scenario, seed, epoch_wall, started_wall, last_wall, status, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, dataset, kind, label, scenario, seed, epoch_wall, time.time(), epoch_wall, "running", _j(meta or {})),
        )

    def touch_run(self, run_id: str, t: float, wall: float) -> None:
        self.x("UPDATE runs SET last_t=?, last_wall=? WHERE id=? AND last_t<=?", (t, wall, run_id, t))

    def end_run(self, run_id: str, status: str = "ended") -> None:
        self.x("UPDATE runs SET status=?, ended_wall=? WHERE id=? AND ended_wall IS NULL", (status, time.time(), run_id))

    def runs(self, dataset: str | None = None, kind: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM runs WHERE 1=1", []
        if dataset:
            sql += " AND dataset=?"
            args.append(dataset)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        rows = self.q(sql + " ORDER BY started_wall DESC LIMIT ?", (*args, limit))
        for r in rows:
            r["meta"] = json.loads(r.pop("meta_json"))
        return rows

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self.q("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            return None
        rows[0]["meta"] = json.loads(rows[0].pop("meta_json"))
        return rows[0]

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def append_event(self, ev: Event, dataset: str, run_id: str) -> bool:
        """Insert an event and update projections. Returns False for duplicates."""
        with self._lock:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO events (id, dataset, run_id, engine_seq, key, type, t, wall, table_id, camera_id,
                   session_id, source, from_state, to_state, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ev.id, dataset, run_id, ev.seq, ev.key or f"{ev.type.value}:{ev.id}", ev.type.value, ev.t,
                    ev.wall if ev.wall is not None else ev.t, ev.table_id, ev.camera_id, ev.session_id,
                    ev.source.value, ev.from_state.value if ev.from_state else None,
                    ev.to_state.value if ev.to_state else None, _j(ev.payload),
                ),
            )
            if cur.rowcount == 0:
                return False
            self._project(ev, dataset, run_id)
            return True

    @staticmethod
    def _row_to_event(r: dict[str, Any]) -> dict[str, Any]:
        r["payload"] = json.loads(r.pop("payload_json"))
        return r

    def events(
        self,
        dataset: str | None = None,
        run_id: str | None = None,
        table_id: str | None = None,
        types: Iterable[str] | None = None,
        limit: int = 200,
        before_seq: int | None = None,
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        for col, val in (("dataset", dataset), ("run_id", run_id), ("table_id", table_id)):
            if val is not None:
                sql += f" AND {col}=?"
                args.append(val)
        types = list(types or [])
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            args.extend(types)
        if before_seq is not None:
            sql += " AND seq<?"
            args.append(before_seq)
        sql += f" ORDER BY seq {'ASC' if ascending else 'DESC'} LIMIT ?"
        args.append(limit)
        return [self._row_to_event(r) for r in self.q(sql, args)]

    def run_events(self, dataset: str, run_id: str) -> list[Event]:
        rows = self.q("SELECT * FROM events WHERE dataset=? AND run_id=? ORDER BY seq", (dataset, run_id))
        out = []
        for r in rows:
            r = self._row_to_event(r)
            out.append(Event.from_dict({**r, "seq": r["engine_seq"]}))
        return out

    # ------------------------------------------------------------------
    # projections
    # ------------------------------------------------------------------
    def _project(self, ev: Event, dataset: str, run_id: str) -> None:
        c = self.conn
        p, w = ev.payload, ev.wall if ev.wall is not None else ev.t
        offset = w - ev.t  # converts domain time to wall time for this run
        et = ev.type
        if et == EventType.GUESTS_SEATED:
            c.execute(
                """INSERT OR IGNORE INTO table_sessions (session_id, dataset, run_id, table_id, camera_id, status, seated_wall,
                   seated_source, confirmed_wall, guest_count, evidence_quality) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ev.session_id, dataset, run_id, ev.table_id, ev.camera_id, "open", p.get("seated_t", ev.t) + offset,
                 ev.source.value, w, p.get("guest_count"), p.get("evidence_quality")),
            )
        elif et == EventType.SERVICE_CONFIRMED:
            c.execute(
                "UPDATE table_sessions SET service_wall=?, service_source=?, wait_s=? WHERE session_id=?",
                (w, ev.source.value, p.get("wait_s"), ev.session_id),
            )
        elif et == EventType.GUESTS_DEPARTED:
            c.execute(
                """UPDATE table_sessions SET status='departed', left_wall=?, occupied_s=?, cleaning_required_wall=?,
                   departed_during_gap=? WHERE session_id=?""",
                (p.get("left_t", ev.t) + offset, p.get("occupied_s"), w, int(bool(p.get("during_observation_gap"))), ev.session_id),
            )
        elif et == EventType.CLEANING_CONFIRMED:
            c.execute(
                """UPDATE table_sessions SET status='cleaned', cleaned_wall=?, cleaning_source=?, cleaning_s=?, turnover_s=?
                   WHERE session_id=?""",
                (w, ev.source.value, p.get("cleaning_s"), p.get("turnover_s"), ev.session_id),
            )
        elif et == EventType.POSSIBLE_STAFF_VISIT and ev.session_id:
            c.execute("UPDATE table_sessions SET visits=visits+1 WHERE session_id=?", (ev.session_id,))
        elif et == EventType.STATUS_CORRECTED:
            frm, to = ev.from_state.value if ev.from_state else None, ev.to_state.value if ev.to_state else None
            prev = p.get("previous_session_id")
            if frm == "FREE":
                c.execute(
                    """INSERT OR IGNORE INTO table_sessions (session_id, dataset, run_id, table_id, camera_id, status, seated_wall,
                       seated_source, confirmed_wall, guest_count, corrected, service_wall, service_source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (ev.session_id, dataset, run_id, ev.table_id, ev.camera_id, "open", w, ev.source.value, w,
                     p.get("guest_count"), w if to == "SERVED" else None, ev.source.value if to == "SERVED" else None),
                )
            elif to == "FREE":
                c.execute(
                    """UPDATE table_sessions SET corrected=1,
                       status=CASE WHEN status='open' THEN 'closed_by_correction' WHEN status='departed' THEN 'freed_by_correction' ELSE status END,
                       left_wall=COALESCE(left_wall, ?),
                       occupied_s=COALESCE(occupied_s, ? - seated_wall) WHERE session_id=?""",
                    (w, w, prev),
                )
            elif to == "NEEDS_CLEANING":
                c.execute(
                    """UPDATE table_sessions SET corrected=1, status='departed', left_wall=?, cleaning_required_wall=?,
                       occupied_s=? - seated_wall WHERE session_id=?""",
                    (w, w, w, prev),
                )
            else:  # reopened after a false departure
                c.execute(
                    """UPDATE table_sessions SET corrected=1, status='open', left_wall=NULL, occupied_s=NULL,
                       cleaning_required_wall=NULL, departed_during_gap=0 WHERE session_id=?""",
                    (prev,),
                )
        elif et == EventType.ALERT_RAISED:
            a = p["alert"]
            c.execute(
                """INSERT OR IGNORE INTO alerts (id, dataset, run_id, type, severity, status, dedup_key, table_id, camera_id,
                   session_id, message, next_step, created_wall, created_t) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (a["id"], dataset, run_id, a["type"], a["severity"], a["status"], a["dedup_key"], a.get("table_id"),
                 a.get("camera_id"), a.get("session_id"), a.get("message"), p.get("next_step"), w, ev.t),
            )
            if a.get("session_id"):
                col = {"UNSERVED_TOO_LONG": "unserved_alert", "CLEANING_OVERDUE": "cleaning_overdue"}.get(a["type"])
                if col:
                    c.execute(f"UPDATE table_sessions SET {col}=1 WHERE session_id=?", (a["session_id"],))
        elif et == EventType.ALERT_ACKNOWLEDGED:
            c.execute(
                "UPDATE alerts SET status='ACKNOWLEDGED', acknowledged_wall=?, acknowledged_by=? WHERE id=? AND status='ACTIVE'",
                (w, p.get("actor"), p["alert_id"]),
            )
        elif et == EventType.ALERT_RESOLVED:
            c.execute(
                "UPDATE alerts SET status='RESOLVED', resolved_wall=?, resolution=? WHERE id=?",
                (w, p.get("resolution"), p["alert_id"]),
            )

    def rebuild_projections(self, dataset: str | None = None) -> int:
        """Recompute sessions and alerts from the event log."""
        with self.batch():
            where, args = ("WHERE dataset=?", (dataset,)) if dataset else ("", ())
            self.conn.execute(f"DELETE FROM table_sessions {where}", args)
            self.conn.execute(f"DELETE FROM alerts {where}", args)
            rows = self.q(f"SELECT * FROM events {where} ORDER BY seq", args)
            for r in rows:
                r = self._row_to_event(r)
                self._project(Event.from_dict({**r, "seq": r["engine_seq"]}), r["dataset"], r["run_id"])
            return len(rows)

    # ------------------------------------------------------------------
    def sessions(self, dataset: str, run_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM table_sessions WHERE dataset=?", [dataset]
        run_ids = list(run_ids) if run_ids is not None else None
        if run_ids is not None:
            if not run_ids:
                return []
            sql += f" AND run_id IN ({','.join('?' * len(run_ids))})"
            args += run_ids
        return self.q(sql + " ORDER BY seated_wall", args)

    def alerts(self, dataset: str | None = None, run_id: str | None = None, status: str | None = None,
               type_: str | None = None, table_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM alerts WHERE 1=1", []
        for col, val in (("dataset", dataset), ("run_id", run_id), ("status", status), ("type", type_), ("table_id", table_id)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        return self.q(sql + " ORDER BY created_wall DESC LIMIT ?", (*args, limit))

    def log_action(self, dataset: str, run_id: str, action: dict[str, Any]) -> None:
        self.x(
            """INSERT OR IGNORE INTO operator_actions (dataset, run_id, request_id, action, table_id, alert_id, source, actor,
               reason, t, wall, result, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (dataset, run_id, action.get("request_id"), action["action"], action.get("table_id"), action.get("alert_id"),
             action["source"], action.get("actor"), action.get("reason"), action["t"], action["wall"], action["result"],
             action.get("error")),
        )

    def actions(self, dataset: str, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM operator_actions WHERE dataset=?", [dataset]
        if run_id:
            sql += " AND run_id=?"
            args.append(run_id)
        return self.q(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))

    # ------------------------------------------------------------------
    # deletion and retention
    # ------------------------------------------------------------------
    def delete_dataset(self, dataset: str, run_id: str | None = None) -> int:
        """Delete operational data for one dataset (optionally one run)."""
        if dataset not in ("simulation", "recorded", "live"):
            raise ValueError("unknown dataset")
        with self.batch():
            where, args = "dataset=?", [dataset]
            if run_id:
                where += " AND run_id=?"
                args.append(run_id)
            n = self.conn.execute(f"DELETE FROM events WHERE {where}", args).rowcount
            for table in ("table_sessions", "alerts", "operator_actions"):
                self.conn.execute(f"DELETE FROM {table} WHERE {where}", args)
            run_where = "dataset=?" + (" AND id=?" if run_id else "")
            self.conn.execute(f"DELETE FROM runs WHERE {run_where}", args)
            return n

    def purge_older_than(self, days: float, datasets: Iterable[str] = ("live", "recorded")) -> int:
        """Retention: drop events and projections older than ``days`` (by wall time)."""
        cutoff = time.time() - days * 86400
        n = 0
        with self.batch():
            for ds in datasets:
                n += self.conn.execute("DELETE FROM events WHERE dataset=? AND wall<?", (ds, cutoff)).rowcount
                self.conn.execute("DELETE FROM table_sessions WHERE dataset=? AND seated_wall<?", (ds, cutoff))
                self.conn.execute("DELETE FROM alerts WHERE dataset=? AND created_wall<?", (ds, cutoff))
                self.conn.execute("DELETE FROM operator_actions WHERE dataset=? AND wall<?", (ds, cutoff))
        return n

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for ds in ("simulation", "recorded", "live"):
            out[ds] = {
                "events": self.q("SELECT COUNT(*) AS n FROM events WHERE dataset=?", (ds,))[0]["n"],
                "runs": self.q("SELECT COUNT(*) AS n FROM runs WHERE dataset=?", (ds,))[0]["n"],
                "sessions": self.q("SELECT COUNT(*) AS n FROM table_sessions WHERE dataset=?", (ds,))[0]["n"],
            }
        return out
