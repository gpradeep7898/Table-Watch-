import { useState } from "react";
import { BellOff, CheckCircle2, Eye, Sparkles } from "lucide-react";
import { ApiError, api } from "../services/api";
import { useLive } from "../hooks/useLive";
import { useToasts } from "../hooks/useToasts";
import { ALERT_LABEL, clockTime, duration } from "../lib/format";
import type { Alert } from "../types/api";
import { useActor } from "./TableDetail";

const SEV: Record<string, string> = {
  critical: "border-red-400/50 bg-red-500/10",
  warning: "border-amber-400/40 bg-amber-500/10",
  info: "border-sky-400/30 bg-sky-500/5",
};

/** One alert with its valid operational actions. */
export function AlertRow({ alert, onOpenTable, compact = false }: { alert: Alert; onOpenTable?: (id: string) => void; compact?: boolean }) {
  const { snapshot } = useLive();
  const { push } = useToasts();
  const actor = useActor();
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      push("success", ok);
    } catch (e) {
      push("error", "Action rejected", (e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  const open = alert.status !== "RESOLVED";
  const table = snapshot?.tables.find((t) => t.table_id === alert.table_id);
  const age = alert.created_t !== undefined && snapshot ? duration(snapshot.clock.t - alert.created_t) : null;
  const resolveAction =
    open && alert.table_id
      ? alert.type === "UNSERVED_TOO_LONG" && table?.state === "SEATED_UNSERVED"
        ? { label: "Confirm service", icon: CheckCircle2, fn: () => api.tableAction(alert.table_id!, "confirm_service", { actor }), ok: "Service confirmed" }
        : (alert.type === "TABLE_NEEDS_CLEANING" || alert.type === "CLEANING_OVERDUE") && table?.state === "NEEDS_CLEANING"
          ? { label: "Confirm cleaning", icon: Sparkles, fn: () => api.tableAction(alert.table_id!, "confirm_cleaning", { actor }), ok: "Cleaning confirmed" }
          : alert.type === "LOW_CONFIDENCE_OBSERVATION"
            ? { label: "Verify table", icon: Eye, fn: () => api.tableAction(alert.table_id!, "verify_observation", { actor, reason: "Checked in person" }), ok: "Observation verified" }
            : null
      : null;
  return (
    <div className={`animate-in rounded-lg border p-3 ${open ? SEV[alert.severity] : "border-ink-600 bg-ink-850 opacity-80"}`} data-testid={`alert-${alert.type}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {ALERT_LABEL[alert.type]}
            {alert.table_id && (
              <button className="text-xs font-medium text-brand underline-offset-2 hover:underline" onClick={() => onOpenTable?.(alert.table_id!)} disabled={!onOpenTable}>
                {table?.label ?? alert.table_id}
              </button>
            )}
            <span className="chip border-ink-500 text-[10px] uppercase text-txt-muted">{alert.severity}</span>
            <span className={`chip text-[10px] ${alert.status === "ACTIVE" ? "border-red-400/40 text-red-200" : alert.status === "ACKNOWLEDGED" ? "border-amber-400/40 text-amber-200" : "border-emerald-400/40 text-emerald-200"}`}>
              {alert.status.toLowerCase()}
            </span>
          </p>
          <p className="mt-0.5 text-xs text-txt-muted">
            {alert.message}
            {" · "}
            {clockTime(alert.created_ts)}
            {age && open ? ` (${age} ago)` : ""}
          </p>
          {!compact && alert.next_step && open && <p className="mt-1 text-xs text-txt">Next step: {alert.next_step}</p>}
          {!open && alert.resolution && <p className="mt-1 text-xs text-emerald-300">Resolved: {alert.resolution.replace(/_/g, " ")}</p>}
          {alert.acknowledged_by && <p className="mt-0.5 text-[11px] text-txt-faint">Acknowledged by {alert.acknowledged_by}</p>}
        </div>
        {open && (
          <div className="flex shrink-0 gap-1.5">
            {resolveAction && (
              <button className="btn-primary px-2.5 py-1 text-xs" disabled={busy} onClick={() => run(resolveAction.fn, resolveAction.ok)}>
                <resolveAction.icon className="h-3.5 w-3.5" /> {resolveAction.label}
              </button>
            )}
            {alert.status === "ACTIVE" && (
              <button className="btn-secondary px-2.5 py-1 text-xs" disabled={busy} onClick={() => run(() => api.acknowledge(alert.id, actor), "Alert acknowledged")} data-testid="ack-alert">
                <BellOff className="h-3.5 w-3.5" /> Acknowledge
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
