import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Crosshair, ImageDown, Plus, RotateCcw, Save, Trash2, Undo2, Wand2 } from "lucide-react";
import { ApiError, api } from "../services/api";
import { useToasts } from "../hooks/useToasts";
import type { Layout, ZoneDef } from "../types/api";
import { ErrorText } from "./ui";

type Pt = [number, number];
const VW = 1000;
const COLORS = ["#38BDF8", "#A78BFA", "#F472B6", "#34D399", "#FBBF24", "#60A5FA", "#F87171", "#2DD4BF", "#C084FC", "#FB923C"];

function clamp01(v: number) {
  return Math.min(1, Math.max(0, v));
}

function inside(p: Pt, poly: Pt[]) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

export function ZoneEditor({ cameraId, layout, simulated }: { cameraId: string; layout: Layout | null; simulated: boolean }) {
  const { push } = useToasts();
  const svgRef = useRef<SVGSVGElement>(null);
  const [zones, setZones] = useState<ZoneDef[]>([]);
  const [anchor, setAnchor] = useState("bottom_center");
  const [version, setVersion] = useState(0);
  const [sel, setSel] = useState<number | null>(null);
  const [drawing, setDrawing] = useState<Pt[] | null>(null);
  const [hover, setHover] = useState<Pt | null>(null);
  const [drag, setDrag] = useState<{ zone: number; vertex: number | null; origin: Pt; start: Pt[] } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [check, setCheck] = useState<{ ok: boolean; errors: string[]; warnings: string[] } | null>(null);
  const [test, setTest] = useState<Awaited<ReturnType<typeof api.testZones>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bg, setBg] = useState<{ url: string; aspect: number } | null>(null);
  const [busy, setBusy] = useState(false);

  const aspect = simulated && layout ? layout.width_m / layout.height_m : bg?.aspect ?? 16 / 9;
  const VH = VW / aspect;
  const tables = layout?.tables ?? [];

  const load = useCallback(async () => {
    try {
      const z = await api.zones(cameraId);
      setZones(z.zones);
      setAnchor(z.anchor);
      setVersion(z.version);
      setDirty(false);
      setSel(null);
      setDrawing(null);
      setCheck(null);
      setTest(null);
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, [cameraId]);
  useEffect(() => {
    load();
    setBg(null);
  }, [load]);

  const grabFrame = async () => {
    setError(null);
    const url = `/api/cameras/${encodeURIComponent(cameraId)}/snapshot.jpg?ts=${Date.now()}`;
    const img = new Image();
    img.onload = () => setBg({ url, aspect: img.naturalWidth / img.naturalHeight });
    img.onerror = async () => {
      try {
        const r = await fetch(url);
        const d = await r.json();
        setError(d?.detail?.message ?? "Could not load a frame from this camera.");
      } catch {
        setError("Could not load a frame from this camera.");
      }
    };
    img.src = url;
  };

  const toNorm = (e: { clientX: number; clientY: number }): Pt => {
    const r = svgRef.current!.getBoundingClientRect();
    return [clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height)];
  };

  const update = (next: ZoneDef[]) => {
    setZones(next);
    setDirty(true);
    setCheck(null);
    setTest(null);
  };

  const nextTable = () => tables.find((t) => !zones.some((z) => z.table_id === t.id))?.id ?? "";

  const finishDrawing = () => {
    if (!drawing || drawing.length < 3) return;
    update([...zones, { table_id: nextTable(), polygon: drawing.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)] as Pt) }]);
    setSel(zones.length);
    setDrawing(null);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!drawing) return;
      if (e.key === "Escape") setDrawing(null);
      if (e.key === "Enter") finishDrawing();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const onDown = (e: React.PointerEvent) => {
    const p = toNorm(e);
    if (drawing) {
      if (drawing.length >= 3) {
        const [fx, fy] = drawing[0];
        if (Math.hypot((fx - p[0]) * VW, (fy - p[1]) * VH) < 14) return finishDrawing();
      }
      setDrawing([...drawing, p]);
      return;
    }
    const hit = [...zones.keys()].reverse().find((i) => inside(p, zones[i].polygon));
    setSel(hit ?? null);
    if (hit !== undefined) {
      setDrag({ zone: hit, vertex: null, origin: p, start: zones[hit].polygon.map((q) => [...q] as Pt) });
      (e.target as Element).setPointerCapture?.(e.pointerId);
    }
  };

  const onMove = (e: React.PointerEvent) => {
    const p = toNorm(e);
    if (drawing) setHover(p);
    if (!drag) return;
    const next = zones.map((z) => ({ ...z, polygon: z.polygon.map((q) => [...q] as Pt) }));
    if (drag.vertex !== null) {
      next[drag.zone].polygon[drag.vertex] = p;
    } else {
      // Move the whole polygon, clamped so it stays inside the frame.
      const xs = drag.start.map((q) => q[0]);
      const ys = drag.start.map((q) => q[1]);
      const dx = Math.min(Math.max(p[0] - drag.origin[0], -Math.min(...xs)), 1 - Math.max(...xs));
      const dy = Math.min(Math.max(p[1] - drag.origin[1], -Math.min(...ys)), 1 - Math.max(...ys));
      next[drag.zone].polygon = drag.start.map(([x, y]) => [+(x + dx).toFixed(4), +(y + dy).toFixed(4)] as Pt);
    }
    update(next);
  };

  const removeVertex = (zi: number, vi: number) => {
    if (zones[zi].polygon.length <= 3) return push("warning", "A zone needs at least 3 points");
    update(zones.map((z, i) => (i === zi ? { ...z, polygon: z.polygon.filter((_, j) => j !== vi) } : z)));
  };

  const insertVertex = (zi: number, after: number) => {
    const poly = zones[zi].polygon;
    const a = poly[after];
    const b = poly[(after + 1) % poly.length];
    const mid: Pt = [+((a[0] + b[0]) / 2).toFixed(4), +((a[1] + b[1]) / 2).toFixed(4)];
    update(zones.map((z, i) => (i === zi ? { ...z, polygon: [...poly.slice(0, after + 1), mid, ...poly.slice(after + 1)] } : z)));
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      const err = e as ApiError;
      setError(err.errors?.length ? err.errors.join(" · ") : err.message);
    } finally {
      setBusy(false);
    }
  };

  const assigned = useMemo(() => new Set(zones.map((z) => z.table_id)), [zones]);

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]" data-testid="zone-editor">
      <div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {drawing ? (
            <>
              <span className="chip border-brand/50 bg-brand/10 text-brand">
                <Crosshair className="h-3.5 w-3.5" /> Click to add points ({drawing.length}). Click the first point or press Enter to finish.
              </span>
              <button className="btn-primary px-2.5 py-1.5" disabled={drawing.length < 3} onClick={finishDrawing}>
                Finish zone
              </button>
              <button className="btn-ghost px-2.5 py-1.5" onClick={() => setDrawing(null)}>
                Cancel
              </button>
            </>
          ) : (
            <button className="btn-secondary px-2.5 py-1.5" onClick={() => { setDrawing([]); setSel(null); }} data-testid="new-zone">
              <Plus className="h-4 w-4" /> New zone
            </button>
          )}
          {!simulated && (
            <button className="btn-secondary px-2.5 py-1.5" onClick={grabFrame}>
              <ImageDown className="h-4 w-4" /> Load frame from camera
            </button>
          )}
          <span className="ml-auto text-xs text-txt-faint">Drag a zone to move it · drag a point to reshape · click ＋ on an edge to add a point · double-click a point to remove it</span>
        </div>
        <div className="overflow-hidden rounded-lg border border-ink-600 bg-ink-950">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${VW} ${VH}`}
            className={`block w-full touch-none ${drawing ? "cursor-crosshair" : "cursor-default"}`}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={() => setDrag(null)}
            onPointerLeave={() => { setDrag(null); setHover(null); }}
            role="application"
            aria-label="Zone drawing canvas"
          >
            {simulated && layout ? (
              <g transform={`scale(${VW / layout.width_m} ${VH / layout.height_m})`}>
                <rect width={layout.width_m} height={layout.height_m} fill="#1a1f2b" />
                {layout.fixtures.map((f) => (
                  <rect key={f.id} x={f.rect[0]} y={f.rect[1]} width={f.rect[2] - f.rect[0]} height={f.rect[3] - f.rect[1]} fill="#262d3d" />
                ))}
                {layout.tables.map((t) => (
                  <g key={t.id}>
                    {t.chairs.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={0.2} fill="#4a3b2a" />)}
                    <rect x={t.center[0] - t.size[0] / 2} y={t.center[1] - t.size[1] / 2} width={t.size[0]} height={t.size[1]} fill="#7a5c3d" />
                    <text x={t.center[0]} y={t.center[1] + 0.12} fontSize={0.34} textAnchor="middle" fill="#f5e6d3">{t.id}</text>
                  </g>
                ))}
              </g>
            ) : bg ? (
              <image href={bg.url} width={VW} height={VH} preserveAspectRatio="none" />
            ) : (
              <text x={VW / 2} y={VH / 2} textAnchor="middle" fill="#64748B" fontSize={20}>
                Load a frame from this authorized camera to draw on it
              </text>
            )}
            {zones.map((z, i) => {
              const color = COLORS[i % COLORS.length];
              const pts = z.polygon.map(([x, y]) => `${x * VW},${y * VH}`).join(" ");
              const [lx, ly] = z.polygon[0];
              return (
                <g key={i}>
                  <polygon points={pts} fill={color} fillOpacity={sel === i ? 0.22 : 0.1} stroke={color} strokeWidth={sel === i ? 3 : 2} className="cursor-move" />
                  <text x={lx * VW + 6} y={ly * VH + 18} fill={color} fontSize={16} fontWeight={700} pointerEvents="none">
                    {z.table_id || "unassigned"}
                    {test ? ` · ${test.per_table[z.table_id]?.length ?? 0}` : ""}
                  </text>
                  {sel === i &&
                    z.polygon.map(([x, y], vi) => {
                      const [nx, ny] = z.polygon[(vi + 1) % z.polygon.length];
                      return (
                        <g key={vi}>
                          <circle
                            cx={((x + nx) / 2) * VW}
                            cy={((y + ny) / 2) * VH}
                            r={6}
                            fill="#0B1220"
                            stroke={color}
                            strokeWidth={1.5}
                            className="cursor-copy"
                            onPointerDown={(e) => { e.stopPropagation(); insertVertex(i, vi); }}
                          >
                            <title>Add a point here</title>
                          </circle>
                          <circle
                            cx={x * VW}
                            cy={y * VH}
                            r={8}
                            fill={color}
                            stroke="#0B1220"
                            strokeWidth={2}
                            className="cursor-grab"
                            onPointerDown={(e) => {
                              e.stopPropagation();
                              setDrag({ zone: i, vertex: vi, origin: toNorm(e), start: z.polygon });
                              (e.target as Element).setPointerCapture?.(e.pointerId);
                            }}
                            onDoubleClick={(e) => { e.stopPropagation(); removeVertex(i, vi); }}
                          >
                            <title>Drag to move · double-click to remove</title>
                          </circle>
                        </g>
                      );
                    })}
                </g>
              );
            })}
            {drawing && (
              <g pointerEvents="none">
                <polyline
                  points={[...drawing, ...(hover ? [hover] : [])].map(([x, y]) => `${x * VW},${y * VH}`).join(" ")}
                  fill="none"
                  stroke="#2DD4BF"
                  strokeWidth={2}
                  strokeDasharray="6 4"
                />
                {drawing.map(([x, y], i) => (
                  <circle key={i} cx={x * VW} cy={y * VH} r={i === 0 ? 8 : 5} fill={i === 0 ? "#2DD4BF" : "#E7EDF7"} />
                ))}
              </g>
            )}
          </svg>
        </div>
        <ErrorText error={error} />
      </div>

      <div className="space-y-4">
        <div className="card p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Zones ({zones.length})</h3>
            <span className="text-xs text-txt-faint">saved version {version}{dirty ? " · unsaved changes" : ""}</span>
          </div>
          <label className="label mt-3" htmlFor="anchor">Person position anchor</label>
          <select id="anchor" className="input" value={anchor} onChange={(e) => { setAnchor(e.target.value); setDirty(true); }}>
            <option value="bottom_center">Feet (box bottom center): angled cameras</option>
            <option value="centroid">Body center: overhead cameras</option>
          </select>
          <ul className="mt-3 max-h-72 space-y-1.5 overflow-auto">
            {zones.map((z, i) => (
              <li key={i} className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 ${sel === i ? "border-brand/60 bg-brand/5" : "border-ink-600"}`}>
                <span className="h-3 w-3 shrink-0 rounded-sm" style={{ background: COLORS[i % COLORS.length] }} />
                <label className="sr-only" htmlFor={`zone-table-${i}`}>Table for zone {i + 1}</label>
                <select
                  id={`zone-table-${i}`}
                  className="input flex-1 py-1"
                  value={z.table_id}
                  onFocus={() => setSel(i)}
                  onChange={(e) => update(zones.map((q, j) => (j === i ? { ...q, table_id: e.target.value } : q)))}
                >
                  <option value="">Choose table…</option>
                  {tables.map((t) => (
                    <option key={t.id} value={t.id} disabled={assigned.has(t.id) && t.id !== z.table_id}>
                      {t.label}
                    </option>
                  ))}
                </select>
                <span className="w-8 text-right font-mono text-[11px] text-txt-faint">{z.polygon.length}pt</span>
                <button className="btn-ghost p-1" onClick={() => { update(zones.filter((_, j) => j !== i)); setSel(null); }} aria-label={`Delete zone ${z.table_id}`}>
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button className="btn-secondary" disabled={busy} onClick={() => run(async () => setCheck(await api.validateZones(cameraId, anchor, zones)))}>
            <CheckCircle2 className="h-4 w-4" /> Validate
          </button>
          <button
            className="btn-primary"
            disabled={busy || !dirty}
            data-testid="save-zones"
            onClick={() =>
              run(async () => {
                const res = await api.saveZones(cameraId, anchor, zones);
                setVersion(res.version);
                setDirty(false);
                setCheck({ ok: true, errors: [], warnings: res.warnings });
                push("success", `Zones saved (version ${res.version})`);
              })
            }
          >
            <Save className="h-4 w-4" /> Save
          </button>
          <button className="btn-secondary" disabled={busy} onClick={() => run(async () => setTest(await api.testZones(cameraId, anchor, zones)))}>
            <Wand2 className="h-4 w-4" /> Test on live tracks
          </button>
          <button className="btn-secondary" disabled={busy} onClick={load}>
            <Undo2 className="h-4 w-4" /> Reload saved
          </button>
          {simulated && (
            <button
              className="btn-ghost col-span-2"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.resetDemoZones(cameraId);
                  await load();
                  push("success", "Demo zones restored");
                })
              }
            >
              <RotateCcw className="h-4 w-4" /> Reset to demo zones
            </button>
          )}
        </div>
        {check && (
          <div className={`rounded-lg border p-3 text-sm ${check.ok ? "border-emerald-400/40 bg-emerald-400/5" : "border-red-400/40 bg-red-500/10"}`} role="status">
            <p className="font-medium">{check.ok ? "Zones are valid" : "Zones need fixing"}</p>
            {[...check.errors, ...check.warnings].map((m) => (
              <p key={m} className={`mt-1 text-xs ${check.errors.includes(m) ? "text-red-200" : "text-amber-200"}`}>
                {m}
              </p>
            ))}
          </div>
        )}
        {test && (
          <div className="rounded-lg border border-sky-400/30 bg-sky-500/5 p-3 text-xs text-sky-100" role="status">
            <p className="font-medium">
              {test.tracks} track(s) in the latest frame → {Object.values(test.per_table).reduce((n, v) => n + v.length, 0)} inside zones, {test.unassigned.length} outside,{" "}
              {test.ambiguous.length} ambiguous
            </p>
            <p className="mt-1 text-sky-200/80">
              {Object.entries(test.per_table)
                .filter(([, v]) => v.length)
                .map(([k, v]) => `${k}: ${v.map((x) => `#${x}`).join(" ")}`)
                .join(" · ") || "No tracks inside any zone right now."}
            </p>
          </div>
        )}
        <p className="text-xs text-txt-faint">
          Draw seating areas, not just tabletops: include where guests sit. Coordinates are stored normalized (0 to 1), so they survive resolution changes. Overlaps are allowed but people inside them are treated as ambiguous.
        </p>
      </div>
    </div>
  );
}
