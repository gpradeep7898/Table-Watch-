"""Multi-object tracker in the style of ByteTrack (Zhang et al., 2022).

This is an independent, dependency-free implementation of the published
two-stage idea: match confident detections first, then use low-confidence
detections to keep existing tracks alive through partial occlusion. Motion
is a simple constant-velocity prediction instead of a Kalman filter.

Track IDs are short-lived integers. They are never linked to identity and
never shared across cameras.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .detector import Detection

Box = tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


@dataclass
class Track:
    track_id: int
    bbox: Box
    score: float
    last_t: float
    hits: int = 1
    velocity: tuple[float, float] = (0.0, 0.0)
    matched_now: bool = True
    history: list[Box] = field(default_factory=list)

    def predict(self, t: float) -> Box:
        dt = max(0.0, t - self.last_t)
        dx, dy = self.velocity[0] * dt, self.velocity[1] * dt
        x1, y1, x2, y2 = self.bbox
        return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)


def _greedy_match(tracks: list[Track], dets: list[Detection], t: float, threshold: float):
    pairs = []
    for ti, tr in enumerate(tracks):
        pred = tr.predict(t)
        for di, d in enumerate(dets):
            v = iou(pred, d.bbox)
            if v >= threshold:
                pairs.append((v, ti, di))
    pairs.sort(reverse=True)
    used_t, used_d, matches = set(), set(), []
    for _, ti, di in pairs:
        if ti in used_t or di in used_d:
            continue
        used_t.add(ti)
        used_d.add(di)
        matches.append((ti, di))
    return matches, [i for i in range(len(tracks)) if i not in used_t], [i for i in range(len(dets)) if i not in used_d]


class ByteTracker:
    def __init__(self, high: float = 0.5, low: float = 0.15, match_iou: float = 0.25, low_iou: float = 0.45,
                 max_lost_s: float = 2.0, min_hits: int = 2) -> None:
        self.high, self.low = high, low
        self.match_iou, self.low_iou = match_iou, low_iou
        self.max_lost_s = max_lost_s
        self.min_hits = min_hits
        self.tracks: list[Track] = []
        self._next = 1

    def reset(self) -> None:
        self.tracks = []

    def _update_track(self, tr: Track, d: Detection, t: float) -> None:
        dt = t - tr.last_t
        if dt > 0:
            cx_old = (tr.bbox[0] + tr.bbox[2]) / 2
            cy_old = (tr.bbox[1] + tr.bbox[3]) / 2
            cx_new = (d.bbox[0] + d.bbox[2]) / 2
            cy_new = (d.bbox[1] + d.bbox[3]) / 2
            vx, vy = (cx_new - cx_old) / dt, (cy_new - cy_old) / dt
            tr.velocity = (0.6 * tr.velocity[0] + 0.4 * vx, 0.6 * tr.velocity[1] + 0.4 * vy)
        tr.bbox, tr.score, tr.last_t = d.bbox, d.score, t
        tr.hits += 1
        tr.matched_now = True

    def update(self, detections: Iterable[Detection], t: float) -> list[Track]:
        dets = list(detections)
        high = [d for d in dets if d.score >= self.high]
        low = [d for d in dets if self.low <= d.score < self.high]
        for tr in self.tracks:
            tr.matched_now = False
        # Stage 1: confident detections against every live track.
        matches, un_t, un_d = _greedy_match(self.tracks, high, t, self.match_iou)
        for ti, di in matches:
            self._update_track(self.tracks[ti], high[di], t)
        # Stage 2: weak detections keep confirmed, unmatched tracks alive.
        remaining = [self.tracks[i] for i in un_t if self.tracks[i].hits >= self.min_hits]
        m2, _, _ = _greedy_match(remaining, low, t, self.low_iou)
        for ti, di in m2:
            self._update_track(remaining[ti], low[di], t)
        # New tracks from unmatched confident detections.
        for di in un_d:
            self.tracks.append(Track(self._next, high[di].bbox, high[di].score, t))
            self._next += 1
        # Drop tracks lost for too long.
        self.tracks = [tr for tr in self.tracks if t - tr.last_t <= self.max_lost_s]
        return [tr for tr in self.tracks if tr.matched_now and tr.hits >= self.min_hits]
