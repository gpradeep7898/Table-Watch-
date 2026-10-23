import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Check, Circle, Clapperboard, HelpCircle, Pause, Play, RotateCcw } from "lucide-react";
import { AlertRow } from "../components/AlertRow";
import { EventList } from "../components/EventTimeline";
import { FloorPlan, Legend } from "../components/FloorPlan";
import { useScenarios } from "../components/SimControls";
import { Logo } from "../components/Shell";
import { TableDetail } from "../components/TableDetail";
import { Modal, Stat } from "../components/ui";
import { useLive } from "../hooks/useLive";
import { useToasts } from "../hooks/useToasts";
import { ApiError, api } from "../services/api";
import { clockTime } from "../lib/format";

const NARRATION: Record<string, string> = {
  enter: "Guests walk into the fictional TableWatch Demo Bistro. Dots are simulated people moving across a synthetic overhead camera.",
  seated: "When people stay in a table’s seating area long enough, the table becomes occupied and a waiting timer starts. No manual check-in.",
  waiting: "Table 6 has not had a confirmed visit from a server. The timer keeps counting. Watch it turn red.",
  alert: "Past the threshold, one alert fires. Only one: it will not repeat every second.",
  served: "Click Table 6 (or the alert) and press Confirm service. Staff confirm; the camera alone never marks a table served.",
  leave: "Another party finishes and walks out.",
  cleaning: "After the table stays empty for a confirmed period, it turns amber: needs cleaning. A camera hiccup can’t free it by accident.",
  cleaned: "After bussing, press Confirm cleaning on Table 1. Only then does it become available again.",
  analytics: "Every step was recorded. Open Analytics to see waits, turnover and cleaning times computed from these events.",
};

