"""Command line tools.

    python -m tablewatch.cli serve
    python -m tablewatch.cli init-db
    python -m tablewatch.cli analyze --video media/lunch.mp4 --zones configs/zones/lunch.json
    python -m tablewatch.cli simulate --scenario normal --out demo/generated/normal
    python -m tablewatch.cli evaluate --events out/events.jsonl --truth truth.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


def _out_dir(arg: str | None, prefix: str) -> Path:
    from .config.layout import project_root

    d = Path(arg) if arg else project_root() / "demo" / "generated" / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_init_db(args: argparse.Namespace) -> int:
    from .config.layout import data_dir
    from .storage.db import Store

    path = Path(args.db) if args.db else data_dir() / "tablewatch.db"
    store = Store(path)
    print(f"Database ready at {path} (schema version {store.migrate()}). Existing data was kept.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .main import main

    main()
    return 0


def _draw(frame, track_frame, zones, engine, w, h):
    import cv2

    colors = {"FREE": (160, 200, 60), "SEATED_UNSERVED": (230, 140, 40), "SERVED": (200, 90, 160), "NEEDS_CLEANING": (40, 170, 240)}
    for z in zones:
        pts = [(int(x * w), int(y * h)) for x, y in z.polygon]
        state = engine.tables[z.table_id].state.value
        c = colors.get(state, (180, 180, 180))
        for i in range(len(pts)):
            cv2.line(frame, pts[i], pts[(i + 1) % len(pts)], c, 2)
        cv2.putText(frame, f"{z.table_id} {state}", pts[0], cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
    for p in track_frame.tracks:
        x1, y1, x2, y2 = p.bbox
        cv2.rectangle(frame, (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h)), (255, 255, 255), 1)
        cv2.putText(frame, f"#{p.track_id} {p.confidence:.2f}", (int(x1 * w), int(y1 * h) - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, "TableWatch analysis (person detector + tracker)", (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (255, 255, 255), 1, cv2.LINE_AA)


def cmd_analyze(args: argparse.Namespace) -> int:
    import cv2

    from .config.layout import load_layout, load_zone_file
    from .core.engine import RestaurantEngine
    from .core.models import Source
    from .core.thresholds import Thresholds
    from .services.settings import default_thresholds
    from .vision.detector import DetectorUnavailable, YoloxOnnxDetector
    from .vision.pipeline import VisionPipeline

    video = Path(args.video)
    if not video.is_file():
        print(f"error: video not found: {video}", file=sys.stderr)
        return 2
    try:
        detector = YoloxOnnxDetector(model=args.model, conf=args.det_conf)
    except DetectorUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    camera_id, anchor, zones = load_zone_file(Path(args.zones))
    if args.anchor:
        anchor = args.anchor
    layout = load_layout()
    th: Thresholds = default_thresholds()
    engine = RestaurantEngine(layout.table_infos(), th, wall=lambda t: t, session_prefix="analysis:")
    engine.register_camera(camera_id, zones, anchor, Source.COMPUTER_VISION)
    unknown = [z.table_id for z in zones if z.table_id not in engine.tables]
    if unknown:
        print(f"warning: zones for unknown tables ignored: {', '.join(unknown)}", file=sys.stderr)
    events = []
    engine.subscribe(events.append)
    pipeline = VisionPipeline(detector, camera_id)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"error: OpenCV could not open {video}", file=sys.stderr)
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out_dir = _out_dir(args.out, f"analysis-{video.stem}")
    writer = None
    if args.annotate:
        writer = cv2.VideoWriter(str(out_dir / "annotated.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), args.sample_fps, (w, h))
    interval = 1.0 / args.sample_fps
    next_t = 0.0
    decoded = analysed = 0
    infer_ms: list[float] = []
    empty_frames = 0
    ambiguous_hits = 0
    last_t = 0.0
    wall0 = time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        decoded += 1
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if args.max_seconds and t > args.max_seconds:
            break
        if t + 1e-6 < next_t:
            continue
        next_t = t + interval
        t0 = time.perf_counter()
        tf, _ = pipeline.process(frame, t)
        infer_ms.append((time.perf_counter() - t0) * 1000)
        analysed += 1
        empty_frames += not tf.tracks
        engine.ingest(tf)
        engine.tick(t)
        ambiguous_hits += len(engine.cameras[camera_id].last_association.get("ambiguous", []))
        last_t = t
        if writer is not None:
            _draw(frame, tf, zones, engine, w, h)
            writer.write(frame)
        if args.progress and analysed % 50 == 0:
            print(f"  {t:7.1f}s analysed={analysed} events={len(events)}", file=sys.stderr)
    cap.release()
    if writer is not None:
        writer.release()
    wall = time.perf_counter() - wall0

    with open(out_dir / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e.to_dict(), default=str) + "\n")
    open_tables = {tid: sm.state.value for tid, sm in engine.tables.items() if sm.state.value != "FREE"}
    lowq = [e for e in events if e.type.value == "LOW_CONFIDENCE_STARTED"]
    summary = {
        "video": {"file": video.name, "fps": round(fps, 2), "frames": total_frames, "resolution": [w, h],
                  "analysed_until_s": round(last_t, 2)},
        "processing": {
            "detector": detector.name,
            "sample_fps": args.sample_fps,
            "frames_decoded": decoded,
            "frames_analysed": analysed,
            "wall_time_s": round(wall, 2),
            "analysed_frames_per_s": round(analysed / wall, 2) if wall else None,
            "inference_ms_mean": round(statistics.fmean(infer_ms), 1) if infer_ms else None,
            "inference_ms_p95": round(sorted(infer_ms)[int(0.95 * (len(infer_ms) - 1))], 1) if infer_ms else None,
            "realtime_factor": round(last_t / wall, 2) if wall else None,
            "note": "Measured on this machine for this video only.",
        },
        "events": dict(Counter(e.type.value for e in events)),
        "transitions": [
            {"t": round(e.t, 2), "table": e.table_id, "type": e.type.value,
             "from": e.from_state.value if e.from_state else None, "to": e.to_state.value if e.to_state else None}
            for e in events if e.to_state is not None
        ],
        "unresolved": {
            "tables_not_free_at_end": open_tables,
            "ambiguous_track_observations": ambiguous_hits,
            "low_confidence_episodes": [{"table": e.table_id, "t": round(e.t, 1), "reasons": e.payload.get("reasons")} for e in lowq],
            "frames_without_tracks": empty_frames,
            "service_confirmations": "Not inferred from video. Service and cleaning need staff confirmation.",
        },
        "outputs": {"events": str(out_dir / "events.jsonl"), "annotated_video": str(out_dir / "annotated.mp4") if writer else None},
        "disclaimer": "Automated observations, not verified ground truth. Accuracy on restaurant CCTV is untested.",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if writer is not None:
        print(f"\nAnnotated video written to {out_dir / 'annotated.mp4'}. Delete it when you no longer need it.", file=sys.stderr)
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from .config.layout import load_layout, load_zone_file, project_root
    from .services.settings import default_thresholds
    from .simulation.scenarios import load_scenarios
    from .simulation.session import SimSession

    layout = load_layout()
    scenarios = load_scenarios({t.id for t in layout.tables})
    if args.scenario not in scenarios:
        print(f"error: unknown scenario. Choose from: {', '.join(scenarios)}", file=sys.stderr)
        return 2
    _, anchor, zones = load_zone_file(project_root() / "configs" / "zones" / "demo.json")
    events = []
    s = SimSession(layout, scenarios[args.scenario], default_thresholds(), zones, anchor, f"cli-{args.scenario}",
                   seed=args.seed, on_event=events.append)
    s.run_headless()
    out = _out_dir(args.out, f"sim-{args.scenario}")
    with open(out / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e.to_dict(), default=str) + "\n")
    (out / "truth.json").write_text(json.dumps(s.sim.ground_truth(), indent=2))
    print(f"SIMULATED run '{args.scenario}' (seed {s.sim.seed}): {len(events)} events")
    print(json.dumps(dict(Counter(e.type.value for e in events)), indent=2))
    print(f"Wrote {out / 'events.jsonl'} and {out / 'truth.json'}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluation.evaluate import evaluate, load_predicted

    truth = json.loads(Path(args.truth).read_text())
    result = evaluate(load_predicted(Path(args.events)), truth, args.tolerance)
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tablewatch", description="TableWatch AI local tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the local API server").set_defaults(fn=cmd_serve)
    s = sub.add_parser("init-db", help="create or migrate the local database (never deletes data)")
    s.add_argument("--db")
    s.set_defaults(fn=cmd_init_db)
    a = sub.add_parser("analyze", help="run person detection + table logic on a local video file")
    a.add_argument("--video", required=True)
    a.add_argument("--zones", required=True, help="zones JSON (camera_id, anchor, zones)")
    a.add_argument("--anchor", choices=["bottom_center", "centroid"])
    a.add_argument("--sample-fps", type=float, default=4.0)
    a.add_argument("--det-conf", type=float, default=0.3)
    a.add_argument("--model", default="yolox_nano", choices=["yolox_nano", "yolox_tiny"])
    a.add_argument("--max-seconds", type=float)
    a.add_argument("--out")
    a.add_argument("--annotate", action="store_true", help="also write an annotated video (opt-in, stored locally)")
    a.add_argument("--progress", action="store_true")
    a.set_defaults(fn=cmd_analyze)
    m = sub.add_parser("simulate", help="run a scenario headless and export events + synthetic ground truth")
    m.add_argument("--scenario", default="normal")
    m.add_argument("--seed", type=int)
    m.add_argument("--out")
    m.set_defaults(fn=cmd_simulate)
    e = sub.add_parser("evaluate", help="score events against a ground-truth annotation")
    e.add_argument("--events", required=True)
    e.add_argument("--truth", required=True)
    e.add_argument("--tolerance", type=float, default=60.0)
    e.set_defaults(fn=cmd_evaluate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
