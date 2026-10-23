import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, Volume2, VolumeX } from "lucide-react";
import { AlertRow } from "../components/AlertRow";
import { Card, Empty, ErrorText, Toggle } from "../components/ui";
import { useLive } from "../hooks/useLive";
import { useToasts } from "../hooks/useToasts";
import { ApiError, api } from "../services/api";
import { ALERT_LABEL } from "../lib/format";
import type { Alert, AlertType } from "../types/api";

export function AlertsPage() {
  const { snapshot, events } = useLive();
  const { soundEnabled, setSoundEnabled } = useToasts();
  const navigate = useNavigate();
  const [type, setType] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [scope, setScope] = useState<"current" | "all">("current");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);

  const alertEvents = useMemo(() => events.filter((e) => e.type.startsWith("ALERT_")).length, [events]);
  useEffect(() => {
    let alive = true;
    api
      .alerts({ scope, status: status || undefined, type: type || undefined, limit: 500 })
      .then((r) => alive && (setAlerts(r.alerts), setError(null)))
      .catch((e: ApiError) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [scope, status, type, alertEvents, snapshot?.run?.id]);

  // Live timers come from the snapshot; history comes from the database.
  const live = new Map((snapshot?.alerts ?? []).map((a) => [a.id, a]));
  const merged = alerts.map((a) => ({ ...a, ...(live.get(a.id) ?? {}) }));
  const counts = { ACTIVE: 0, ACKNOWLEDGED: 0, RESOLVED: 0 } as Record<string, number>;
  merged.forEach((a) => (counts[a.status] += 1));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Alert center</h1>
          <p className="text-sm text-txt-muted">
            Alerts are deduplicated per table visit. Acknowledging means “someone is on it”. Alerts resolve through real actions: confirmed service, confirmed cleaning, a verified observation or a recovered camera.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {soundEnabled ? <Volume2 className="h-4 w-4 text-txt-muted" /> : <VolumeX className="h-4 w-4 text-txt-muted" />}
          <Toggle id="sound" label="Sound for new alerts" checked={soundEnabled} onChange={setSoundEnabled} />
        </div>
      </div>
      <Card
        title={`${counts.ACTIVE} active · ${counts.ACKNOWLEDGED} acknowledged · ${counts.RESOLVED} resolved`}
        icon={<Bell className="h-4 w-4 text-txt-muted" />}
        actions={
          <div className="flex flex-wrap gap-2">
            <label className="sr-only" htmlFor="f-type">Type</label>
            <select id="f-type" className="input w-auto py-1.5" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All types</option>
              {(Object.keys(ALERT_LABEL) as AlertType[]).map((t) => (
                <option key={t} value={t}>{ALERT_LABEL[t]}</option>
              ))}
            </select>
            <label className="sr-only" htmlFor="f-status">Status</label>
            <select id="f-status" className="input w-auto py-1.5" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any status</option>
              <option value="ACTIVE">Active</option>
              <option value="ACKNOWLEDGED">Acknowledged</option>
              <option value="RESOLVED">Resolved</option>
            </select>
            <label className="sr-only" htmlFor="f-scope">Scope</label>
            <select id="f-scope" className="input w-auto py-1.5" value={scope} onChange={(e) => setScope(e.target.value as "current" | "all")}>
              <option value="current">This run</option>
              <option value="all">All runs ({snapshot?.dataset ?? "…"})</option>
            </select>
          </div>
        }
      >
        <ErrorText error={error} />
        {merged.length ? (
          <div className="space-y-2">
            {merged.map((a) => (
              <AlertRow key={a.id} alert={a} onOpenTable={(id) => navigate(`/floor?table=${id}`)} />
            ))}
          </div>
        ) : (
          <Empty title="No alerts match" body="Try another filter. In the simulator, scenario B (forgotten customers) raises a waiting alert within a few minutes." />
        )}
      </Card>
    </div>
  );
}
