import { useEffect, useRef } from "react";
import type { CameraFrame, Layout, ZoneDef } from "../types/api";

export interface Overlay {
  zones: boolean;
  boxes: boolean;
  ids: boolean;
  confidence: boolean;
}

const W = 960;

/** Draws the synthetic overhead scene (simulation) or overlays on top of a real video frame. */
export function CameraView({ frame, layout, zones, overlay, size }: {
  frame: CameraFrame | undefined;
  layout: Layout | null;
  zones: ZoneDef[];
  overlay: Overlay;
  size?: [number, number];
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const simulated = frame?.simulated ?? true;
  const aspect = simulated && layout ? layout.width_m / layout.height_m : size && size[0] ? size[0] / size[1] : 16 / 9;
  const H = Math.round(W / aspect);

  useEffect(() => {
    const c = ref.current;
    const ctx = c?.getContext("2d");
    if (!c || !ctx) return;
    ctx.clearRect(0, 0, W, H);
    if (simulated && layout) {
      // Synthetic overhead render generated from simulator positions.
      ctx.fillStyle = "#1a1f2b";
      ctx.fillRect(0, 0, W, H);
      const sx = W / layout.width_m;
      const sy = H / layout.height_m;
      ctx.strokeStyle = "rgba(255,255,255,0.04)";
      for (let x = 0; x < layout.width_m; x += 1) {
        ctx.beginPath();
        ctx.moveTo(x * sx, 0);
        ctx.lineTo(x * sx, H);
        ctx.stroke();
      }
      for (const f of layout.fixtures) {
        const [x0, y0, x1, y1] = f.rect;
        ctx.fillStyle = f.kind === "door" ? "#2b6f68" : "#262d3d";
        ctx.fillRect(x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy);
      }
      for (const t of layout.tables) {
        ctx.fillStyle = "#4a3b2a";
        for (const [cx, cy] of t.chairs) {
          ctx.beginPath();
          ctx.arc(cx * sx, cy * sy, 0.2 * sx, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.fillStyle = "#7a5c3d";
        ctx.fillRect((t.center[0] - t.size[0] / 2) * sx, (t.center[1] - t.size[1] / 2) * sy, t.size[0] * sx, t.size[1] * sy);
      }
      for (const p of frame?.people ?? []) {
        const x = p.x * W;
        const y = p.y * H;
        ctx.beginPath();
        ctx.arc(x, y, 0.22 * sx, 0, Math.PI * 2);
        ctx.fillStyle = p.role === "staff" ? "#1f2937" : p.role === "passerby" ? "#6b7280" : "#c7a27c";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, 0.11 * sx, 0, Math.PI * 2);
        ctx.fillStyle = p.role === "staff" ? "#e5e7eb" : "#3b2f2f";
        ctx.fill();
      }
      if (frame?.outage) {
        ctx.fillStyle = "rgba(10,12,20,0.85)";
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = "#cbd5e1";
        ctx.font = "600 28px system-ui";
        ctx.textAlign = "center";
        ctx.fillText("NO SIGNAL (simulated camera outage)", W / 2, H / 2);
        ctx.textAlign = "left";
      }
    }
    if (overlay.zones) {
      ctx.lineWidth = 2;
      for (const z of zones) {
        ctx.beginPath();
        z.polygon.forEach(([x, y], i) => (i ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H)));
        ctx.closePath();
        ctx.strokeStyle = "rgba(56,189,248,0.85)";
        ctx.setLineDash([8, 6]);
        ctx.stroke();
        ctx.fillStyle = "rgba(56,189,248,0.06)";
        ctx.fill();
        ctx.setLineDash([]);
        ctx.fillStyle = "#7dd3fc";
        ctx.font = "600 14px system-ui";
        ctx.fillText(z.table_id, z.polygon[0][0] * W + 4, z.polygon[0][1] * H + 16);
      }
    }
    for (const t of frame?.tracks ?? []) {
      const [x1, y1, x2, y2] = t.bbox;
      const color = t.ambiguous ? "#fbbf24" : t.table ? "#4ade80" : "#e2e8f0";
      if (overlay.boxes) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.strokeRect(x1 * W, y1 * H, (x2 - x1) * W, (y2 - y1) * H);
      }
      const label = [overlay.ids ? `#${t.id}` : "", overlay.confidence ? t.conf.toFixed(2) : "", t.ambiguous ? "?" : ""].filter(Boolean).join(" ");
      if (label && (overlay.boxes || overlay.ids || overlay.confidence)) {
        ctx.font = "600 12px ui-monospace, monospace";
        const w = ctx.measureText(label).width + 6;
        ctx.fillStyle = "rgba(7,12,23,0.8)";
        ctx.fillRect(x1 * W, y1 * H - 16, w, 15);
        ctx.fillStyle = color;
        ctx.fillText(label, x1 * W + 3, y1 * H - 4);
      }
    }
  }, [frame, layout, zones, overlay, simulated, H]);

  return (
    <div className="relative overflow-hidden rounded-lg border border-ink-600 bg-black" style={{ aspectRatio: `${W} / ${H}` }}>
      {!simulated && frame && (
        <img
          src={`/api/cameras/${encodeURIComponent(frame.camera_id)}/stream.mjpg`}
          alt={`Live preview of camera ${frame.camera_id}`}
          className="absolute inset-0 h-full w-full object-fill"
        />
      )}
      <canvas ref={ref} width={W} height={H} className="absolute inset-0 h-full w-full" aria-label="Camera view with detection overlays" />
      <div className="pointer-events-none absolute left-2 top-2 flex gap-2">
        {simulated ? (
          <span className="chip border-fuchsia-400/60 bg-fuchsia-600/70 text-white">SIMULATED DATA · synthetic tracks, no model</span>
        ) : (
          <span className="chip border-sky-400/60 bg-sky-700/70 text-white">Person detector + tracker output</span>
        )}
      </div>
    </div>
  );
}
