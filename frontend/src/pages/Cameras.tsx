import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Camera, CheckCircle2, Film, Pause, Play, Plus, Radio, Trash2, Video } from "lucide-react";
import { CameraView, type Overlay } from "../components/CameraView";
import { SimControls } from "../components/SimControls";
import { ZoneEditor } from "../components/ZoneEditor";
import { Card, Empty, ErrorText, Modal, SimBadge, Toggle } from "../components/ui";
import { useLive } from "../hooks/useLive";
import { useToasts } from "../hooks/useToasts";
import { ApiError, api } from "../services/api";
import { clockTime, getLocal, setLocal } from "../lib/format";
import type { CameraConfig, VisionStatus, ZoneDef } from "../types/api";

type Tab = "monitor" | "zones" | "sources";

function useCameraConfig() {
  const [data, setData] = useState<{ cameras: CameraConfig[]; vision: VisionStatus; media: { path: string; name: string; size_mb: number }[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => {
    try {
      setData(await api.cameras());
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, reload };
}

export function CamerasPage() {
  const [tab, setTab] = useState<Tab>(() => getLocal("tw.cam.tab", "monitor"));
  const cfg = useCameraConfig();
  const pick = (t: Tab) => {
    setTab(t);
    setLocal("tw.cam.tab", t);
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Cameras</h1>
          <p className="text-sm text-txt-muted">Only cameras you add here are ever opened. Nothing scans your network.</p>
        </div>
        <div className="flex rounded-lg border border-ink-600 bg-ink-850 p-1" role="tablist">
          {(
            [
              ["monitor", "Monitor"],
              ["zones", "Zone editor"],
              ["sources", "Sources & modes"],
            ] as [Tab, string][]
          ).map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              className={`btn px-3 py-1.5 ${tab === id ? "bg-ink-700 text-txt" : "text-txt-muted hover:text-txt"}`}
              onClick={() => pick(id)}
              data-testid={`tab-${id}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <ErrorText error={cfg.error} />
      {tab === "monitor" && <Monitor />}
      {tab === "zones" && <ZonesTab cameras={cfg.data?.cameras ?? []} />}
      {tab === "sources" && <Sources cfg={cfg} />}
    </div>
  );
}

function Monitor() {
  const { snapshot, frames, layout } = useLive();
  const { push } = useToasts();
  const [cam, setCam] = useState<string | null>(null);
  const [overlay, setOverlay] = useState<Overlay>(() => getLocal("tw.overlay", { zones: true, boxes: true, ids: true, confidence: false }));
  const [zones, setZones] = useState<ZoneDef[]>([]);
  const cams = snapshot?.cameras ?? [];
  const active = cam && cams.some((c) => c.camera_id === cam) ? cam : cams[0]?.camera_id;
  const health = cams.find((c) => c.camera_id === active);
  const frame = frames?.frames.find((f) => f.camera_id === active);
  useEffect(() => {
    if (active) api.zones(active).then((z) => setZones(z.zones)).catch(() => setZones([]));
  }, [active, snapshot?.run?.id]);
  const setO = (k: keyof Overlay, v: boolean) => {
    const next = { ...overlay, [k]: v };
    setOverlay(next);
    setLocal("tw.overlay", next);
  };
  if (!snapshot) return null;
  const sim = snapshot.mode === "simulation";
  const status = health?.status ?? (health?.offline ? "offline" : "running");
  return (
    <div className="space-y-4">
      {sim && <SimControls compact />}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <Card
          title={
            <span className="flex items-center gap-2">
              <Camera className="h-4 w-4 text-txt-muted" />
              <select className="input w-auto py-1" value={active ?? ""} onChange={(e) => setCam(e.target.value)} aria-label="Camera">
                {cams.map((c) => (
                  <option key={c.camera_id} value={c.camera_id}>
                    {c.camera_id}
                  </option>
                ))}
              </select>
            </span>
          }
          actions={
            !sim && (
              <div className="flex gap-1">
                <button className="btn-secondary px-2 py-1" onClick={() => api.videoControl("play").catch((e) => push("error", e.message))} aria-label="Play video">
                  <Play className="h-4 w-4" />
                </button>
                <button className="btn-secondary px-2 py-1" onClick={() => api.videoControl("pause").catch((e) => push("error", e.message))} aria-label="Pause video">
                  <Pause className="h-4 w-4" />
                </button>
              </div>
            )
          }
        >
          {active ? (
            <CameraView frame={frame} layout={layout} zones={zones} overlay={overlay} size={frame?.size} />
          ) : (
            <Empty title="No camera is active" />
          )}
          <div className="mt-3 flex flex-wrap gap-4">
            <Toggle id="o-zones" label="Zones" checked={overlay.zones} onChange={(v) => setO("zones", v)} />
            <Toggle id="o-boxes" label="Bounding boxes" checked={overlay.boxes} onChange={(v) => setO("boxes", v)} />
            <Toggle id="o-ids" label="Track IDs" checked={overlay.ids} onChange={(v) => setO("ids", v)} />
            <Toggle id="o-conf" label="Confidence" checked={overlay.confidence} onChange={(v) => setO("confidence", v)} />
          </div>
          <p className="mt-2 text-xs text-txt-faint">Box colours: green = inside a table zone, amber = ambiguous (overlapping zones), white = outside all zones.</p>
        </Card>
        <Card title="Feed status">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Mode</dt><dd>{sim ? <SimBadge compact /> : snapshot.mode === "recorded" ? "Recorded video" : "Live camera"}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Connection</dt><dd className={health?.offline ? "text-red-300" : "text-emerald-300"}>{health?.offline ? "Offline" : status}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Observation source</dt><dd className="text-right text-xs">{health?.detector ?? "—"}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Processing rate</dt><dd className="font-mono">{health?.processing_fps ?? 0} fps</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Observations/s</dt><dd className="font-mono">{health?.observations_per_s ?? 0}</dd></div>
            {health?.inference_ms !== undefined && <div className="flex justify-between gap-2"><dt className="text-txt-muted">Inference</dt><dd className="font-mono">{health.inference_ms} ms</dd></div>}
            {health?.frames_dropped !== undefined && <div className="flex justify-between gap-2"><dt className="text-txt-muted">Stale frames dropped</dt><dd className="font-mono">{health.frames_dropped}</dd></div>}
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Last good frame</dt><dd className="font-mono">{health?.last_frame_wall ? clockTime(new Date(health.last_frame_wall * 1000).toISOString()) : "—"}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Tracks in view</dt><dd className="font-mono">{frame?.tracks.length ?? 0}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-txt-muted">Outages</dt><dd className="font-mono">{health?.outages ?? 0}</dd></div>
          </dl>
          {health?.error && <ErrorText error={health.error} />}
          {sim && (
            <p className="mt-4 rounded-lg border border-fuchsia-400/30 bg-fuchsia-500/5 p-3 text-xs text-fuchsia-100">
              This feed is a synthetic overhead render. Tracks come straight from simulated positions with injected misses, ID switches and noise. No detector runs and nothing here measures real accuracy.
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}

function ZonesTab({ cameras }: { cameras: CameraConfig[] }) {
  const { layout } = useLive();
  const [cam, setCam] = useState("sim-cam-1");
  const current = cameras.find((c) => c.id === cam);
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Table zones for
          <select className="input w-auto py-1" value={cam} onChange={(e) => setCam(e.target.value)} aria-label="Camera for zones">
            {(cameras.length ? cameras : [{ id: "sim-cam-1", name: "sim-cam-1" } as CameraConfig]).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name ?? c.id}
              </option>
            ))}
          </select>
        </span>
      }
    >
      <ZoneEditor key={cam} cameraId={cam} layout={layout} simulated={!current || current.kind === "simulation"} />
    </Card>
  );
}

function Sources({ cfg }: { cfg: ReturnType<typeof useCameraConfig> }) {
  const { snapshot } = useLive();
  const { push } = useToasts();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const data = cfg.data;
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    try {
      await fn();
      push("success", ok);
      cfg.reload();
    } catch (e) {
      setError((e as ApiError).message);
    }
  };
  if (!data) return null;
  const v = data.vision;
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="space-y-5">
        <Card title="Processing mode" icon={<Radio className="h-4 w-4 text-txt-muted" />}>
          <p className="text-sm text-txt-muted">
            Current mode: <strong className="text-txt">{snapshot?.mode}</strong>. Each mode writes to its own dataset, so simulated and real data never mix.
          </p>
          {snapshot?.mode !== "simulation" && (
            <button className="btn-secondary mt-3" onClick={() => run(() => api.setMode("simulation"), "Back to simulation")}>
              Return to simulation
            </button>
          )}
          <ErrorText error={error} />
        </Card>
        <Card title="Configured sources" actions={<button className="btn-primary px-2.5 py-1.5" onClick={() => setAdding(true)}><Plus className="h-4 w-4" /> Add source</button>}>
          <ul className="space-y-2">
            {data.cameras.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-ink-600 bg-ink-850 px-3 py-2.5">
                {c.kind === "simulation" ? <SimBadge compact /> : c.kind === "file" ? <Film className="h-4 w-4 text-sky-300" /> : <Video className="h-4 w-4 text-sky-300" />}
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{c.name}</p>
                  <p className="truncate font-mono text-[11px] text-txt-faint">
                    {c.id} · {c.kind}
                    {c.config.path ? ` · ${c.config.path}` : ""}
                    {c.config.url ? ` · ${c.config.url}` : ""}
                    {c.config.url_env ? ` · $${c.config.url_env}` : ""}
                    {c.config.index !== undefined ? ` · device ${c.config.index}` : ""}
                  </p>
                </div>
                {c.kind === "file" && (
                  <button className="btn-secondary px-2.5 py-1 text-xs" onClick={() => run(() => api.setMode("recorded", [c.id]), "Recorded video analysis started")}>
                    <Play className="h-3.5 w-3.5" /> Analyse video
                  </button>
                )}
                {(c.kind === "webcam" || c.kind === "rtsp") && (
                  <button className="btn-secondary px-2.5 py-1 text-xs" onClick={() => run(() => api.setMode("live", [c.id]), "Live camera started")}>
                    <Radio className="h-3.5 w-3.5" /> Go live
                  </button>
                )}
                {!c.builtin && (
                  <button className="btn-ghost p-1" onClick={() => run(() => api.deleteCamera(c.id), "Source removed")} aria-label={`Remove ${c.name}`}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-txt-faint">Real sources need table zones drawn in the Zone editor before analysis can start.</p>
        </Card>
      </div>
      <Card title="Person detector" icon={v.ready ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <AlertCircle className="h-4 w-4 text-amber-300" />}>
        <dl className="space-y-1.5 text-sm">
          <div className="flex justify-between"><dt className="text-txt-muted">Model</dt><dd className="font-mono text-xs">{v.name}</dd></div>
          <div className="flex justify-between"><dt className="text-txt-muted">Weights present</dt><dd>{v.present ? "yes" : "no"}</dd></div>
          <div className="flex justify-between"><dt className="text-txt-muted">Checksum verified</dt><dd>{v.verified ? "yes" : "no"}</dd></div>
          <div className="flex justify-between"><dt className="text-txt-muted">ONNX Runtime</dt><dd>{v.onnxruntime ? "installed" : "missing"}</dd></div>
          <div className="flex justify-between"><dt className="text-txt-muted">OpenCV</dt><dd>{v.opencv ? "installed" : "missing"}</dd></div>
        </dl>
        <p className="mt-3 text-xs text-txt-muted">License: {v.license}.</p>
        {!v.ready && (
          <div className="mt-3 rounded-lg border border-amber-400/30 bg-amber-500/5 p-3 text-xs text-amber-100">
            Real video is optional. To enable it run <code className="kbd">./scripts/setup.sh --vision</code> then <code className="kbd">./scripts/download-model.sh</code>. Demo mode works without it.
          </div>
        )}
        <p className="mt-3 text-xs text-txt-faint">
          Put local recordings in the project&apos;s <code>media/</code> folder. Videos are read in place and never copied or uploaded.
        </p>
      </Card>
      <AddSource open={adding} onClose={() => setAdding(false)} media={data.media} onAdded={() => { setAdding(false); cfg.reload(); }} />
    </div>
  );
}

function AddSource({ open, onClose, media, onAdded }: { open: boolean; onClose: () => void; media: { path: string; name: string }[]; onAdded: () => void }) {
  const { push } = useToasts();
  const [kind, setKind] = useState<"file" | "webcam" | "rtsp">("file");
  const [form, setForm] = useState({ id: "", name: "", path: "", index: 0, url: "", url_env: "", anchor: "bottom_center", sample_fps: 4, authorized: false });
  const [error, setError] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));
  const submit = async () => {
    setError(null);
    try {
      const body: Record<string, unknown> = { id: form.id, name: form.name, kind, anchor: form.anchor, sample_fps: Number(form.sample_fps), authorized: form.authorized };
      if (kind === "file") body.path = form.path;
      if (kind === "webcam") body.index = Number(form.index);
      if (kind === "rtsp") {
        if (form.url_env) body.url_env = form.url_env;
        else body.url = form.url;
      }
      await api.addCamera(body);
      push("success", `Source ${form.name} added`);
      onAdded();
    } catch (e) {
      setError((e as ApiError).message);
    }
  };
  return (
    <Modal open={open} title="Add a video source" onClose={onClose}>
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-1 rounded-lg border border-ink-600 p-1">
          {(["file", "webcam", "rtsp"] as const).map((k) => (
            <button key={k} className={`btn px-2 py-1.5 ${kind === k ? "bg-ink-700" : "text-txt-muted"}`} onClick={() => setKind(k)}>
              {k === "file" ? "Video file" : k === "webcam" ? "Webcam" : "RTSP camera"}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="cam-id">ID</label>
            <input id="cam-id" className="input" placeholder="dining-cam" value={form.id} onChange={(e) => set("id", e.target.value.toLowerCase())} />
          </div>
          <div>
            <label className="label" htmlFor="cam-name">Name</label>
            <input id="cam-name" className="input" placeholder="Dining room" value={form.name} onChange={(e) => set("name", e.target.value)} />
          </div>
        </div>
        {kind === "file" && (
          <div>
            <label className="label" htmlFor="cam-path">Video in media/</label>
            {media.length ? (
              <select id="cam-path" className="input" value={form.path} onChange={(e) => set("path", e.target.value)}>
                <option value="">Choose a file…</option>
                {media.map((m) => (
                  <option key={m.path} value={m.path}>{m.name}</option>
                ))}
              </select>
            ) : (
              <p className="text-xs text-txt-muted">No videos found. Copy a file into the project&apos;s media/ folder and reopen this dialog.</p>
            )}
          </div>
        )}
        {kind === "webcam" && (
          <div>
            <label className="label" htmlFor="cam-index">Device index</label>
            <input id="cam-index" type="number" min={0} max={9} className="input" value={form.index} onChange={(e) => set("index", e.target.value)} />
          </div>
        )}
        {kind === "rtsp" && (
          <>
            <div>
              <label className="label" htmlFor="cam-env">Environment variable holding the URL (recommended)</label>
              <input id="cam-env" className="input font-mono" placeholder="TABLEWATCH_CAM_DINING_URL" value={form.url_env} onChange={(e) => set("url_env", e.target.value.toUpperCase())} />
            </div>
            <div>
              <label className="label" htmlFor="cam-url">…or the RTSP URL</label>
              <input id="cam-url" className="input font-mono" placeholder="rtsp://user:pass@192.168.1.20:554/stream1" value={form.url} onChange={(e) => set("url", e.target.value)} disabled={!!form.url_env} autoComplete="off" />
              <p className="mt-1 text-[11px] text-txt-faint">Stored only in the local database and always shown redacted. Never committed or logged.</p>
            </div>
          </>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="cam-anchor">Camera angle</label>
            <select id="cam-anchor" className="input" value={form.anchor} onChange={(e) => set("anchor", e.target.value)}>
              <option value="bottom_center">Angled (use feet)</option>
              <option value="centroid">Overhead (use body center)</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="cam-fps">Frames analysed per second</label>
            <input id="cam-fps" type="number" min={0.5} max={15} step={0.5} className="input" value={form.sample_fps} onChange={(e) => set("sample_fps", e.target.value)} />
          </div>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 accent-teal-400" checked={form.authorized} onChange={(e) => set("authorized", e.target.checked)} />
          <span>I own this video source or have explicit permission to process it, and people in view have been informed as local law requires.</span>
        </label>
        <ErrorText error={error} />
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={!form.id || !form.name || !form.authorized}>
            <Plus className="h-4 w-4" /> Add source
          </button>
        </div>
      </div>
    </Modal>
  );
}
