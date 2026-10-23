export type TableStateName = "FREE" | "SEATED_UNSERVED" | "SERVED" | "NEEDS_CLEANING";
export type Health = "UNKNOWN" | "OK" | "LOW_CONFIDENCE" | "DEGRADED" | "OFFLINE";
export type Mode = "simulation" | "recorded" | "live";
export type AlertType =
  | "UNSERVED_TOO_LONG"
  | "TABLE_NEEDS_CLEANING"
  | "CLEANING_OVERDUE"
  | "CAMERA_OFFLINE"
  | "LOW_CONFIDENCE_OBSERVATION";
export type AlertStatus = "ACTIVE" | "ACKNOWLEDGED" | "RESOLVED";
export type Severity = "info" | "warning" | "critical";
export type SourceName = "simulator" | "computer_vision" | "operator" | "system";

export interface Alert {
  id: string;
  type: AlertType;
  severity: Severity;
  status: AlertStatus;
  dedup_key: string;
  table_id: string | null;
  camera_id: string | null;
  session_id: string | null;
  message: string;
  created_t?: number;
  created_ts?: string;
  created_wall?: number;
  acknowledged_by?: string | null;
  resolution?: string | null;
  next_step?: string | null;
  run_id?: string;
}

export interface TableView {
  table_id: string;
  label: string;
  seats: number;
  area: string;
  camera_id: string | null;
  state: TableStateName;
  session_id: string | null;
  seated_t: number | null;
  service_t: number | null;
  service_source: string | null;
  left_t: number | null;
  cleaning_required_t: number | null;
  wait_s: number | null;
  occupied_s: number | null;
  cleaning_s: number | null;
  unserved_overdue: boolean;
  cleaning_overdue: boolean;
  guest_count: number;
  present_count: number | null;
  uncertain_count: number | null;
  seating_candidate_s: number | null;
  absence_s: number | null;
  visits: number;
  last_visit_t: number | null;
  reassociations: number;
  health: Health;
  uncertainty: string[];
  alerts: Alert[];
}

export interface CameraHealth {
  camera_id: string;
  kind: string | null;
  online: boolean;
  offline: boolean;
  last_frame_t: number | null;
  frames: number;
  observations: number;
  outages: number;
  zones: number;
  status?: string;
  processing_fps?: number;
  observations_per_s?: number;
  detector?: string | null;
  last_frame_wall?: number | null;
  inference_ms?: number;
  frames_dropped?: number;
  error?: string | null;
  reconnects?: number;
}

export interface Scenario {
  id: string;
  name: string;
  summary: string;
  description: string;
  seed: number;
  start_time: string;
  duration_s: number;
  waiters: number;
  parties: number;
  disturbances: string[];
}

export interface SimStatus {
  scenario: Scenario;
  seed: number;
  speed: number;
  paused: boolean;
  finished: boolean;
  t: number;
  duration_s: number;
  progress: number;
  staff_auto_confirm: boolean;
  waiting_parties: number;
  simulated_actions: number;
}

export interface GuideStep {
  id: string;
  title: string;
  done: boolean;
}

export interface Summary {
  total: number;
  free: number;
  occupied: number;
  awaiting_service: number;
  served: number;
  needs_cleaning: number;
  active_alerts: number;
  open_alerts: number;
  uncertain_tables: number;
}

export interface Snapshot {
  type: "snapshot";
  mode: Mode;
  dataset: Mode;
  run: { id: string; label: string | null; scenario: string | null; seed: number | null; status: string } | null;
  restaurant: { id: string; name: string };
  clock: { t: number; wall: number; iso: string; simulated: boolean; speed: number; paused: boolean };
  sim: SimStatus | null;
  thresholds: Record<string, number>;
  tables: TableView[];
  summary: Summary;
  alerts: Alert[];
  cameras: CameraHealth[];
  guide: GuideStep[] | null;
  error: string | null;
}

export interface Track {
  id: number;
  bbox: [number, number, number, number];
  conf: number;
  table: string | null;
  ambiguous: boolean;
}

export interface Person {
  id: number;
  x: number;
  y: number;
  role: "guest" | "staff" | "passerby";
  activity: string;
  party: number | null;
  track_id: number | null;
  detected: boolean;
  conf: number | null;
}

