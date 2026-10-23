import type {
  Alert,
  Analytics,
  CameraConfig,
  Layout,
  Mode,
  Scenario,
  SettingsResponse,
  SimStatus,
  Snapshot,
  TableView,
  TWEvent,
  VisionStatus,
  ZoneDef,
} from "../types/api";

export class ApiError extends Error {
  status: number;
  code: string;
  errors: string[];
  constructor(status: number, code: string, message: string, errors: string[] = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.errors = errors;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the TableWatch server. Is it running?");
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const d = data?.detail;
    if (Array.isArray(d)) {
      // FastAPI validation errors
      const msgs = d.map((x: any) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`);
      throw new ApiError(res.status, "validation", msgs.join("; "), msgs);
    }
    throw new ApiError(res.status, d?.code ?? "error", d?.message ?? `Request failed (${res.status})`, d?.errors ?? []);
  }
  return data as T;
}

export function newRequestId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const q = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  });
  const str = s.toString();
  return str ? `?${str}` : "";
};

export type TableAction = "confirm_service" | "confirm_cleaning" | "correct_status" | "verify_observation";

export const api = {
  state: () => request<Snapshot>("GET", "/api/state"),
  layout: () => request<Layout>("GET", "/api/layout"),
  table: (id: string) => request<{ table: TableView; events: TWEvent[] }>("GET", `/api/tables/${encodeURIComponent(id)}`),
  tableAction: (id: string, action: TableAction, extra: { to_state?: string; reason?: string; actor?: string } = {}) =>
    request<{ ok: boolean; events: TWEvent[]; table: TableView }>("POST", `/api/tables/${encodeURIComponent(id)}/actions`, {
      action,
      request_id: newRequestId(),
      ...extra,
    }),
  alerts: (params: { dataset?: Mode; scope?: string; status?: string; type?: string; table_id?: string; limit?: number }) =>
    request<{ alerts: Alert[] }>("GET", `/api/alerts${q(params)}`),
  acknowledge: (id: string, actor: string) =>
    request<{ ok: boolean; alert: Alert }>("POST", `/api/alerts/${id}/acknowledge`, { actor, request_id: newRequestId() }),
  events: (params: { dataset?: Mode; scope?: string; run_id?: string; table_id?: string; types?: string; limit?: number; before_seq?: number }) =>
    request<{ events: TWEvent[]; run_id: string | null }>("GET", `/api/events${q(params)}`),
  runs: (dataset?: Mode) => request<{ runs: any[] }>("GET", `/api/runs${q({ dataset })}`),
  scenarios: () => request<{ scenarios: Scenario[] }>("GET", "/api/sim/scenarios"),
  simStart: (scenario?: string, opts: { seed?: number; autoplay?: boolean; speed?: number } = {}) =>
    request<{ sim: SimStatus }>("POST", "/api/sim/start", { scenario, ...opts }),
  simControl: (action: "play" | "pause" | "speed", speed?: number) =>
    request<{ sim: SimStatus }>("POST", "/api/sim/control", { action, speed }),
  simAdvance: (seconds: number) => request<{ sim: SimStatus }>("POST", "/api/sim/advance", { seconds }),
  history: (days: number) => request<{ job: any }>("POST", "/api/sim/history", { days }),
  historyStatus: () => request<{ job: any; runs: number }>("GET", "/api/sim/history"),
  settings: () => request<SettingsResponse>("GET", "/api/settings"),
  saveThresholds: (data: Record<string, number>) => request<{ thresholds: Record<string, number>; version: number }>("PUT", "/api/settings/thresholds", data),
  resetThresholds: () => request<{ thresholds: Record<string, number> }>("POST", "/api/settings/thresholds/reset"),
  saveSim: (data: Record<string, unknown>) => request("PUT", "/api/settings/simulation", data),
  savePrivacy: (data: Record<string, unknown>) => request("PUT", "/api/settings/privacy", data),
  cameras: () => request<{ cameras: CameraConfig[]; vision: VisionStatus; media: { path: string; name: string; size_mb: number }[] }>("GET", "/api/cameras"),
  addCamera: (data: Record<string, unknown>) => request<{ camera: CameraConfig }>("POST", "/api/cameras", data),
  deleteCamera: (id: string) => request("DELETE", `/api/cameras/${encodeURIComponent(id)}`),
  setMode: (mode: Mode, camera_ids: string[] = []) => request<{ mode: Mode }>("POST", "/api/mode", { mode, camera_ids }),
  videoControl: (action: "play" | "pause") => request("POST", "/api/video/control", { action }),
  zones: (cameraId: string) => request<{ camera_id: string; anchor: string; version: number; zones: ZoneDef[]; anchor_modes: string[] }>("GET", `/api/zones/${encodeURIComponent(cameraId)}`),
  validateZones: (cameraId: string, anchor: string, zones: ZoneDef[]) =>
    request<{ ok: boolean; errors: string[]; warnings: string[] }>("POST", `/api/zones/${encodeURIComponent(cameraId)}/validate`, { anchor, zones }),
  saveZones: (cameraId: string, anchor: string, zones: ZoneDef[]) =>
    request<{ version: number; warnings: string[] }>("PUT", `/api/zones/${encodeURIComponent(cameraId)}`, { anchor, zones }),
  resetDemoZones: (cameraId: string) => request<{ version: number }>("POST", `/api/zones/${encodeURIComponent(cameraId)}/reset-demo`),
  testZones: (cameraId: string, anchor: string, zones: ZoneDef[]) =>
    request<{ t: number; tracks: number; per_table: Record<string, number[]>; unassigned: number[]; ambiguous: number[] }>(
      "POST",
      `/api/zones/${encodeURIComponent(cameraId)}/test`,
      { anchor, zones },
    ),
  analytics: (params: { dataset?: Mode; scope?: string; run_id?: string; tables_per_waiter?: number }) =>
    request<Analytics>("GET", `/api/analytics${q(params)}`),
  dataSummary: () => request<{ counts: Record<string, { events: number; runs: number; sessions: number }>; privacy: any }>("GET", "/api/data/summary"),
  deleteData: (dataset: Mode, confirm: string) => request<{ deleted_events: number }>("POST", "/api/data/delete", { dataset, confirm }),
  exportUrl: (kind: "events" | "sessions" | "alerts", dataset: Mode, run_id?: string) => `/api/export/${kind}.csv${q({ dataset, run_id })}`,
};
