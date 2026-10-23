import { useEffect, useState } from "react";
import { Bell, Lock, RotateCcw, Save, ShieldCheck, SlidersHorizontal, Trash2, User } from "lucide-react";
import { Card, ErrorText, Modal, Toggle } from "../components/ui";
import { useToasts } from "../hooks/useToasts";
import { ApiError, api } from "../services/api";
import { getLocal, setLocal } from "../lib/format";
import type { Mode, SettingsResponse } from "../types/api";

const GROUPS: { title: string; keys: string[] }[] = [
  { title: "Seating and departure", keys: ["occupancy_confirm_s", "departure_confirm_s", "lost_track_grace_s"] },
  { title: "Alerts", keys: ["unserved_warning_s", "cleaning_overdue_s"] },
  { title: "Staff visits", keys: ["min_visit_s", "max_visit_s"] },
  { title: "Detection and observation quality", keys: ["detection_confidence", "camera_offline_after_s", "low_confidence_alert_s", "low_confidence_clear_s", "max_observation_gap_s", "reassociation_window_s", "reassociation_distance", "ambiguity_margin"] },
];

const LABELS: Record<string, string> = {
  occupancy_confirm_s: "Occupancy confirmation",
  departure_confirm_s: "Departure confirmation",
  lost_track_grace_s: "Lost-track grace period",
  unserved_warning_s: "Unserved warning after",
  cleaning_overdue_s: "Cleaning overdue after",
  min_visit_s: "Minimum candidate visit",
  max_visit_s: "Visitor joins party after",
  detection_confidence: "Detection confidence threshold",
  camera_offline_after_s: "Camera offline after",
  low_confidence_alert_s: "Uncertainty alert after",
  low_confidence_clear_s: "Uncertainty clears after",
  max_observation_gap_s: "Max gap counted as observed",
  reassociation_window_s: "Track re-link window",
  reassociation_distance: "Track re-link distance",
  ambiguity_margin: "Zone ambiguity margin",
};

