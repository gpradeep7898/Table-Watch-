import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Bell, CheckCircle2, CircleDashed, Clock3, LayoutGrid, Map as MapIcon, Users } from "lucide-react";
import { AlertRow } from "../components/AlertRow";
import { EventList } from "../components/EventTimeline";
import { FloorPlan, Legend } from "../components/FloorPlan";
import { SimControls } from "../components/SimControls";
import { TableDetail } from "../components/TableDetail";
import { Card, Empty, Stat } from "../components/ui";
import { useLive } from "../hooks/useLive";

export function SummaryCards() {
  const { snapshot } = useLive();
  const s = snapshot?.summary;
  if (!s) return null;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Stat testId="kpi-total" label="Total tables" value={s.total} icon={<LayoutGrid className="h-4 w-4" />} />
      <Stat testId="kpi-free" label="Available" value={s.free} tone="text-st-free" icon={<CheckCircle2 className="h-4 w-4" />} />
      <Stat testId="kpi-occupied" label="Occupied" value={s.occupied} tone="text-st-seated" sub={`${s.served} served`} icon={<Users className="h-4 w-4" />} />
      <Stat
        testId="kpi-awaiting"
        label="Awaiting service"
        value={s.awaiting_service}
        tone={s.awaiting_service ? "text-st-seated" : "text-txt"}
        sub="no confirmed service yet"
        icon={<Clock3 className="h-4 w-4" />}
      />
      <Stat testId="kpi-cleaning" label="Need cleaning" value={s.needs_cleaning} tone={s.needs_cleaning ? "text-st-cleaning" : "text-txt"} icon={<CircleDashed className="h-4 w-4" />} />
      <Stat
        testId="kpi-alerts"
        label="Active alerts"
        value={s.active_alerts}
        tone={s.active_alerts ? "text-st-overdue" : "text-txt"}
        sub={s.open_alerts > s.active_alerts ? `${s.open_alerts - s.active_alerts} acknowledged` : s.uncertain_tables ? `${s.uncertain_tables} uncertain table(s)` : undefined}
        icon={<Bell className="h-4 w-4" />}
      />
    </div>
  );
}

export function OverviewPage() {
  const { snapshot, layout, events } = useLive();
  const [selected, setSelected] = useState<string | null>(null);
  if (!snapshot || !layout) return <p className="text-sm text-txt-muted">Connecting to TableWatch…</p>;
  const alerts = snapshot.alerts.filter((a) => a.status !== "RESOLVED");
  return (
    <div className="space-y-5">
      <SimControls />
      <SummaryCards />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <Card
          title="Floor plan"
          icon={<MapIcon className="h-4 w-4 text-txt-muted" />}
          actions={
            <Link to="/floor" className="btn-ghost px-2 py-1 text-xs">
              Open live floor
            </Link>
          }
        >
          <FloorPlan layout={layout} tables={snapshot.tables} selected={selected} onSelect={setSelected} />
          <div className="mt-3">
            <Legend />
          </div>
        </Card>
        <div className="space-y-5">
          {selected ? (
            <TableDetail tableId={selected} onClose={() => setSelected(null)} />
          ) : (
            <Card title={`Needs attention (${alerts.length})`} icon={<AlertTriangle className="h-4 w-4 text-st-overdue" />} actions={<Link to="/alerts" className="btn-ghost px-2 py-1 text-xs">Alert center</Link>}>
              {alerts.length ? (
                <div className="space-y-2">
                  {alerts.slice(0, 5).map((a) => (
                    <AlertRow key={a.id} alert={a} onOpenTable={setSelected} compact />
                  ))}
                </div>
              ) : (
                <Empty title="All clear" body="No table needs attention right now. Click any table for details." icon={<CheckCircle2 className="h-5 w-5 text-st-free" />} />
              )}
            </Card>
          )}
          <Card title="Live event feed" pad={false}>
            <div className="max-h-[26rem] overflow-auto p-2">
              <EventList events={events.filter((e) => e.type !== "ALERT_ACKNOWLEDGED")} limit={40} compact />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
