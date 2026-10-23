"""Polygon helpers for table seating zones. Coordinates are normalized 0..1."""

from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]
Polygon = Sequence[Point]


def point_in_polygon(p: Point, poly: Polygon) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def boundary_distance(p: Point, poly: Polygon) -> float:
    """Distance from p to the nearest polygon edge."""
    n = len(poly)
    return min(_seg_dist(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def depth(p: Point, poly: Polygon) -> float:
    """Signed depth: positive inside (distance to edge), negative outside."""
    d = boundary_distance(p, poly)
    return d if point_in_polygon(p, poly) else -d


def area(poly: Polygon) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def centroid(poly: Polygon) -> Point:
    return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = _orient(c, d, a), _orient(c, d, b)
    d3, d4 = _orient(a, b, c), _orient(a, b, d)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def is_self_intersecting(poly: Polygon) -> bool:
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if abs(i - j) <= 1 or (i == 0 and j == n - 1):
                continue
            c, d = poly[j], poly[(j + 1) % n]
            if _segments_cross(a, b, c, d):
                return True
    return False


def overlap_fraction(a: Polygon, b: Polygon, samples: int = 60) -> float:
    """Approximate overlapping area as a fraction of the smaller polygon.

    Grid sampling is plenty for validating hand-drawn zones.
    """
    xs = [p[0] for p in a] + [p[0] for p in b]
    ys = [p[1] for p in a] + [p[1] for p in b]
    ax0, ax1 = min(p[0] for p in a), max(p[0] for p in a)
    bx0, bx1 = min(p[0] for p in b), max(p[0] for p in b)
    ay0, ay1 = min(p[1] for p in a), max(p[1] for p in a)
    by0, by1 = min(p[1] for p in b), max(p[1] for p in b)
    if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
        return 0.0
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    in_a = in_b = both = 0
    for i in range(samples):
        for j in range(samples):
            p = (x0 + (i + 0.5) * (x1 - x0) / samples, y0 + (j + 0.5) * (y1 - y0) / samples)
            ia, ib = point_in_polygon(p, a), point_in_polygon(p, b)
            in_a += ia
            in_b += ib
            both += ia and ib
    smaller = min(in_a, in_b)
    return both / smaller if smaller else 0.0
