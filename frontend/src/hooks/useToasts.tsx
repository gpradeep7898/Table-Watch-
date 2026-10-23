import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { ALERT_LABEL, getLocal, setLocal } from "../lib/format";
import type { AlertType, TWEvent } from "../types/api";

type Kind = "info" | "success" | "warning" | "error";
interface Toast {
  id: number;
  kind: Kind;
  title: string;
  body?: string;
}

interface ToastValue {
  push: (kind: Kind, title: string, body?: string) => void;
  alertToast: (ev: TWEvent) => void;
  soundEnabled: boolean;
  setSoundEnabled: (v: boolean) => void;
}

const ToastContext = createContext<ToastValue | null>(null);

function beep(severity: string) {
  try {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = severity === "critical" ? 880 : 660;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.4);
    osc.onended = () => ctx.close();
  } catch {
    /* audio unavailable */
  }
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [soundEnabled, setSound] = useState<boolean>(() => getLocal("tw.sound", false));
  const soundRef = useRef(soundEnabled);
  soundRef.current = soundEnabled;
  const next = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const push = useCallback(
    (kind: Kind, title: string, body?: string) => {
      const id = next.current++;
      setToasts((t) => [...t.slice(-3), { id, kind, title, body }]);
      setTimeout(() => dismiss(id), kind === "error" ? 9000 : 6000);
    },
    [dismiss],
  );

  const alertToast = useCallback(
    (ev: TWEvent) => {
      const a = ev.payload?.alert;
      if (!a) return;
      const kind: Kind = a.severity === "info" ? "info" : a.severity === "critical" ? "error" : "warning";
      push(kind, ALERT_LABEL[a.type as AlertType] ?? a.type, a.message);
      if (soundRef.current && a.severity !== "info") beep(a.severity);
    },
    [push],
  );

  const setSoundEnabled = useCallback((v: boolean) => {
    setSound(v);
    setLocal("tw.sound", v);
    if (v) beep("warning");
  }, []);

  const value = useMemo(() => ({ push, alertToast, soundEnabled, setSoundEnabled }), [push, alertToast, soundEnabled, setSoundEnabled]);
  const icon = { info: Info, success: CheckCircle2, warning: AlertTriangle, error: XCircle };
  const tone = {
    info: "border-sky-400/40 text-sky-100",
    success: "border-emerald-400/40 text-emerald-100",
    warning: "border-amber-400/50 text-amber-100",
    error: "border-red-400/50 text-red-100",
  };
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[22rem] max-w-[calc(100vw-2rem)] flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((t) => {
          const Icon = icon[t.kind];
          return (
            <div key={t.id} className={`animate-in pointer-events-auto flex gap-3 rounded-xl border bg-ink-800/95 p-3 shadow-card backdrop-blur ${tone[t.kind]}`}>
              <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">{t.title}</p>
                {t.body && <p className="mt-0.5 text-xs text-txt-muted">{t.body}</p>}
              </div>
              <button className="text-txt-faint hover:text-txt" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToasts(): ToastValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToasts must be used inside ToastProvider");
  return ctx;
}
