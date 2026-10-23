import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BellOff, CheckCircle2, ClipboardCheck, Eye, History, Pencil, Sparkles, X } from "lucide-react";
import { ApiError, api, type TableAction } from "../services/api";
import { useLive } from "../hooks/useLive";
import { useToasts } from "../hooks/useToasts";
import { ALERT_LABEL, STATE_LABEL, duration, getLocal } from "../lib/format";
import type { TableStateName, TWEvent } from "../types/api";
import { EventList } from "./EventTimeline";
import { ErrorText, HealthBadge, Modal, StateBadge } from "./ui";

export function useActor(): string {
  return getLocal("tw.actor", "Manager");
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <dt className="text-txt-muted">{label}</dt>
      <dd className="text-right font-medium tabular-nums text-txt">{value}</dd>
    </div>
  );
}

export function TableDetail({ tableId, onClose, inline = false }: { tableId: string; onClose: () => void; inline?: boolean }) {
  const { snapshot } = useLive();
  const { push } = useToasts();
  const actor = useActor();
  const table = snapshot?.tables.find((t) => t.table_id === tableId);
  const [events, setEvents] = useState<TWEvent[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.table(tableId);
      setEvents(res.events);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, [tableId]);

  const stateKey = `${table?.state}|${table?.alerts.map((a) => a.id + a.status).join(",")}|${table?.visits}|${table?.health}`;
  useEffect(() => {
    load();
  }, [load, stateKey]);
  useEffect(() => setError(null), [tableId]);

  if (!snapshot) return null;
  if (!table) {
    return (
      <aside className="card p-5 text-sm text-txt-muted">
        Table {tableId} is not part of this restaurant layout.
      </aside>
    );
  }

  const run = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      push("success", done);
      await load();
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(null);
    }
  };
  const act = (action: TableAction, done: string, extra: Record<string, string> = {}) =>
    run(action, () => api.tableAction(tableId, action, { actor, ...extra }), done);

  const open = table.alerts.filter((a) => a.status !== "RESOLVED");
  const lowConf = open.some((a) => a.type === "LOW_CONFIDENCE_OBSERVATION");
  const t = snapshot.clock.t;
  const ago = (x: number | null) => (x === null ? "—" : `${duration(t - x)} ago`);

  return (
    <aside className={`card flex flex-col ${inline ? "" : "max-h-[calc(100vh-7rem)]"}`} aria-label={`${table.label} details`} data-testid="table-detail">
      <header className="flex items-start justify-between gap-3 border-b border-ink-700 px-5 py-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-txt-muted">{table.area} · {table.seats} seats</p>
          <h2 className="text-lg font-semibold">{table.label}</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            <StateBadge table={table} />
            <HealthBadge health={table.health} />
          </div>
        </div>
        <button className="btn-ghost p-1.5" onClick={onClose} aria-label="Close table details">
          <X className="h-4 w-4" />
        </button>
      </header>
      <div className="flex-1 overflow-auto px-5 py-4">
        <dl className="divide-y divide-ink-700/60">
          <Row label="Current state" value={STATE_LABEL[table.state]} />
          <Row label="Seated" value={table.seated_t !== null ? ago(table.seated_t) : "—"} />
          <Row label="Service confirmation" value={table.service_t !== null ? `${ago(table.service_t)} (${table.service_source})` : table.state === "SEATED_UNSERVED" ? "Not confirmed" : "—"} />
          <Row label={table.state === "SEATED_UNSERVED" ? "Waiting" : "Waited for service"} value={<span className={table.unserved_overdue ? "text-st-overdue" : ""}>{duration(table.wait_s)}</span>} />
          <Row label="Time occupied" value={duration(table.occupied_s)} />
          <Row label="Cleaning" value={table.state === "NEEDS_CLEANING" ? <span className={table.cleaning_overdue ? "text-st-cleaning" : ""}>Needed for {duration(table.cleaning_s)}</span> : "Not needed"} />
          <Row label="People in zone now" value={table.present_count ?? "No camera"} />
          <Row label="Possible staff visits" value={`${table.visits} (unverified)`} />
          <Row label="Observation" value={`${table.health.replace("_", " ").toLowerCase()}${table.uncertain_count ? ` · ${table.uncertain_count} uncertain track(s)` : ""}`} />
        </dl>

        {table.uncertainty.length > 0 && (
          <div className="mt-3 rounded-lg border border-slate-400/30 bg-slate-400/5 p-3 text-xs text-slate-200">
            <p className="mb-1 font-semibold">Why this status may be uncertain</p>
            <ul className="list-disc space-y-0.5 pl-4">
              {table.uncertainty.map((u) => (
                <li key={u}>{u}</li>
              ))}
            </ul>
          </div>
        )}

        {open.length > 0 && (
          <div className="mt-4 space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-txt-muted">Open alerts</h3>
            {open.map((a) => (
              <div key={a.id} className="flex items-center justify-between gap-2 rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm">
                <div>
                  <p className="font-medium">{ALERT_LABEL[a.type]}</p>
                  <p className="text-xs text-txt-muted">{a.status === "ACKNOWLEDGED" ? `Acknowledged by ${a.acknowledged_by}` : "Not acknowledged"}</p>
                </div>
                {a.status === "ACTIVE" && (
                  <button
                    className="btn-secondary px-2 py-1 text-xs"
                    disabled={busy !== null}
                    onClick={() => run(`ack-${a.id}`, () => api.acknowledge(a.id, actor), "Alert acknowledged")}
                  >
                    <BellOff className="h-3.5 w-3.5" /> Acknowledge
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-2">
          <button
            className="btn-primary"
            disabled={table.state !== "SEATED_UNSERVED" || busy !== null}
            title={table.state === "SERVED" ? "Service already confirmed" : table.state !== "SEATED_UNSERVED" ? "Only seated, unserved guests can be served" : ""}
            onClick={() => act("confirm_service", `Service confirmed at ${table.label}`)}
            data-testid="confirm-service"
          >
            <CheckCircle2 className="h-4 w-4" /> Confirm service
          </button>
          <button
            className="btn-primary"
            disabled={table.state !== "NEEDS_CLEANING" || busy !== null}
            title={table.state !== "NEEDS_CLEANING" ? "Only a table that needs cleaning can be marked clean" : ""}
            onClick={() => act("confirm_cleaning", `${table.label} is clean and available`)}
            data-testid="confirm-cleaning"
          >
            <Sparkles className="h-4 w-4" /> Confirm cleaning
          </button>
          <button className="btn-secondary" disabled={busy !== null} onClick={() => setCorrecting(true)} data-testid="correct-status">
            <Pencil className="h-4 w-4" /> Correct status
          </button>
          {lowConf ? (
            <button className="btn-secondary" disabled={busy !== null} onClick={() => act("verify_observation", "Observation verified", { reason: "Checked in person" })}>
              <Eye className="h-4 w-4" /> Verify table
            </button>
          ) : (
            <Link className="btn-secondary" to={`/history?table=${table.table_id}`}>
              <History className="h-4 w-4" /> Full history
            </Link>
          )}
        </div>
        <ErrorText error={error} />
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-txt-faint">
          <ClipboardCheck className="h-3.5 w-3.5" /> Every action is logged with your name ({actor}), time and source.
        </p>

        <h3 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wide text-txt-muted">Recent events</h3>
        <EventList events={events} compact showTable={false} />
      </div>
      <CorrectStatusDialog
        open={correcting}
        current={table.state}
        served={table.service_t !== null}
        onClose={() => setCorrecting(false)}
        onSubmit={async (to, reason) => {
          await api.tableAction(tableId, "correct_status", { to_state: to, reason, actor });
          push("success", `${table.label} corrected to ${STATE_LABEL[to]}`);
          setCorrecting(false);
          load();
        }}
      />
    </aside>
  );
}

const OPTIONS: { to: TableStateName; label: string; help: string }[] = [
  { to: "FREE", label: "Available", help: "Nobody is there and the table is ready. Closes the current session as corrected; history is kept." },
  { to: "SEATED_UNSERVED", label: "Guests seated, not served", help: "The camera missed guests (or they are still here after a false departure)." },
  { to: "SERVED", label: "Guests seated and served", help: "Reopen a served party after a false departure, or record a walk-in that was already served." },
  { to: "NEEDS_CLEANING", label: "Guests left, needs cleaning", help: "Guests left but the camera did not confirm it." },
];

function CorrectStatusDialog({ open, current, served, onClose, onSubmit }: {
  open: boolean;
  current: TableStateName;
  served: boolean;
  onClose: () => void;
  onSubmit: (to: TableStateName, reason: string) => Promise<void>;
}) {
  const [to, setTo] = useState<TableStateName | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) {
      setTo(null);
      setReason("");
      setError(null);
    }
  }, [open]);
  return (
    <Modal open={open} title="Correct table status" onClose={onClose}>
      <p className="mb-3 text-sm text-txt-muted">
        Corrections never erase history. They are recorded with your reason and shown on the timeline. Currently: <strong className="text-txt">{STATE_LABEL[current]}</strong>
        {served ? " (service confirmed)" : ""}.
      </p>
      <fieldset className="space-y-2">
        <legend className="label">New status</legend>
        {OPTIONS.filter((o) => o.to !== current).map((o) => (
          <label key={o.to} className={`flex cursor-pointer gap-3 rounded-lg border p-3 text-sm ${to === o.to ? "border-brand/70 bg-brand/5" : "border-ink-600 hover:border-ink-500"}`}>
            <input type="radio" name="to_state" className="mt-1 accent-teal-400" checked={to === o.to} onChange={() => setTo(o.to)} />
            <span>
              <span className="font-medium">{o.label}</span>
              <span className="block text-xs text-txt-muted">{o.help}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <label className="label mt-4" htmlFor="reason">
        Reason (required)
      </label>
      <textarea id="reason" className="input" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Party moved to the patio" />
      <ErrorText error={error} />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn-primary"
          disabled={!to || reason.trim().length < 3 || busy}
          onClick={async () => {
            if (!to) return;
            setBusy(true);
            setError(null);
            try {
              await onSubmit(to, reason.trim());
            } catch (e) {
              setError((e as ApiError).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          Save correction
        </button>
      </div>
    </Modal>
  );
}
