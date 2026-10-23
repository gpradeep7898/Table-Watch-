import { useCallback, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BarChart3, Database, Download, FlaskConical, Info, Users } from "lucide-react";
import { Card, Empty, ErrorText, SimBadge, Stat } from "../components/ui";
import { useLive } from "../hooks/useLive";
import { useToasts } from "../hooks/useToasts";
import { ApiError, api } from "../services/api";
import { ALERT_LABEL, minutes, pct } from "../lib/format";
import type { Analytics, AlertType, Mode } from "../types/api";

// Categorical slots validated (dataviz validator) against the #121C31 card surface, dark mode.
const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];
const ALERT_ORDER: AlertType[] = ["UNSERVED_TOO_LONG", "TABLE_NEEDS_CLEANING", "CLEANING_OVERDUE", "CAMERA_OFFLINE", "LOW_CONFIDENCE_OBSERVATION"];
const AXIS = { stroke: "#64748B", fontSize: 11, tickLine: false };
const GRID = <CartesianGrid stroke="#1D2944" vertical={false} />;
const TIP = {
  contentStyle: { background: "#0B1220", border: "1px solid #2A3858", borderRadius: 8, fontSize: 12, color: "#E7EDF7" },
  labelStyle: { color: "#93A3BE" },
  cursor: { fill: "rgba(148,163,184,0.08)" },
};
const BAR = { radius: [4, 4, 0, 0] as [number, number, number, number], maxBarSize: 36 };

function ChartCard({ title, children, empty, note }: { title: string; children: React.ReactNode; empty?: boolean; note?: string }) {
  return (
    <Card title={title}>
      {empty ? <Empty title="Not enough data yet" body="Let the simulation run longer, or generate a synthetic week of history." /> : <div className="h-64">{children}</div>}
      {note && <p className="mt-2 text-xs text-txt-faint">{note}</p>}
    </Card>
  );
}

