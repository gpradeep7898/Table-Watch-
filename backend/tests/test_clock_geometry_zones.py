import pytest

from tablewatch.core import geometry
from tablewatch.core.clock import ManualClock, VirtualClock
from tablewatch.core.models import Source, TrackedPerson, TrackFrame
from tablewatch.core.thresholds import Thresholds, validate_thresholds
from tablewatch.core.zones import Zone, ZoneAssigner, validate_zones


def test_virtual_clock_pause_resume_speed():
    c = VirtualClock(epoch=1000.0)
    assert c.paused
    assert c.advance(1.0) == 0.0 and c.now() == 0.0  # paused: no time passes
    c.resume()
    c.advance(1.0)
    assert c.now() == 1.0
    c.set_speed(10)
    c.advance(0.5)
    assert c.now() == 6.0
    c.pause()
    c.advance(100)
    assert c.now() == 6.0
    c.resume()
    c.set_speed(5)
    c.advance(1.0)
    assert c.now() == 11.0
    assert c.wall() == 1011.0
    c.set_speed(0)  # speed 0 means paused
    assert c.paused
    c.reset()
    assert c.now() == 0.0 and c.paused
    with pytest.raises(ValueError):
        c.set_speed(-1)


def test_manual_clock_rejects_time_travel():
    c = ManualClock()
    c.set(5)
    with pytest.raises(ValueError):
        c.set(4)


def test_geometry_basics():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert geometry.point_in_polygon((0.5, 0.5), sq)
    assert not geometry.point_in_polygon((1.5, 0.5), sq)
    assert geometry.area(sq) == pytest.approx(1.0)
    assert geometry.depth((0.5, 0.5), sq) == pytest.approx(0.5)
    assert geometry.depth((1.5, 0.5), sq) == pytest.approx(-0.5)
    bowtie = [(0, 0), (1, 1), (1, 0), (0, 1)]
    assert geometry.is_self_intersecting(bowtie)
    assert not geometry.is_self_intersecting(sq)
    assert geometry.overlap_fraction(sq, [(0.5, 0), (1.5, 0), (1.5, 1), (0.5, 1)]) == pytest.approx(0.5, abs=0.05)
    assert geometry.overlap_fraction(sq, [(2, 2), (3, 2), (3, 3)]) == 0.0


def test_zone_validation_catches_bad_input():
    cams = ["cam"]
    good = [{"table_id": "T1", "polygon": [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3]]}]
    assert validate_zones(good, "cam", cams).ok
    assert not validate_zones(good, "nope", cams).ok
    cases = {
        "at least 3 points": [{"table_id": "T1", "polygon": [[0.1, 0.1], [0.2, 0.2]]}],
        "normalized": [{"table_id": "T1", "polygon": [[0.1, 0.1], [1.3, 0.1], [0.3, 0.3]]}],
        "cross": [{"table_id": "T1", "polygon": [[0.1, 0.1], [0.4, 0.4], [0.4, 0.1], [0.1, 0.4]]}],
        "duplicate": good + good,
        "needs table_id": [{"polygon": [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3]]}],
        "numbers": [{"table_id": "T1", "polygon": [["a", 0.1], [0.3, 0.1], [0.3, 0.3]]}],
    }
    for expected, zones in cases.items():
        res = validate_zones(zones, "cam", cams)
        assert not res.ok, expected
        assert any(expected in e for e in res.errors), (expected, res.errors)
    unknown_table = validate_zones(good, "cam", cams, known_tables=["T2"])
    assert not unknown_table.ok


def test_zone_overlap_warning_and_error():
    a = [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]]
    slight = [[0.48, 0.1], [0.9, 0.1], [0.9, 0.5], [0.48, 0.5]]
    heavy = [[0.2, 0.1], [0.6, 0.1], [0.6, 0.5], [0.2, 0.5]]
    r = validate_zones([{"table_id": "T1", "polygon": a}, {"table_id": "T2", "polygon": slight}], "cam", ["cam"])
    assert r.ok and r.warnings
    r = validate_zones([{"table_id": "T1", "polygon": a}, {"table_id": "T2", "polygon": heavy}], "cam", ["cam"])
    assert not r.ok


def test_anchor_modes():
    p = TrackedPerson(1, (0.1, 0.1, 0.2, 0.5), 0.9)
    assert p.anchor("centroid") == pytest.approx((0.15, 0.3))
    assert p.anchor("bottom_center") == pytest.approx((0.15, 0.5))
    zone = Zone("T1", "cam", ((0.05, 0.4), (0.55, 0.4), (0.55, 0.9), (0.05, 0.9)))
    # Feet inside the zone, centroid above it.
    tall = TrackedPerson(1, (0.2, 0.0, 0.3, 0.5), 0.9)
    frame = TrackFrame("cam", 0, (tall,), Source.COMPUTER_VISION)
    assert ZoneAssigner([zone], "bottom_center").associate(frame).per_table["T1"]
    assert not ZoneAssigner([zone], "centroid").associate(frame).per_table["T1"]
    with pytest.raises(ValueError):
        ZoneAssigner([zone], "head")


def test_threshold_validation():
    assert validate_thresholds({}) == []
    assert validate_thresholds({"unserved_warning_s": 5})  # below minimum
    assert validate_thresholds({"unserved_warning_s": "300"})  # not a number
    assert validate_thresholds({"unserved_warning_s": True})
    assert validate_thresholds({"nonsense": 1})
    assert validate_thresholds({"unserved_warning_s": float("nan")})
    assert validate_thresholds({"lost_track_grace_s": 100, "departure_confirm_s": 90})
    assert validate_thresholds({"min_visit_s": 200, "max_visit_s": 100})
    th = Thresholds.from_dict({"unserved_warning_s": 240})
    assert th.unserved_warning_s == 240 and th.departure_confirm_s == 90
    with pytest.raises(ValueError):
        Thresholds.from_dict({"unserved_warning_s": -1})
