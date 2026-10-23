import type { AlertType, Health, SourceName, TableStateName, TableView } from "../types/api";

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function minutes(seconds: number | null | undefined, digits = 1): string {
  if (seconds === null || seconds === undefined) return "—";
  return `${(seconds / 60).toFixed(digits)} min`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function clockTime(iso: string | undefined | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso: string | undefined | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const STATE_LABEL: Record<TableStateName, string> = {
  FREE: "Available",
  SEATED_UNSERVED: "Awaiting service",
  SERVED: "Served",
  NEEDS_CLEANING: "Needs cleaning",
};

export interface Look {
  label: string;
  color: string; // hex
  text: string; // tailwind text class
  bg: string; // tailwind bg class
  border: string;
}

export function tableLook(t: Pick<TableView, "state" | "unserved_overdue" | "cleaning_overdue" | "health">): Look {
  if (t.state === "SEATED_UNSERVED" && t.unserved_overdue)
    return { label: "Waiting too long", color: "#F87171", text: "text-st-overdue", bg: "bg-st-overdue/15", border: "border-st-overdue/50" };
  switch (t.state) {
    case "FREE":
      return { label: STATE_LABEL.FREE, color: "#2DD4BF", text: "text-st-free", bg: "bg-st-free/10", border: "border-st-free/40" };
    case "SEATED_UNSERVED":
      return { label: STATE_LABEL.SEATED_UNSERVED, color: "#60A5FA", text: "text-st-seated", bg: "bg-st-seated/10", border: "border-st-seated/40" };
    case "SERVED":
      return { label: STATE_LABEL.SERVED, color: "#A78BFA", text: "text-st-served", bg: "bg-st-served/10", border: "border-st-served/40" };
    case "NEEDS_CLEANING":
      return {
        label: t.cleaning_overdue ? "Cleaning overdue" : STATE_LABEL.NEEDS_CLEANING,
        color: "#FBBF24",
        text: "text-st-cleaning",
        bg: t.cleaning_overdue ? "bg-st-cleaning/20" : "bg-st-cleaning/10",
        border: "border-st-cleaning/50",
      };
  }
}

export const HEALTH_LABEL: Record<Health, string> = {
  UNKNOWN: "No camera data",
  OK: "Observed",
  LOW_CONFIDENCE: "Low confidence",
  DEGRADED: "Degraded",
  OFFLINE: "Camera offline",
};

export function isUncertain(h: Health) {
  return h === "OFFLINE" || h === "LOW_CONFIDENCE" || h === "DEGRADED" || h === "UNKNOWN";
}

export const ALERT_LABEL: Record<AlertType, string> = {
  UNSERVED_TOO_LONG: "Waiting too long",
  TABLE_NEEDS_CLEANING: "Needs cleaning",
  CLEANING_OVERDUE: "Cleaning overdue",
  CAMERA_OFFLINE: "Camera offline",
  LOW_CONFIDENCE_OBSERVATION: "Uncertain observation",
};

export const SOURCE_LABEL: Record<SourceName, string> = {
  simulator: "Simulator",
  computer_vision: "Computer vision",
  operator: "Operator",
  system: "System",
};

export const SOURCE_STYLE: Record<SourceName, string> = {
  simulator: "border-fuchsia-400/40 bg-fuchsia-400/10 text-fuchsia-200",
  computer_vision: "border-sky-400/40 bg-sky-400/10 text-sky-200",
  operator: "border-emerald-400/40 bg-emerald-400/10 text-emerald-200",
  system: "border-slate-400/40 bg-slate-400/10 text-slate-200",
};

export const EVENT_LABEL: Record<string, string> = {
  GUESTS_SEATED: "Guests seated",
  POSSIBLE_STAFF_VISIT: "Possible staff visit",
  SERVICE_CONFIRMED: "Service confirmed",
  GUESTS_DEPARTED: "Guests departed",
  CLEANING_CONFIRMED: "Cleaning confirmed",
  STATUS_CORRECTED: "Status corrected",
  OBSERVATION_VERIFIED: "Observation verified",
  LOW_CONFIDENCE_STARTED: "Observation uncertain",
  LOW_CONFIDENCE_ENDED: "Observation clear again",
  CAMERA_OFFLINE: "Camera offline (observation gap)",
  CAMERA_ONLINE: "Camera back online",
  ALERT_RAISED: "Alert raised",
  ALERT_ACKNOWLEDGED: "Alert acknowledged",
  ALERT_RESOLVED: "Alert resolved",
};

export function eventDetail(e: { type: string; payload: Record<string, any> }): string {
  const p = e.payload || {};
  switch (e.type) {
    case "GUESTS_SEATED":
      return `${p.guest_count ?? "?"} guest(s) · evidence ${Math.round((p.evidence_quality ?? 0) * 100)}% stable`;
    case "SERVICE_CONFIRMED":
      return `Waited ${duration(p.wait_s)} · by ${p.actor ?? "?"}`;
    case "GUESTS_DEPARTED":
      return `Occupied ${duration(p.occupied_s)}${p.during_observation_gap ? " · left during an observation gap (time uncertain)" : ""}`;
    case "CLEANING_CONFIRMED":
      return `Cleaning took ${duration(p.cleaning_s)} · turnover ${duration(p.turnover_s)} · by ${p.actor ?? "?"}`;
    case "POSSIBLE_STAFF_VISIT":
      return `${p.kind === "possible_cleaning_visit" ? "Possible cleaning visit" : "Unverified visit"} · ${Math.round(p.duration_s ?? 0)}s · not counted as service`;
    case "STATUS_CORRECTED":
      return `“${p.reason ?? ""}” · by ${p.actor ?? "?"}`;
    case "ALERT_RAISED":
      return p.alert?.message ?? "";
    case "ALERT_RESOLVED":
      return `${ALERT_LABEL[p.alert_type as AlertType] ?? p.alert_type} · ${String(p.resolution ?? "").replace(/_/g, " ")}`;
    case "ALERT_ACKNOWLEDGED":
      return `${ALERT_LABEL[p.alert_type as AlertType] ?? p.alert_type} · by ${p.actor ?? "?"}`;
    case "LOW_CONFIDENCE_STARTED":
      return (p.reasons ?? []).map((r: string) => r.replace(/_/g, " ")).join(", ");
    case "CAMERA_ONLINE":
      return `Gap of ${duration(p.gap_s)}`;
    case "OBSERVATION_VERIFIED":
      return `by ${p.actor ?? "?"}${p.note ? ` · “${p.note}”` : ""}`;
    default:
      return "";
  }
}

export function getLocal<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}

export function setLocal(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: preference just won't persist */
  }
}
