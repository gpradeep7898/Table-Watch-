"""Operational analytics computed only from persisted events and sessions.

Rules that keep these numbers honest:
* Simulation, recorded and live datasets are analysed separately.
* Time when a camera was offline is removed from both the numerator and
  the denominator of occupancy. Unknown is not the same as empty.
* "Occupied" comes from observations. "Served" only counts confirmed
  service. The two are reported separately.
* Small samples are flagged instead of being dressed up.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

Interval = tuple[float, float]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MIN_SESSIONS = 3


def _subtract(interval: Interval, holes: list[Interval]) -> list[Interval]:
    parts = [interval]
    for hs, he in holes:
        nxt = []
        for s, e in parts:
            if he <= s or hs >= e:
                nxt.append((s, e))
                continue
            if hs > s:
                nxt.append((s, hs))
            if he < e:
                nxt.append((he, e))
        parts = nxt
    return [(s, e) for s, e in parts if e > s]


def _split_hours(s: float, e: float) -> Iterable[tuple[int, int, float]]:
    """Yield (weekday, hour, seconds) pieces of [s, e) in local time."""
    cur = s
    while cur < e:
        dt = datetime.fromtimestamp(cur)
        nxt_dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        nxt = min(e, nxt_dt.timestamp())
        if nxt <= cur:  # DST edge; move on by a minute
            nxt = min(e, cur + 60)
        yield dt.weekday(), dt.hour, nxt - cur
        cur = nxt


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean_s": None, "median_s": None, "p90_s": None, "max_s": None}
    vs = sorted(values)
    return {
        "count": len(vs),
        "mean_s": round(statistics.fmean(vs), 1),
        "median_s": round(statistics.median(vs), 1),
        "p90_s": round(vs[min(len(vs) - 1, int(math.ceil(0.9 * len(vs))) - 1)], 1),
        "max_s": round(vs[-1], 1),
    }


def _histogram(values: list[float], edges_min: list[float]) -> list[dict[str, Any]]:
    edges = [e * 60 for e in edges_min]
    out = []
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        label = f"{edges_min[i]:g}-{edges_min[i + 1]:g} min" if hi is not None else f"{edges_min[i]:g}+ min"
        out.append({"bucket": label, "count": sum(1 for v in values if v >= lo and (hi is None or v < hi))})
    return out


def compute(
    dataset: str,
    runs: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    table_ids: list[str],
    tables_per_waiter: float = 4.0,
) -> dict[str, Any]:
    n_tables = len(table_ids)
    runs = [r for r in runs if r.get("last_wall") is not None and r["last_wall"] > r["epoch_wall"]]
    run_ids = {r["id"] for r in runs}
    sessions = [s for s in sessions if s["run_id"] in run_ids]
    alerts = [a for a in alerts if a["run_id"] in run_ids]
    events = [e for e in events if e["run_id"] in run_ids]

    # Observation windows and camera outages, per run.
    windows: dict[str, Interval] = {r["id"]: (r["epoch_wall"], r["last_wall"]) for r in runs}
    outages: dict[str, list[Interval]] = defaultdict(list)
    open_outage: dict[tuple[str, str], float] = {}
    for e in sorted(events, key=lambda x: x["wall"]):
        key = (e["run_id"], e.get("camera_id") or "")
        if e["type"] == "CAMERA_OFFLINE":
            last = (e.get("payload") or {}).get("last_frame_t")
            start = e["wall"] - (e["t"] - last) if last is not None else e["wall"]
            open_outage[key] = start
        elif e["type"] == "CAMERA_ONLINE" and key in open_outage:
            outages[e["run_id"]].append((open_outage.pop(key), e["wall"]))
    for (rid, _), start in open_outage.items():
        outages[rid].append((start, windows[rid][1]))

    observed_s = 0.0
    window_s = 0.0
    obs_hour: dict[tuple[int, int], float] = defaultdict(float)
    for rid, (ws, we) in windows.items():
        window_s += (we - ws) * n_tables
        for s, e in _subtract((ws, we), outages[rid]):
            observed_s += (e - s) * n_tables
            for wd, hr, secs in _split_hours(s, e):
                obs_hour[(wd, hr)] += secs * n_tables

    occ_hour: dict[tuple[int, int], float] = defaultdict(float)
    occupied_s = 0.0
    timeline: list[tuple[float, int]] = []
    for s in sessions:
        ws, we = windows[s["run_id"]]
        end = s["left_wall"] if s["left_wall"] is not None else we
        start, end = max(ws, s["seated_wall"]), min(we, end)
        if end <= start:
            continue
        timeline += [(start, 1), (end, -1)]
        for a, b in _subtract((start, end), outages[s["run_id"]]):
            occupied_s += b - a
            for wd, hr, secs in _split_hours(a, b):
                occ_hour[(wd, hr)] += secs

    # Peak simultaneous occupancy (overall and per hour of day).
    peak, peak_at, cur = 0, None, 0
    peak_by_hour: dict[int, int] = defaultdict(int)
    peak_by_cell: dict[tuple[int, int], int] = defaultdict(int)
    for wall, delta in sorted(timeline, key=lambda x: (x[0], x[1])):
        cur += delta
        dt = datetime.fromtimestamp(wall)
        peak_by_hour[dt.hour] = max(peak_by_hour[dt.hour], cur)
        peak_by_cell[(dt.weekday(), dt.hour)] = max(peak_by_cell[(dt.weekday(), dt.hour)], cur)
        if cur > peak:
            peak, peak_at = cur, wall

    completed = [s for s in sessions if s["left_wall"] is not None]
    waits = [s["wait_s"] for s in sessions if s["wait_s"] is not None]
    durations = [s["occupied_s"] for s in completed if s["occupied_s"] is not None]
    cleanings = [s["cleaning_s"] for s in sessions if s["cleaning_s"] is not None]
    turnovers = [s["turnover_s"] for s in sessions if s["turnover_s"] is not None]

    def rate(occ: float, obs: float) -> float | None:
        return round(occ / obs, 4) if obs > 0 else None

    hours = sorted({hr for _, hr in obs_hour})
    by_hour = []
    for hr in hours:
        o = sum(v for (wd, h), v in obs_hour.items() if h == hr)
        c = sum(v for (wd, h), v in occ_hour.items() if h == hr)
        by_hour.append({"hour": hr, "label": f"{hr:02d}:00", "occupancy": rate(c, o), "observed_table_hours": round(o / 3600, 2),
                        "peak_occupied": peak_by_hour.get(hr, 0)})
    by_weekday = []
    for wd in range(7):
        o = sum(v for (d, h), v in obs_hour.items() if d == wd)
        c = sum(v for (d, h), v in occ_hour.items() if d == wd)
        by_weekday.append({"weekday": WEEKDAYS[wd], "occupancy": rate(c, o), "observed_table_hours": round(o / 3600, 2)})
    heatmap = [
        {"weekday": WEEKDAYS[wd], "hour": hr, "occupancy": rate(occ_hour.get((wd, hr), 0.0), obs),
         "peak_occupied": peak_by_cell.get((wd, hr), 0)}
        for (wd, hr), obs in sorted(obs_hour.items())
    ]

    per_table = []
    for tid in table_ids:
        ts = [s for s in sessions if s["table_id"] == tid]
        done = [s for s in ts if s["occupied_s"] is not None]
        per_table.append({
            "table_id": tid,
            "sessions": len(ts),
            "avg_occupied_min": round(statistics.fmean(s["occupied_s"] for s in done) / 60, 1) if done else None,
            "avg_turnover_min": round(statistics.fmean(s["turnover_s"] for s in ts if s["turnover_s"] is not None) / 60, 1)
            if any(s["turnover_s"] is not None for s in ts) else None,
            "unserved_alerts": sum(s["unserved_alert"] for s in ts),
        })

    alert_types = sorted({a["type"] for a in alerts})
    alert_buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in alerts:
        label = datetime.fromtimestamp(a["created_wall"]).strftime("%a %H:00")
        alert_buckets[(a["created_wall"] // 3600, label)][a["type"]] += 1  # type: ignore[index]
    alert_series = [{"bucket": label, **counts} for (_, label), counts in sorted(alert_buckets.items())]

    corrections = [e for e in events if e["type"] == "STATUS_CORRECTED"]
    visits = [e for e in events if e["type"] == "POSSIBLE_STAFF_VISIT"]
    service_sources = defaultdict(int)
    for s in sessions:
        if s["service_source"]:
            service_sources[s["service_source"]] += 1

    ratio = max(0.5, float(tables_per_waiter))
    staffing_by_hour = [
        {"hour": h["hour"], "label": h["label"], "peak_occupied": h["peak_occupied"],
         "estimated_servers": math.ceil(h["peak_occupied"] / ratio) if h["peak_occupied"] else 0}
        for h in by_hour
    ]
    total_hours = window_s / max(1, n_tables) / 3600
    return {
        "dataset": dataset,
        "simulated": dataset == "simulation",
        "runs": [{"id": r["id"], "label": r.get("label"), "kind": r.get("kind"), "start": datetime.fromtimestamp(r["epoch_wall"]).isoformat(timespec="minutes"),
                  "hours": round((r["last_wall"] - r["epoch_wall"]) / 3600, 2)} for r in runs],
        "period_hours": round(total_hours, 2),
        "tables": n_tables,
        "insufficient_data": len(completed) < MIN_SESSIONS,
        "kpis": {
            "occupancy_rate": rate(occupied_s, observed_s),
            "occupied_table_minutes": round(occupied_s / 60, 1),
            "observed_table_minutes": round(observed_s / 60, 1),
            "sessions": len(sessions),
            "completed_sessions": len(completed),
            "sessions_with_confirmed_service": len(waits),
            "service_response": _stats(waits),
            "unserved_warnings": sum(1 for a in alerts if a["type"] == "UNSERVED_TOO_LONG"),
            "avg_occupied_session_s": _stats(durations)["mean_s"],
            "occupied_session": _stats(durations),
            "cleaning_turnaround": _stats(cleanings),
            "table_turnover": _stats(turnovers),
            "overdue_cleanings": sum(1 for a in alerts if a["type"] == "CLEANING_OVERDUE"),
            "peak_occupied_tables": peak,
            "peak_at": datetime.fromtimestamp(peak_at).isoformat(timespec="minutes") if peak_at else None,
            "camera_uptime": rate(observed_s, window_s),
            "camera_outages": sum(len(v) for v in outages.values()),
            "manual_corrections": len(corrections),
            "possible_staff_visits": len(visits),
            "service_confirmation_sources": dict(service_sources),
        },
        "charts": {
            "occupancy_by_hour": by_hour,
            "occupancy_by_weekday": by_weekday,
            "heatmap": heatmap,
            "service_response_hist": _histogram(waits, [0, 1, 2, 3, 5, 8, 12]),
            "cleaning_hist": _histogram(cleanings, [0, 2, 4, 6, 8, 12]),
            "turnover_by_table": per_table,
            "alerts_over_time": alert_series,
            "alert_types": alert_types,
        },
        "staffing": {
            "tables_per_server": ratio,
            "peak_occupied_tables": peak,
            "estimated_servers_at_peak": math.ceil(peak / ratio) if peak else 0,
            "by_hour": staffing_by_hour,
            "disclaimer": "Illustrative planning heuristic: peak simultaneous occupied tables divided by your "
                          "tables-per-server ratio. Not a validated staffing recommendation.",
        },
    }
