import { useEffect, useState } from "react";
import { FastForward, Pause, Play, RotateCcw } from "lucide-react";
import { ApiError, api } from "../services/api";
import { useLive } from "../hooks/useLive";
import { useToasts } from "../hooks/useToasts";
import { duration } from "../lib/format";
import type { Scenario } from "../types/api";
import { SimBadge } from "./ui";

export function useScenarios() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  useEffect(() => {
    api.scenarios().then((r) => setScenarios(r.scenarios)).catch(() => setScenarios([]));
  }, []);
  return scenarios;
}

export function SimControls({ compact = false, showSkip = true }: { compact?: boolean; showSkip?: boolean }) {
  const { snapshot } = useLive();
  const { push } = useToasts();
  const scenarios = useScenarios();
  const [busy, setBusy] = useState(false);
  const sim = snapshot?.sim;
  if (!snapshot || snapshot.mode !== "simulation" || !sim) return null;

  const call = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      push("error", "Simulation control failed", (e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  const playing = !sim.paused;
  return (
    <div className={`card flex flex-wrap items-center gap-3 ${compact ? "px-3 py-2" : "px-4 py-3"}`} data-testid="sim-controls">
      <SimBadge compact={compact} />
      <label className="sr-only" htmlFor="scenario-select">
        Scenario
      </label>
      <select
        id="scenario-select"
        className="input w-auto min-w-[12rem] py-1.5"
        value={sim.scenario.id}
        disabled={busy}
        onChange={(e) => call(() => api.simStart(e.target.value, { autoplay: true, speed: sim.speed }))}
      >
        {scenarios.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
      <div className="flex items-center gap-1" role="group" aria-label="Playback">
        <button
          className="btn-secondary px-2.5 py-1.5"
          disabled={busy || sim.finished}
          onClick={() => call(() => api.simControl(playing ? "pause" : "play"))}
          aria-label={playing ? "Pause simulation" : "Play simulation"}
          data-testid="sim-playpause"
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          {!compact && (playing ? "Pause" : "Play")}
        </button>
        {[1, 5, 10].map((s) => (
          <button
            key={s}
            className={`btn px-2.5 py-1.5 ${sim.speed === s && playing ? "bg-brand/20 text-brand ring-1 ring-brand/50" : "btn-ghost"}`}
            disabled={busy || sim.finished}
            aria-pressed={sim.speed === s && playing}
            onClick={() => call(() => api.simControl("speed", s))}
            data-testid={`speed-${s}`}
          >
            {s}x
          </button>
        ))}
      </div>
      <button className="btn-ghost px-2.5 py-1.5" disabled={busy} onClick={() => call(() => api.simStart(sim.scenario.id, { autoplay: true, speed: sim.speed }))} data-testid="sim-restart">
        <RotateCcw className="h-4 w-4" /> {!compact && "Restart"}
      </button>
      {showSkip && (
        <button className="btn-ghost px-2.5 py-1.5" disabled={busy || sim.finished} onClick={() => call(() => api.simAdvance(60))} title="Run the next 60 simulated seconds immediately">
          <FastForward className="h-4 w-4" /> {!compact && "+1 min"}
        </button>
      )}
      <div className="ml-auto flex min-w-[10rem] flex-1 items-center gap-2 sm:max-w-xs">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-700" role="progressbar" aria-valuenow={Math.round(sim.progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Scenario progress">
          <div className="h-full rounded-full bg-brand transition-all" style={{ width: `${sim.progress * 100}%` }} />
        </div>
        <span className="font-mono text-xs text-txt-muted">
          {duration(sim.t)} / {duration(sim.duration_s)}
        </span>
      </div>
      {sim.finished && <span className="chip border-brand/40 bg-brand/10 text-brand">Scenario complete</span>}
    </div>
  );
}