export function SettingsPage() {
  const { push, soundEnabled, setSoundEnabled } = useToasts();
  const [s, setS] = useState<SettingsResponse | null>(null);
  const [th, setTh] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [sim, setSim] = useState<SettingsResponse["simulation"] | null>(null);
  const [privacy, setPrivacy] = useState<SettingsResponse["privacy"] | null>(null);
  const [counts, setCounts] = useState<Record<string, { events: number; runs: number; sessions: number }>>({});
  const [actor, setActor] = useState<string>(() => getLocal("tw.actor", "Manager"));
  const [deleting, setDeleting] = useState<Mode | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await api.settings();
      setS(r);
      setTh(Object.fromEntries(Object.entries(r.thresholds).map(([k, v]) => [k, String(v)])));
      setSim(r.simulation);
      setPrivacy(r.privacy);
      setCounts((await api.dataSummary()).counts);
    } catch (e) {
      setMsg((e as ApiError).message);
    }
  };
  useEffect(() => {
    load();
  }, []);

  if (!s || !sim || !privacy) return <ErrorText error={msg} />;
  const changed = Object.entries(th).filter(([k, v]) => Number(v) !== s.thresholds[k]);

  const saveTh = async () => {
    setErrors([]);
    try {
      const body = Object.fromEntries(changed.map(([k, v]) => [k, v.trim() === "" ? NaN : Number(v)]));
      for (const [k, v] of Object.entries(body)) if (Number.isNaN(v)) throw new ApiError(400, "invalid", `${LABELS[k]} must be a number`, [`${LABELS[k]} must be a number`]);
      const r = await api.saveThresholds(body);
      push("success", `Thresholds saved (version ${r.version})`, "They apply to the running session immediately.");
      load();
    } catch (e) {
      const err = e as ApiError;
      setErrors(err.errors.length ? err.errors : [err.message]);
    }
  };

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Settings</h1>

      <Card title="Thresholds" icon={<SlidersHorizontal className="h-4 w-4 text-txt-muted" />} actions={<span className="text-xs text-txt-faint">version {s.thresholds_version}</span>}>
        <div className="grid gap-6 lg:grid-cols-2">
          {GROUPS.map((g) => (
            <fieldset key={g.title}>
              <legend className="mb-2 text-sm font-semibold">{g.title}</legend>
              <div className="space-y-3">
                {g.keys.map((k) => {
                  const lim = s.threshold_limits[k];
                  const isSec = k.endsWith("_s");
                  const v = Number(th[k]);
                  return (
                    <div key={k}>
                      <label className="flex items-baseline justify-between text-sm" htmlFor={`th-${k}`}>
                        <span>{LABELS[k] ?? k}</span>
                        <span className="text-xs text-txt-faint">
                          {isSec && !Number.isNaN(v) && v >= 60 ? `${(v / 60).toFixed(1)} min · ` : ""}
                          {lim.min}–{lim.max}
                          {isSec ? " s" : ""}
                        </span>
                      </label>
                      <input
                        id={`th-${k}`}
                        className={`input mt-1 font-mono ${Number(th[k]) !== s.thresholds[k] ? "border-brand/60" : ""}`}
                        type="number"
                        step="any"
                        min={lim.min}
                        max={lim.max}
                        value={th[k] ?? ""}
                        onChange={(e) => setTh({ ...th, [k]: e.target.value })}
                      />
                      <p className="mt-0.5 text-[11px] text-txt-faint">{lim.description}</p>
                    </div>
                  );
                })}
              </div>
            </fieldset>
          ))}
        </div>
        {errors.length > 0 && <ErrorText error={errors.join(" · ")} />}
        <div className="mt-5 flex flex-wrap gap-2">
          <button className="btn-primary" disabled={!changed.length} onClick={saveTh} data-testid="save-thresholds">
            <Save className="h-4 w-4" /> Save {changed.length ? `${changed.length} change(s)` : ""}
          </button>
          <button
            className="btn-ghost"
            onClick={async () => {
              await api.resetThresholds();
              push("success", "Thresholds reset to defaults");
              load();
            }}
          >
            <RotateCcw className="h-4 w-4" /> Reset to defaults
          </button>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Simulation">
          <div className="space-y-3">
            <div>
              <label className="label" htmlFor="sim-scn">Default scenario</label>
              <select id="sim-scn" className="input" value={sim.default_scenario} onChange={(e) => setSim({ ...sim, default_scenario: e.target.value })}>
                {s.scenarios.map((x) => (
                  <option key={x.id} value={x.id}>{x.name}</option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label" htmlFor="sim-seed">Seed (blank = scenario seed)</label>
                <input id="sim-seed" className="input font-mono" type="number" min={0} value={sim.seed ?? ""} onChange={(e) => setSim({ ...sim, seed: e.target.value === "" ? null : Number(e.target.value) })} />
              </div>
              <div>
                <label className="label" htmlFor="sim-speed">Start speed</label>
                <select id="sim-speed" className="input" value={sim.speed} onChange={(e) => setSim({ ...sim, speed: Number(e.target.value) })}>
                  {[1, 5, 10].map((x) => <option key={x} value={x}>{x}x</option>)}
                </select>
              </div>
            </div>
            <Toggle id="sim-auto" label="Simulated staff confirm service and cleaning in the app" checked={sim.staff_auto_confirm} onChange={(v) => setSim({ ...sim, staff_auto_confirm: v })} />
            <Toggle id="sim-play" label="Start playing automatically" checked={sim.autoplay} onChange={(v) => setSim({ ...sim, autoplay: v })} />
            <p className="text-xs text-txt-faint">Applies the next time a scenario starts or restarts.</p>
            <button
              className="btn-primary"
              onClick={async () => {
                try {
                  await api.saveSim(sim);
                  push("success", "Simulation settings saved");
                } catch (e) {
                  push("error", "Not saved", (e as ApiError).message);
                }
              }}
            >
              <Save className="h-4 w-4" /> Save simulation settings
            </button>
          </div>
        </Card>

        <Card title="You and notifications" icon={<User className="h-4 w-4 text-txt-muted" />}>
          <label className="label" htmlFor="actor">Name recorded on your actions</label>
          <input id="actor" className="input" maxLength={40} value={actor} onChange={(e) => setActor(e.target.value)} onBlur={() => { setLocal("tw.actor", actor.trim() || "Manager"); push("success", "Name saved on this browser"); }} />
          <div className="mt-4 flex items-center gap-2">
            <Bell className="h-4 w-4 text-txt-muted" />
            <Toggle id="snd" label="Play a sound for new warnings (off by default)" checked={soundEnabled} onChange={setSoundEnabled} />
          </div>
          <p className="mt-3 flex items-start gap-2 text-xs text-txt-faint">
            <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" /> The server listens on <code className="kbd">{s.bind_host}</code>. There is no login, so keep it on this computer. Exposing it to a network needs authentication and HTTPS first.
          </p>
        </Card>
      </div>

      <Card title="Privacy and data retention" icon={<ShieldCheck className="h-4 w-4 text-txt-muted" />}>
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="space-y-3 text-sm">
            <ul className="list-disc space-y-1 pl-5 text-txt-muted">
              <li>No face recognition, no face embeddings, no identification of guests.</li>
              <li>No cross-camera re-identification. Track IDs are temporary numbers.</li>
              <li>No audio. No cloud uploads. No telemetry.</li>
              <li>Raw video is never stored. Only operational events go to the local database.</li>
            </ul>
            <div>
              <label className="label" htmlFor="ret">Delete live and recorded events older than (days)</label>
              <input id="ret" type="number" min={1} max={3650} className="input" value={privacy.event_retention_days} onChange={(e) => setPrivacy({ ...privacy, event_retention_days: Number(e.target.value) })} />
            </div>
            <Toggle id="dbg" label="Allow annotated debug video from the CLI (opt-in, local only)" checked={privacy.store_debug_video} onChange={(v) => setPrivacy({ ...privacy, store_debug_video: v })} />
            <button
              className="btn-primary"
              onClick={async () => {
                try {
                  await api.savePrivacy(privacy);
                  push("success", "Privacy settings saved", "Retention runs each time the server starts.");
                } catch (e) {
                  push("error", "Not saved", (e as ApiError).message);
                }
              }}
            >
              <Save className="h-4 w-4" /> Save privacy settings
            </button>
          </div>
          <div>
            <p className="label">Stored data (local database)</p>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-txt-muted"><th className="py-1">Dataset</th><th>Runs</th><th>Visits</th><th>Events</th><th /></tr>
              </thead>
              <tbody>
                {(["simulation", "recorded", "live"] as Mode[]).map((d) => (
                  <tr key={d} className="border-t border-ink-700">
                    <td className="py-2 capitalize">{d}</td>
                    <td className="font-mono">{counts[d]?.runs ?? 0}</td>
                    <td className="font-mono">{counts[d]?.sessions ?? 0}</td>
                    <td className="font-mono">{counts[d]?.events ?? 0}</td>
                    <td className="text-right">
                      <button className="btn-danger px-2 py-1 text-xs" onClick={() => setDeleting(d)}>
                        <Trash2 className="h-3.5 w-3.5" /> Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Card>
      <DeleteDialog dataset={deleting} onClose={() => setDeleting(null)} onDone={load} />
    </div>
  );
}

function DeleteDialog({ dataset, onClose, onDone }: { dataset: Mode | null; onClose: () => void; onDone: () => void }) {
  const { push } = useToasts();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText("");
    setError(null);
  }, [dataset]);
  if (!dataset) return null;
  const phrase = `DELETE ${dataset}`;
  return (
    <Modal open title={`Delete ${dataset} data`} onClose={onClose}>
      <p className="text-sm text-txt-muted">
        This permanently removes all {dataset} events, table visits, alerts and action logs from the local database. Other datasets are untouched. Type <code className="kbd">{phrase}</code> to confirm.
      </p>
      <input className="input mt-3 font-mono" value={text} onChange={(e) => setText(e.target.value)} aria-label="Confirmation phrase" />
      <ErrorText error={error} />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button
          className="btn-danger"
          disabled={text !== phrase}
          onClick={async () => {
            try {
              const r = await api.deleteData(dataset, text);
              push("success", `Deleted ${r.deleted_events} ${dataset} events`);
              onClose();
              onDone();
            } catch (e) {
              setError((e as ApiError).message);
            }
          }}
        >
          <Trash2 className="h-4 w-4" /> Delete permanently
        </button>
      </div>
    </Modal>
  );
}
