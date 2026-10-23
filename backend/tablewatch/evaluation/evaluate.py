"""Compare TableWatch events against hand-annotated ground truth.

Ground truth format ``tablewatch-groundtruth/v1`` (JSON)::

    {
      "format": "tablewatch-groundtruth/v1",
      "source": "who annotated what (e.g. 'manual annotation of lunch.mp4')",
      "events": [
        {"type": "seated",      "table": "T3", "t": 125.0},
        {"type": "departed",    "table": "T3", "t": 2410.5},
        {"type": "staff_visit", "table": "T3", "start": 300.0, "end": 330.0}
      ]
    }

``t`` is seconds from the start of the video (or simulation run). Predicted
events are TableWatch events (JSON lines, as written by ``analyze``).
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def load_predicted(path: Path) -> list[dict[str, Any]]:
    text = Path(path).read_text().strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _match(truth: list[tuple[str, float]], pred: list[tuple[str, float]], tol: float):
    """Greedy one-to-one matching on the same table within tolerance."""
    pairs = sorted(
        ((abs(pt - tt), i, j) for i, (ttab, tt) in enumerate(truth) for j, (ptab, pt) in enumerate(pred)
         if ttab == ptab and abs(pt - tt) <= tol),
    )
    used_t, used_p, matches = set(), set(), []
    for _, i, j in pairs:
        if i in used_t or j in used_p:
            continue
        used_t.add(i)
        used_p.add(j)
        matches.append((i, j))
    return matches, len(truth) - len(matches), len(pred) - len(matches)


def _pr(tp: int, fn: int, fp: int) -> dict[str, Any]:
    return {
        "true_positives": tp, "missed": fn, "false_positives": fp,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
    }


def _intervals(events: list[tuple[str, str, float]], end: float) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    open_: dict[str, float] = {}
    for kind, table, t in sorted(events, key=lambda e: e[2]):
        if kind == "start":
            open_.setdefault(table, t)
        elif table in open_:
            out.setdefault(table, []).append((open_.pop(table), t))
    for table, s in open_.items():
        out.setdefault(table, []).append((s, end))
    return out


def _occupied(ivs: list[tuple[float, float]], t: float) -> bool:
    return any(s <= t < e for s, e in ivs)


def evaluate(predicted: list[dict[str, Any]], truth: dict[str, Any], tolerance_s: float = 60.0) -> dict[str, Any]:
    if truth.get("format") != "tablewatch-groundtruth/v1":
        raise ValueError("Ground truth must use format tablewatch-groundtruth/v1")
    tev = truth["events"]
    t_seat = [(e["table"], float(e["t"])) for e in tev if e["type"] == "seated"]
    t_dep = [(e["table"], float(e["t"])) for e in tev if e["type"] == "departed"]
    t_vis = [(e["table"], float(e["start"]), float(e["end"])) for e in tev if e["type"] == "staff_visit"]

    p_seat_ev = [e for e in predicted if e["type"] == "GUESTS_SEATED"]
    p_dep_ev = [e for e in predicted if e["type"] == "GUESTS_DEPARTED"]
    p_seat = [(e["table_id"], float(e["payload"].get("seated_t", e["t"]))) for e in p_seat_ev]
    p_dep = [(e["table_id"], float(e["payload"].get("left_t", e["t"]))) for e in p_dep_ev]
    p_vis = [(e["table_id"], float(e["payload"]["arrived_t"]), float(e["payload"]["left_t"]))
             for e in predicted if e["type"] == "POSSIBLE_STAFF_VISIT"]

    sm, s_fn, s_fp = _match(t_seat, p_seat, tolerance_s)
    dm, d_fn, d_fp = _match(t_dep, p_dep, tolerance_s)
    seat_delay = [float(p_seat_ev[j]["t"]) - t_seat[i][1] for i, j in sm]
    dep_delay = [float(p_dep_ev[j]["t"]) - t_dep[i][1] for i, j in dm]

    # Staff visits: a truth visit is found if a predicted visit at the same table overlaps it.
    found = sum(1 for tab, s, e in t_vis if any(pt == tab and ps < e and pe > s for pt, ps, pe in p_vis))
    true_pred = sum(1 for tab, s, e in p_vis if any(tt == tab and ts < e and te > s for tt, ts, te in t_vis))

    # Per-second occupancy agreement across all tables seen in either set.
    end = max([t for _, t in t_seat + t_dep + p_seat + p_dep] + [0.0]) + 1
    t_iv = _intervals([("start", tab, t) for tab, t in t_seat] + [("end", tab, t) for tab, t in t_dep], end)
    p_iv = _intervals([("start", tab, t) for tab, t in p_seat] + [("end", tab, t) for tab, t in p_dep], end)
    tables = sorted(set(t_iv) | set(p_iv) | {tab for tab, _ in t_seat})
    agree = total = 0
    step = 1.0
    for tab in tables:
        t = 0.0
        while t < end:
            total += 1
            agree += _occupied(t_iv.get(tab, []), t) == _occupied(p_iv.get(tab, []), t)
            t += step
    corrections = sum(1 for e in predicted if e["type"] == "STATUS_CORRECTED")
    sessions = len(p_seat_ev) + sum(1 for e in predicted if e["type"] == "STATUS_CORRECTED" and e.get("from_state") == "FREE")
    cleanings = [e for e in predicted if e["type"] == "CLEANING_CONFIRMED"]

    def med(v: list[float]) -> float | None:
        return round(statistics.median(v), 1) if v else None

    return {
        "truth_source": truth.get("source"),
        "tolerance_s": tolerance_s,
        "seating": {**_pr(len(sm), s_fn, s_fp), "median_detection_delay_s": med(seat_delay)},
        "departure": {**_pr(len(dm), d_fn, d_fp), "false_departures": d_fp, "missed_departures": d_fn,
                      "median_detection_delay_s": med(dep_delay)},
        "staff_visits": {"truth_visits": len(t_vis), "found": found, "predicted": len(p_vis),
                         "recall": round(found / len(t_vis), 3) if t_vis else None,
                         "precision": round(true_pred / len(p_vis), 3) if p_vis else None},
        "occupancy_state_agreement": round(agree / total, 4) if total else None,
        "operator_correction_rate": round(corrections / sessions, 3) if sessions else None,
        "cleaning_confirmations": len(cleanings),
        "note": "Metrics are only as good as the ground truth. Synthetic truth does not measure real-world accuracy.",
    }
