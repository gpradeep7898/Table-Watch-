import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { History } from "lucide-react";
import { EventList } from "../components/EventTimeline";
import { Card, ErrorText, SimBadge } from "../components/ui";
import { useLive } from "../hooks/useLive";
import { ApiError, api } from "../services/api";
import type { TWEvent } from "../types/api";

const FILTERS: { id: string; label: string; types: string }[] = [
  { id: "", label: "Everything", types: "" },
  { id: "table", label: "Table lifecycle", types: "GUESTS_SEATED,SERVICE_CONFIRMED,GUESTS_DEPARTED,CLEANING_CONFIRMED,STATUS_CORRECTED" },
  { id: "visits", label: "Possible staff visits", types: "POSSIBLE_STAFF_VISIT" },
  { id: "alerts", label: "Alerts", types: "ALERT_RAISED,ALERT_ACKNOWLEDGED,ALERT_RESOLVED" },
  { id: "gaps", label: "Observation gaps", types: "LOW_CONFIDENCE_STARTED,LOW_CONFIDENCE_ENDED,OBSERVATION_VERIFIED,CAMERA_OFFLINE,CAMERA_ONLINE" },
];

export function TableHistoryPage() {
  const { snapshot, layout, events: live } = useLive();
  const [params, setParams] = useSearchParams();
  const table = params.get("table") ?? layout?.tables[0]?.id ?? "T1";
  const [filter, setFilter] = useState("");
  const [scope, setScope] = useState<"current" | "all">("current");
  const [events, setEvents] = useState<TWEvent[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const types = FILTERS.find((f) => f.id === filter)?.types ?? "";

  const load = useCallback(
    async (before?: number) => {
      try {
        const camera = snapshot?.tables.find((t) => t.table_id === table)?.camera_id;
        const res = await api.events({ scope, table_id: table, types: types || undefined, limit: 100, before_seq: before });
        let rows = res.events;
        if (!before && camera && (!types || types.includes("CAMERA"))) {
          // Camera gaps are camera-level events; include them so the table's timeline shows them.
          const cam = await api.events({ scope, types: "CAMERA_OFFLINE,CAMERA_ONLINE", limit: 100 });
          rows = [...rows, ...cam.events.filter((e) => e.camera_id === camera)].sort((a, b) => (b.seq ?? 0) - (a.seq ?? 0));
        }
        setEvents((prev) => (before ? [...prev, ...rows] : rows));
        setMore(res.events.length === 100);
        setError(null);
      } catch (e) {
        setError((e as ApiError).message);
      }
    },
    [scope, table, types, snapshot?.tables],
  );

  const liveCount = live.filter((e) => e.table_id === table).length;
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, table, types, liveCount, snapshot?.run?.id]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            Table history {snapshot?.mode === "simulation" && <SimBadge compact />}
          </h1>
          <p className="text-sm text-txt-muted">Every event is labelled with where it came from: simulator, computer vision, operator or system timer.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="h-table">Table</label>
          <select id="h-table" className="input w-auto" value={table} onChange={(e) => setParams({ table: e.target.value })}>
            {layout?.tables.map((t) => (
              <option key={t.id} value={t.id}>{t.label}</option>
            ))}
          </select>
          <label className="sr-only" htmlFor="h-filter">Event filter</label>
          <select id="h-filter" className="input w-auto" value={filter} onChange={(e) => setFilter(e.target.value)}>
            {FILTERS.map((f) => (
              <option key={f.id} value={f.id}>{f.label}</option>
            ))}
          </select>
          <label className="sr-only" htmlFor="h-scope">Scope</label>
          <select id="h-scope" className="input w-auto" value={scope} onChange={(e) => setScope(e.target.value as "current" | "all")}>
            <option value="current">This run</option>
            <option value="all">All runs</option>
          </select>
        </div>
      </div>
      <Card title={`Timeline · ${layout?.tables.find((t) => t.id === table)?.label ?? table}`} icon={<History className="h-4 w-4 text-txt-muted" />}>
        <ErrorText error={error} />
        <EventList events={events} showTable={false} />
        {more && (
          <div className="mt-3 flex justify-center">
            <button className="btn-secondary" onClick={() => load(events[events.length - 1]?.seq)}>
              Load older events
            </button>
          </div>
        )}
      </Card>
    </div>
  );
}