export interface CameraFrame {
  camera_id: string;
  t: number;
  simulated: boolean;
  tracks: Track[];
  people?: Person[];
  outage?: boolean;
  jpeg_seq?: number;
  size?: [number, number];
}

export interface FramesMsg {
  type: "frames";
  seq: number;
  frames: CameraFrame[];
}

export interface TWEvent {
  id: string;
  seq?: number;
  type: string;
  t: number;
  wall: number;
  ts?: string;
  source: SourceName;
  table_id: string | null;
  camera_id: string | null;
  session_id: string | null;
  from_state: TableStateName | null;
  to_state: TableStateName | null;
  payload: Record<string, any>;
  dataset?: string;
  run_id?: string;
}

export interface LayoutTable {
  id: string;
  label: string;
  area: string;
  seats: number;
  center: [number, number];
  size: [number, number];
  shape: string;
  chairs: [number, number][];
}

export interface Layout {
  id: string;
  name: string;
  width_m: number;
  height_m: number;
  areas: { id: string; name: string; rect: [number, number, number, number] }[];
  fixtures: { id: string; name: string; kind: string; rect: [number, number, number, number] }[];
  tables: LayoutTable[];
  sim_camera: { id: string; anchor: string; zones_version: number; zones: { table_id: string; polygon: [number, number][] }[] };
}

export interface ZoneDef {
  table_id: string;
  polygon: [number, number][];
}

export interface CameraConfig {
  id: string;
  name: string;
  kind: "simulation" | "file" | "webcam" | "rtsp";
  enabled: boolean;
  builtin: boolean;
  config: Record<string, any>;
}

export interface VisionStatus {
  name: string;
  path: string;
  present: boolean;
  verified: boolean;
  onnxruntime: boolean;
  opencv: boolean;
  ready: boolean;
  license: string;
}

export interface Stats {
  count: number;
  mean_s: number | null;
  median_s: number | null;
  p90_s: number | null;
  max_s: number | null;
}

export interface Analytics {
  dataset: Mode;
  simulated: boolean;
  scope: string;
  runs: { id: string; label: string | null; kind: string; start: string; hours: number }[];
  period_hours: number;
  tables: number;
  insufficient_data: boolean;
  kpis: {
    occupancy_rate: number | null;
    occupied_table_minutes: number;
    observed_table_minutes: number;
    sessions: number;
    completed_sessions: number;
    sessions_with_confirmed_service: number;
    service_response: Stats;
    unserved_warnings: number;
    avg_occupied_session_s: number | null;
    occupied_session: Stats;
    cleaning_turnaround: Stats;
    table_turnover: Stats;
    overdue_cleanings: number;
    peak_occupied_tables: number;
    peak_at: string | null;
    camera_uptime: number | null;
    camera_outages: number;
    manual_corrections: number;
    possible_staff_visits: number;
    service_confirmation_sources: Record<string, number>;
  };
  charts: {
    occupancy_by_hour: { hour: number; label: string; occupancy: number | null; observed_table_hours: number; peak_occupied: number }[];
    occupancy_by_weekday: { weekday: string; occupancy: number | null; observed_table_hours: number }[];
    heatmap: { weekday: string; hour: number; occupancy: number | null; peak_occupied: number }[];
    service_response_hist: { bucket: string; count: number }[];
    cleaning_hist: { bucket: string; count: number }[];
    turnover_by_table: { table_id: string; sessions: number; avg_occupied_min: number | null; avg_turnover_min: number | null; unserved_alerts: number }[];
    alerts_over_time: Record<string, string | number>[];
    alert_types: string[];
  };
  staffing: {
    tables_per_server: number;
    peak_occupied_tables: number;
    estimated_servers_at_peak: number;
    by_hour: { hour: number; label: string; peak_occupied: number; estimated_servers: number }[];
    disclaimer: string;
  };
}

export interface SettingsResponse {
  thresholds: Record<string, number>;
  thresholds_version: number;
  threshold_limits: Record<string, { min: number; max: number; description: string }>;
  simulation: { default_scenario: string; seed: number | null; staff_auto_confirm: boolean; autoplay: boolean; speed: number };
  privacy: { event_retention_days: number; store_debug_video: boolean; debug_video_retention_hours: number };
  scenarios: Scenario[];
  bind_host: string;
}
