import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../services/api";
import type { FramesMsg, Layout, Snapshot, TWEvent } from "../types/api";
import { useToasts } from "./useToasts";

interface LiveValue {
  snapshot: Snapshot | null;
  frames: FramesMsg | null;
  events: TWEvent[];
  layout: Layout | null;
  connected: boolean;
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
      setSnapshot(s);
    } catch {
      /* handled by connection state */
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
        loadEvents();
        reloadLayout();
      };
      ws.onclose = () => {
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
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [loadEvents, reloadLayout]);

  const value = useMemo(
    () => ({ snapshot, frames, events, layout, connected, refresh, reloadLayout }),
    [snapshot, frames, events, layout, connected, refresh, reloadLayout],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside LiveProvider");
  return ctx;
}
