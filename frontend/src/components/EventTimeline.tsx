import {
  AlertTriangle,
  BellOff,
  BellRing,
  CheckCircle2,
  CircleDashed,
  Eye,
  HelpCircle,
  LogIn,
  LogOut,
  Pencil,
  ShieldCheck,
  Sparkles,
  UserRound,
  Wifi,
  WifiOff,
} from "lucide-react";
import { EVENT_LABEL, SOURCE_LABEL, SOURCE_STYLE, clockTime, eventDetail } from "../lib/format";
import type { SourceName, TWEvent } from "../types/api";
import { Empty } from "./ui";

const ICONS: Record<string, typeof LogIn> = {
  GUESTS_SEATED: LogIn,
  POSSIBLE_STAFF_VISIT: UserRound,
  SERVICE_CONFIRMED: CheckCircle2,
  GUESTS_DEPARTED: LogOut,
  CLEANING_CONFIRMED: Sparkles,
  STATUS_CORRECTED: Pencil,
  OBSERVATION_VERIFIED: Eye,
  LOW_CONFIDENCE_STARTED: HelpCircle,
  LOW_CONFIDENCE_ENDED: ShieldCheck,
  CAMERA_OFFLINE: WifiOff,
  CAMERA_ONLINE: Wifi,
  ALERT_RAISED: BellRing,
  ALERT_ACKNOWLEDGED: BellOff,
  ALERT_RESOLVED: CheckCircle2,
};

const TONE: Record<string, string> = {
  GUESTS_SEATED: "text-st-seated",
  SERVICE_CONFIRMED: "text-st-served",
  GUESTS_DEPARTED: "text-st-cleaning",
  CLEANING_CONFIRMED: "text-st-free",
  ALERT_RAISED: "text-st-overdue",
  CAMERA_OFFLINE: "text-slate-300",
  LOW_CONFIDENCE_STARTED: "text-slate-300",
  STATUS_CORRECTED: "text-emerald-300",
};

export function SourceChip({ source }: { source: SourceName }) {
  return <span className={`chip ${SOURCE_STYLE[source] ?? ""}`}>{SOURCE_LABEL[source] ?? source}</span>;
}

export function EventList({ events, compact = false, showTable = true, limit }: {
  events: TWEvent[];
  compact?: boolean;
  showTable?: boolean;
  limit?: number;
}) {
  const list = limit ? events.slice(0, limit) : events;
  if (!list.length) return <Empty title="No events yet" body="Events appear here as the state machine records them." />;
  return (
    <ol className="relative space-y-0.5" aria-label="Event timeline">
      {list.map((e) => {
        const Icon = ICONS[e.type] ?? (e.type.includes("ALERT") ? AlertTriangle : CircleDashed);
        const detail = eventDetail(e);
        return (
          <li key={e.id} className="animate-in flex gap-3 rounded-lg px-2 py-1.5 hover:bg-ink-750/60">
            <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${TONE[e.type] ?? "text-txt-muted"}`} aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-sm font-medium text-txt">{EVENT_LABEL[e.type] ?? e.type}</span>
                {showTable && (e.table_id || e.camera_id) && <span className="text-xs text-txt-muted">{e.table_id ?? e.camera_id}</span>}
                {!compact && <SourceChip source={e.source} />}
              </div>
              {detail && <p className="truncate text-xs text-txt-muted" title={detail}>{detail}</p>}
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <time className="font-mono text-[11px] text-txt-faint">{clockTime(e.ts ?? new Date(e.wall * 1000).toISOString())}</time>
              {compact && <SourceChip source={e.source} />}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