export function DemoPage() {
  const { snapshot, layout, frames, events } = useLive();
  const { push } = useToasts();
  const scenarios = useScenarios();
  const [selected, setSelected] = useState<string | null>(null);
  const [how, setHow] = useState(false);
  const sim = snapshot?.sim;
  const call = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      push("error", "Could not do that", (e as ApiError).message);
    }
  };
  const start = () => call(() => api.simStart("guided", { autoplay: true, speed: 5 }));
  const guide = snapshot?.guide ?? [];
  const current = guide.find((g) => !g.done);
  const alerts = snapshot?.alerts.filter((a) => a.status !== "RESOLVED" && a.type !== "TABLE_NEEDS_CLEANING") ?? [];
  const cleaning = snapshot?.alerts.filter((a) => a.status !== "RESOLVED" && a.type === "TABLE_NEEDS_CLEANING") ?? [];

  return (
    <div className="min-h-full bg-ink-900">
      <header className="sticky top-0 z-20 border-b border-ink-700 bg-ink-900/95 backdrop-blur">
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
          <Link to="/" className="btn-ghost p-1.5" aria-label="Back to dashboard">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <Logo />
          <span className="hidden text-sm text-txt-muted md:inline">· {snapshot?.restaurant.name}</span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button className="btn-primary" onClick={start} data-testid="start-presentation">
              <Clapperboard className="h-4 w-4" /> Start presentation
            </button>
            {sim && snapshot?.mode === "simulation" && (
              <>
                <select className="input w-auto py-1.5" aria-label="Scenario" value={sim.scenario.id} onChange={(e) => call(() => api.simStart(e.target.value, { autoplay: true, speed: sim.speed }))}>
                  {scenarios.map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
                <button className="btn-secondary px-2.5" onClick={() => call(() => api.simControl(sim.paused ? "play" : "pause"))} disabled={sim.finished} aria-label={sim.paused ? "Play" : "Pause"}>
                  {sim.paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
                </button>
                {[1, 5, 10].map((s) => (
                  <button key={s} className={`btn px-2.5 py-1.5 ${sim.speed === s && !sim.paused ? "bg-brand/20 text-brand ring-1 ring-brand/50" : "btn-ghost"}`} onClick={() => call(() => api.simControl("speed", s))}>
                    {s}x
                  </button>
                ))}
                <button className="btn-ghost px-2.5" onClick={() => call(() => api.simStart(sim.scenario.id, { autoplay: true, speed: sim.speed }))}>
                  <RotateCcw className="h-4 w-4" /> Restart
                </button>
              </>
            )}
            <button className="btn-ghost" onClick={() => setHow(true)}>
              <HelpCircle className="h-4 w-4" /> How it works
            </button>
          </div>
        </div>
        <div className="border-t border-fuchsia-400/30 bg-fuchsia-500/10 px-4 py-1.5 text-center text-xs font-medium text-fuchsia-100 sm:px-6" role="note">
          SIMULATION · A fictional restaurant with simulated guests. This is not camera footage and not results from any real restaurant.
          <span className="ml-2 font-mono">{clockTime(snapshot?.clock.iso)}</span>
        </div>
      </header>

      {!snapshot || !layout ? (
        <p className="p-6 text-sm text-txt-muted">Connecting…</p>
      ) : (
        <main className="grid gap-5 px-4 py-5 sm:px-6 xl:grid-cols-[minmax(0,1fr)_25rem]">
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Available" value={snapshot.summary.free} tone="text-st-free" />
              <Stat label="Awaiting service" value={snapshot.summary.awaiting_service} tone="text-st-seated" />
              <Stat label="Need cleaning" value={snapshot.summary.needs_cleaning} tone="text-st-cleaning" />
              <Stat label="Active alerts" value={snapshot.summary.active_alerts} tone={snapshot.summary.active_alerts ? "text-st-overdue" : "text-txt"} />
            </div>
            <div className="card p-3 sm:p-4">
              <FloorPlan layout={layout} tables={snapshot.tables} selected={selected} onSelect={setSelected} people={frames?.frames.find((f) => f.simulated)?.people} />
              <div className="mt-3">
                <Legend />
              </div>
            </div>
            <div className="grid gap-5 lg:grid-cols-2">
              <section className="card p-4">
                <h2 className="mb-2 text-sm font-semibold">Before TableWatch</h2>
                <ul className="list-disc space-y-1 pl-5 text-sm text-txt-muted">
                  <li>Managers walk the floor to spot who has waited too long.</li>
                  <li>A forgotten table is noticed when the guest complains.</li>
                  <li>Dirty tables sit while guests wait at the host stand.</li>
                  <li>Nobody knows the real wait or turnover times.</li>
                </ul>
              </section>
              <section className="card p-4">
                <h2 className="mb-2 text-sm font-semibold">With TableWatch (what this demo shows)</h2>
                <ul className="list-disc space-y-1 pl-5 text-sm text-txt-muted">
                  <li>Every table’s status and timer on one screen.</li>
                  <li>One alert when a table waits past your threshold.</li>
                  <li>Staff confirm service and cleaning in one tap.</li>
                  <li>Wait, turnover and cleaning times from real events.</li>
                </ul>
              </section>
            </div>
          </div>

          <aside className="space-y-5">
            {selected && <TableDetail tableId={selected} onClose={() => setSelected(null)} />}
            <section className="card p-4" aria-label="Guided demo steps">
              <h2 className="mb-3 text-sm font-semibold">
                Guided story {sim?.scenario.id !== "guided" && <span className="text-xs font-normal text-txt-faint">(press Start presentation)</span>}
              </h2>
              <ol className="space-y-2">
                {guide.map((g, i) => {
                  const isCurrent = current?.id === g.id;
                  return (
                    <li key={g.id} className={`rounded-lg border px-3 py-2 ${isCurrent ? "border-brand/60 bg-brand/5" : "border-transparent"}`}>
                      <p className={`flex items-center gap-2 text-sm ${g.done ? "text-txt" : isCurrent ? "font-semibold text-brand" : "text-txt-faint"}`}>
                        {g.done ? <Check className="h-4 w-4 text-st-free" /> : <Circle className="h-4 w-4" />}
                        {i + 1}. {g.title}
                      </p>
                      {isCurrent && <p className="mt-1 pl-6 text-xs text-txt-muted">{NARRATION[g.id]}</p>}
                    </li>
                  );
                })}
              </ol>
              {current?.id === "analytics" || (!current && guide.length > 0) ? (
                <Link to="/analytics" className="btn-primary mt-3 w-full">Open analytics</Link>
              ) : null}
            </section>
            {(alerts.length > 0 || cleaning.length > 0) && (
              <section className="space-y-2" aria-label="Alerts">
                {[...alerts, ...cleaning].map((a) => (
                  <AlertRow key={a.id} alert={a} onOpenTable={setSelected} compact />
                ))}
              </section>
            )}
            <section className="card p-3">
              <h2 className="mb-1 px-1 text-sm font-semibold">What just happened</h2>
              <div className="max-h-80 overflow-auto">
                <EventList events={events.filter((e) => !["ALERT_ACKNOWLEDGED", "LOW_CONFIDENCE_ENDED"].includes(e.type))} limit={25} compact />
              </div>
            </section>
          </aside>
        </main>
      )}
      <HowItWorks open={how} onClose={() => setHow(false)} />
    </div>
  );
}

function HowItWorks({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} title="How TableWatch works" onClose={onClose} wide>
      <div className="grid gap-5 text-sm text-txt-muted md:grid-cols-2">
        <div className="space-y-3">
          <p><strong className="text-txt">Uses cameras you may already have.</strong> Many existing security cameras can be used, but every camera needs a placement and compatibility check first. Some angles, lighting or obstructions will not work.</p>
          <p><strong className="text-txt">Sees people, not identities.</strong> A person detector draws boxes around people. There is no face recognition and no attempt to identify anyone.</p>
          <p><strong className="text-txt">Runs on a local computer.</strong> Video can be processed inside the restaurant. Nothing needs to go to the cloud, and raw video is not stored.</p>
        </div>
        <div className="space-y-3">
          <p><strong className="text-txt">Staff stay in charge.</strong> The system suggests; staff confirm service and cleaning. Uncertain observations are flagged for a quick human check instead of guessed.</p>
          <p><strong className="text-txt">Accuracy has to be earned.</strong> This demo is a simulation. Real accuracy has to be measured in your restaurant, with your cameras, during a supervised pilot.</p>
          <p><strong className="text-txt">What we will not promise.</strong> No guaranteed revenue, no “99% accuracy”, no “works with every camera”. We would rather measure it with you.</p>
        </div>
      </div>
    </Modal>
  );
}