function Heatmap({ data }: { data: Analytics["charts"]["heatmap"] }) {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const hours = [...new Set(data.map((d) => d.hour))].sort((a, b) => a - b);
  if (!hours.length) return <Empty title="Not enough data yet" />;
  const cell = new Map(data.map((d) => [`${d.weekday}-${d.hour}`, d]));
  const shade = (v: number | null) => (v === null ? "transparent" : `rgba(57,135,229,${0.12 + 0.88 * v})`);
  return (
    <div className="overflow-x-auto">
      <table className="border-separate" style={{ borderSpacing: 2 }} aria-label="Occupancy by weekday and hour">
        <thead>
          <tr>
            <th className="w-10" />
            {hours.map((h) => (
              <th key={h} scope="col" className="text-[10px] font-normal text-txt-faint">{String(h).padStart(2, "0")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d}>
              <th scope="row" className="pr-2 text-left text-xs font-normal text-txt-muted">{d}</th>
              {hours.map((h) => {
                const c = cell.get(`${d}-${h}`);
                return (
                  <td
                    key={h}
                    className={`h-7 w-8 min-w-[2rem] rounded ${c ? "" : "hatch"}`}
                    style={{ background: c ? shade(c.occupancy) : undefined }}
                    title={c ? `${d} ${h}:00 · occupancy ${pct(c.occupancy)} · peak ${c.peak_occupied} tables` : `${d} ${h}:00 · not observed`}
                  />
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 flex items-center gap-2 text-[11px] text-txt-faint">
        0%
        <span className="h-2 w-24 rounded" style={{ background: "linear-gradient(90deg, rgba(57,135,229,.12), rgba(57,135,229,1))" }} />
        100% occupied · <span className="hatch inline-block h-2.5 w-4 rounded border border-slate-500/50" /> not observed (excluded, not zero)
      </div>
    </div>
  );
}

export function AnalyticsPage() {
  const { snapshot } = useLive();
  const { push } = useToasts();
  const [dataset, setDataset] = useState<Mode>("simulation");
  const [scope, setScope] = useState<"current" | "all" | "history">("current");
  const [ratio, setRatio] = useState(4);
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<{ status: string; done: number; total: number } | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.analytics({ dataset, scope: dataset === "simulation" ? scope : "all", tables_per_waiter: ratio }));
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, [dataset, scope, ratio]);

  // Refresh as the current run produces events.
  const tick = Math.floor((snapshot?.clock.t ?? 0) / 30);
  useEffect(() => {
    load();
  }, [load, tick]);

  useEffect(() => {
    if (job?.status !== "running") return;
    const id = setInterval(async () => {
      const r = await api.historyStatus().catch(() => null);
      if (!r?.job) return;
      setJob(r.job);
      if (r.job.status !== "running") {
        push(r.job.status === "done" ? "success" : "error", r.job.status === "done" ? "Synthetic history ready" : "History generation failed", r.job.error ?? undefined);
        setScope("history");
        load();
      }
    }, 1500);
    return () => clearInterval(id);
  }, [job?.status, load, push]);

  const generate = async () => {
    try {
      const r = await api.history(7);
      setJob(r.job);
    } catch (e) {
      push("error", "Could not start", (e as ApiError).message);
    }
  };

  const k = data?.kpis;
  const sim = dataset === "simulation";
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">Analytics {sim && <SimBadge />}</h1>
          <p className="text-sm text-txt-muted">Computed only from persisted events. Camera-offline time is excluded, not counted as empty.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="a-ds">Dataset</label>
          <select id="a-ds" className="input w-auto" value={dataset} onChange={(e) => setDataset(e.target.value as Mode)}>
            <option value="simulation">Simulation data</option>
            <option value="recorded">Recorded video data</option>
            <option value="live">Live camera data</option>
          </select>
          {sim && (
            <>
              <label className="sr-only" htmlFor="a-scope">Scope</label>
              <select id="a-scope" className="input w-auto" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
                <option value="current">Current simulation run</option>
                <option value="history">Synthetic week (history)</option>
                <option value="all">All simulation runs</option>
              </select>
            </>
          )}
        </div>
      </div>

      {sim && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-fuchsia-400/30 bg-fuchsia-500/5 px-4 py-3 text-sm text-fuchsia-100">
          <FlaskConical className="h-4 w-4 shrink-0" />
          <p className="flex-1">
            These numbers describe a fictional restaurant driven by a simulator. They show how the analytics work. They say nothing about any real restaurant&apos;s performance.
          </p>
          <button className="btn-secondary px-3 py-1.5" onClick={generate} disabled={job?.status === "running"} data-testid="generate-history">
            <Database className="h-4 w-4" />
            {job?.status === "running" ? `Generating ${job.done}/${job.total} services…` : "Generate synthetic week"}
          </button>
        </div>
      )}
      <ErrorText error={error} />

      {data && k && (
        <>
          {data.insufficient_data && (
            <div className="flex items-center gap-2 rounded-lg border border-amber-400/30 bg-amber-500/5 px-3 py-2 text-sm text-amber-100" role="status">
              <Info className="h-4 w-4" /> Only {k.completed_sessions} completed table visit(s) so far. Averages below are not meaningful yet.
            </div>
          )}
          <p className="text-xs text-txt-faint">
            {data.runs.length} run(s) · {data.period_hours} h observed · {data.tables} tables
          </p>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6" data-testid="analytics-kpis">
            <Stat label="Occupancy rate" value={pct(k.occupancy_rate)} sub={`${Math.round(k.occupied_table_minutes)} of ${Math.round(k.observed_table_minutes)} observed table-min`} />
            <Stat label="Avg service response" value={minutes(k.service_response.mean_s)} sub={`median ${minutes(k.service_response.median_s)} · ${k.service_response.count} confirmed`} />
            <Stat label="Waiting alerts" value={k.unserved_warnings} tone={k.unserved_warnings ? "text-st-overdue" : "text-txt"} />
            <Stat label="Avg table visit" value={minutes(k.avg_occupied_session_s)} sub={`${k.completed_sessions} completed visits`} />
            <Stat label="Cleaning turnaround" value={minutes(k.cleaning_turnaround.mean_s)} sub={`${k.overdue_cleanings} overdue`} />
            <Stat label="Peak occupied" value={`${k.peak_occupied_tables} / ${data.tables}`} sub={k.peak_at ? `at ${k.peak_at.slice(11)}` : undefined} />
            <Stat label="Camera uptime" value={pct(k.camera_uptime, 1)} sub={`${k.camera_outages} outage(s)`} />
            <Stat label="Manual corrections" value={k.manual_corrections} />
            <Stat label="Observed sessions" value={k.sessions} sub="from camera evidence" />
            <Stat label="Service confirmed" value={k.sessions_with_confirmed_service} sub={Object.entries(k.service_confirmation_sources).map(([s, n]) => `${n} ${s}`).join(" · ") || "none"} />
            <Stat label="Possible staff visits" value={k.possible_staff_visits} sub="unverified, not service" />
            <Stat label="Table turnover" value={minutes(k.table_turnover.mean_s)} sub="guests leave → table ready" />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <ChartCard title="Occupancy by hour" empty={!data.charts.occupancy_by_hour.length}>
              <ResponsiveContainer>
                <BarChart data={data.charts.occupancy_by_hour.map((d) => ({ ...d, pct: d.occupancy === null ? null : Math.round(d.occupancy * 100) }))}>
                  {GRID}
                  <XAxis dataKey="label" {...AXIS} />
                  <YAxis {...AXIS} unit="%" domain={[0, 100]} width={40} />
                  <Tooltip {...TIP} formatter={(v: number) => [`${v}%`, "Occupancy"]} />
                  <Bar dataKey="pct" name="Occupancy" fill={SERIES[0]} {...BAR} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Occupancy by weekday" empty={!data.charts.occupancy_by_weekday.some((d) => d.occupancy !== null)} note="Days without observations show no bar.">
              <ResponsiveContainer>
                <BarChart data={data.charts.occupancy_by_weekday.map((d) => ({ ...d, pct: d.occupancy === null ? null : Math.round(d.occupancy * 100) }))}>
                  {GRID}
                  <XAxis dataKey="weekday" {...AXIS} />
                  <YAxis {...AXIS} unit="%" domain={[0, 100]} width={40} />
                  <Tooltip {...TIP} formatter={(v: number) => [`${v}%`, "Occupancy"]} />
                  <Bar dataKey="pct" name="Occupancy" fill={SERIES[0]} {...BAR} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <Card title="Occupancy heatmap (weekday × hour)">
            <Heatmap data={data.charts.heatmap} />
          </Card>

          <div className="grid gap-5 lg:grid-cols-2">
            <ChartCard title="Service response time (seated → confirmed service)" empty={!k.service_response.count}>
              <ResponsiveContainer>
                <BarChart data={data.charts.service_response_hist}>
                  {GRID}
                  <XAxis dataKey="bucket" {...AXIS} />
                  <YAxis {...AXIS} allowDecimals={false} width={32} />
                  <Tooltip {...TIP} formatter={(v: number) => [v, "Table visits"]} />
                  <Bar dataKey="count" name="Table visits" fill={SERIES[0]} {...BAR} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Cleaning turnaround (cleaning needed → confirmed)" empty={!k.cleaning_turnaround.count}>
              <ResponsiveContainer>
                <BarChart data={data.charts.cleaning_hist}>
                  {GRID}
                  <XAxis dataKey="bucket" {...AXIS} />
                  <YAxis {...AXIS} allowDecimals={false} width={32} />
                  <Tooltip {...TIP} formatter={(v: number) => [v, "Cleanings"]} />
                  <Bar dataKey="count" name="Cleanings" fill={SERIES[0]} {...BAR} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Table turnover: visits per table" empty={!k.sessions} note="Hover a bar for average visit length and turnover per table.">
              <ResponsiveContainer>
                <BarChart data={data.charts.turnover_by_table}>
                  {GRID}
                  <XAxis dataKey="table_id" {...AXIS} />
                  <YAxis {...AXIS} allowDecimals={false} width={32} />
                  <Tooltip
                    {...TIP}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const d = payload[0].payload as Analytics["charts"]["turnover_by_table"][number];
                      return (
                        <div style={TIP.contentStyle} className="px-3 py-2">
                          <p className="font-semibold">{d.table_id}</p>
                          <p>{d.sessions} visit(s)</p>
                          <p className="text-txt-muted">avg visit {d.avg_occupied_min ?? "—"} min · avg turnover {d.avg_turnover_min ?? "—"} min</p>
                          <p className="text-txt-muted">{d.unserved_alerts} waiting alert(s)</p>
                        </div>
                      );
                    }}
                  />
                  <Bar dataKey="sessions" name="Visits" fill={SERIES[0]} {...BAR} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Alerts over time" empty={!data.charts.alerts_over_time.length}>
              <ResponsiveContainer>
                <BarChart data={data.charts.alerts_over_time}>
                  {GRID}
                  <XAxis dataKey="bucket" {...AXIS} />
                  <YAxis {...AXIS} allowDecimals={false} width={32} />
                  <Tooltip {...TIP} />
                  <Legend wrapperStyle={{ fontSize: 11, color: "#93A3BE" }} />
                  {ALERT_ORDER.filter((t) => data.charts.alert_types.includes(t)).map((t) => (
                    <Bar key={t} dataKey={t} name={ALERT_LABEL[t]} stackId="a" fill={SERIES[ALERT_ORDER.indexOf(t)]} stroke="#121C31" strokeWidth={2} maxBarSize={36} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <Card title="Staffing estimator" icon={<Users className="h-4 w-4 text-txt-muted" />}>
            <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
              <div className="space-y-3">
                <label className="label" htmlFor="ratio">Tables one server can cover</label>
                <input id="ratio" type="range" min={1} max={10} step={0.5} value={ratio} onChange={(e) => setRatio(Number(e.target.value))} className="w-full accent-teal-400" />
                <p className="text-sm">
                  <span className="font-mono text-lg">{ratio}</span> tables per server
                </p>
                <div className="rounded-lg border border-ink-600 bg-ink-850 p-3">
                  <p className="text-xs text-txt-muted">At the peak of {data.staffing.peak_occupied_tables} occupied tables</p>
                  <p className="text-2xl font-semibold">{data.staffing.estimated_servers_at_peak} server(s)</p>
                </div>
                <p className="text-xs text-amber-200/90">{data.staffing.disclaimer}</p>
              </div>
              <div className="h-56">
                {data.staffing.by_hour.length ? (
                  <ResponsiveContainer>
                    <BarChart data={data.staffing.by_hour}>
                      {GRID}
                      <XAxis dataKey="label" {...AXIS} />
                      <YAxis {...AXIS} allowDecimals={false} width={32} />
                      <Tooltip {...TIP} formatter={(v: number, n: string) => [v, n]} />
                      <Bar dataKey="estimated_servers" name="Estimated servers (peak in hour)" fill={SERIES[0]} {...BAR} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <Empty title="No hourly data yet" />
                )}
              </div>
            </div>
          </Card>

          <Card title="Export" icon={<Download className="h-4 w-4 text-txt-muted" />}>
            <div className="flex flex-wrap gap-2">
              {(["events", "sessions", "alerts"] as const).map((kind) => (
                <a key={kind} className="btn-secondary" href={api.exportUrl(kind, dataset)} download>
                  <Download className="h-4 w-4" /> {kind}.csv
                </a>
              ))}
            </div>
            <p className="mt-2 text-xs text-txt-faint">Exports contain operational events only: no images, no identities. Simulated exports are labelled in the first line.</p>
          </Card>
        </>
      )}
      {!data && !error && (
        <p className="flex items-center gap-2 text-sm text-txt-muted">
          <BarChart3 className="h-4 w-4" /> Loading analytics…
        </p>
      )}
    </div>
  );
}
