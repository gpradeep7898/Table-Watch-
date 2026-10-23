import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../services/api";
import type { FramesMsg, Layout, Snapshot, TWEvent } from "../types/api";
import { useToasts } from "./useToasts";

interface LiveValue {
  snapshot: Snapshot | null;
  frames: FramesMsg | null;
  events: TWEvent[];
  layout: Layout | null;
  connected: boolean;
  problem: string | null;
  refresh: () => Promise<void>;
  reloadLayout: () => Promise<void>;
}

const LiveContext = createContext<LiveValue | null>(null);
const MAX_EVENTS = 300;

export function LiveProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [frames, setFrames] = useState<FramesMsg | null>(null);
  const [events, setEvents] = useState<TWEvent[]>([]);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [connected, setConnected] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const { alertToast } = useToasts();
  const alertToastRef = useRef(alertToast);
  alertToastRef.current = alertToast;
  const runRef = useRef<string | null>(null);

  const loadEvents = useCallback(async () => {
    try {
      const res = await api.events({ scope: "current", limit: 150 });
      setEvents(res.events);
    } catch {
      /* the banner shows connection problems */
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await api.state();
      if (!s || (s as any).type !== "snapshot" || !Array.isArray(s.tables)) {
        setProblem("Something answered at /api, but it is not the TableWatch server. Another program may be using its port. Restart with ./scripts/start-demo.sh.");
        return;
      }
      setSnapshot(s);
      setProblem(null);
    } catch (e) {
      const err = e as ApiError;
      setProblem(
        err.status === 0 || err.status >= 500
          ? "Cannot reach the TableWatch server. Is ./scripts/start-demo.sh still running in Terminal?"
          : err.status === 404
            ? "The server at /api is not TableWatch (404). Another program may be using port 8000. Restart with ./scripts/start-demo.sh."
            : err.message,
      );
    }
  }, []);

  const reloadLayout = useCallback(async () => {
    try {
      setLayout(await api.layout());
    } catch {
      /* retried on reconnect */
    }
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => {
        setConnected(true);
        setProblem(null);
        loadEvents();
        reloadLayout();
      };
      ws.onclose = () => {
        if (!stopped) refresh();
        setConnected(false);
        if (!stopped) retry = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = (msg) => {
        let data: any;
        try {
          data = JSON.parse(msg.data);
        } catch {
          return;
        }
        if (data.type === "snapshot") {
          const s = data as Snapshot;
          if (runRef.current && s.run?.id !== runRef.current) {
            setEvents([]);
            loadEvents();
          }
          runRef.current = s.run?.id ?? null;
          setSnapshot(s);
        } else if (data.type === "frames") {
          setFrames(data as FramesMsg);
        } else if (data.type === "event") {
          const ev = data.event as TWEvent;
          setEvents((prev) => [ev, ...prev].slice(0, MAX_EVENTS));
          if (ev.type === "ALERT_RAISED") alertToastRef.current(ev);
        } else if (data.type === "reset") {
          setEvents([]);
          loadEvents();
          reloadLayout();
        }
      };
    };
    connect();
    // Load immediately over HTTP, and keep polling while the live socket is down.
    refresh();
    reloadLayout();
    loadEvents();
    const poll = setInterval(() => {
      if (ws?.readyState !== WebSocket.OPEN) {
        refresh();
        reloadLayout();
      }
    }, 2000);
    return () => {
      clearInterval(poll);
      stopped = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [loadEvents, reloadLayout, refresh]);

  const value = useMemo(
    () => ({ snapshot, frames, events, layout, connected, problem, refresh, reloadLayout }),
    [snapshot, frames, events, layout, connected, problem, refresh, reloadLayout],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside LiveProvider");
  return ctx;
}
