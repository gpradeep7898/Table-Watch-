import { useEffect, useState } from "react";
import { NavLink, Outlet, Link } from "react-router-dom";
import {
  BarChart3,
  Bell,
  Camera,
  Clock,
  History,
  LayoutDashboard,
  Map,
  Menu,
  Presentation,
  Settings,
  Video,
  WifiOff,
  X,
} from "lucide-react";
import { useLive } from "../hooks/useLive";
import { clockTime } from "../lib/format";
import { SimBadge } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/floor", label: "Live Floor", icon: Map },
  { to: "/cameras", label: "Cameras", icon: Camera },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/history", label: "Table History", icon: History },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Logo({ small = false }: { small?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className={small ? "h-7 w-7" : "h-8 w-8"} aria-hidden>
        <rect width="32" height="32" rx="8" fill="#0F172A" stroke="#1D2944" />
        <rect x="7" y="9" width="18" height="14" rx="3" fill="none" stroke="#2DD4BF" strokeWidth="2.5" />
        <circle cx="16" cy="16" r="3" fill="#2DD4BF" />
      </svg>
      <div className="leading-tight">
        <p className="text-sm font-semibold tracking-tight text-txt">TableWatch AI</p>
        {!small && <p className="text-[11px] text-txt-faint">Know every table.</p>}
      </div>
    </div>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { snapshot } = useLive();
  const active = snapshot?.summary.active_alerts ?? 0;
  return (
    <nav className="flex h-full flex-col gap-1 p-3" aria-label="Main">
      <div className="px-2 pb-4 pt-2">
        <Logo />
      </div>
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            `group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 ${
              isActive ? "bg-ink-700 text-txt" : "text-txt-muted hover:bg-ink-750 hover:text-txt"
            }`
          }
        >
          <n.icon className="h-4 w-4" aria-hidden />
          <span className="flex-1">{n.label}</span>
          {n.to === "/alerts" && active > 0 && (
            <span className="rounded-full bg-red-500/90 px-1.5 text-[11px] font-semibold text-white" aria-label={`${active} active alerts`}>
              {active}
            </span>
          )}
        </NavLink>
      ))}
      <div className="mt-auto space-y-2 px-1 pt-4">
        <Link to="/demo" className="btn-secondary w-full justify-start" onClick={onNavigate}>
          <Presentation className="h-4 w-4" /> Presentation mode
        </Link>
        <p className="px-2 text-[11px] leading-snug text-txt-faint">Runs locally on this computer. No cloud, no face recognition.</p>
      </div>
    </nav>
  );
}

function ModeChip() {
  const { snapshot } = useLive();
  if (!snapshot) return null;
  if (snapshot.mode === "simulation") return <SimBadge />;
  const label = snapshot.mode === "recorded" ? "Recorded video" : "Live camera";
  return (
    <span className="chip border-sky-400/50 bg-sky-500/15 text-sky-100">
      <Video className="h-3.5 w-3.5" /> {label}
    </span>
  );
}

function CameraHealthChip() {
  const { snapshot } = useLive();
  if (!snapshot) return null;
  const cams = snapshot.cameras;
  const offline = cams.filter((c) => c.offline).length;
  const ok = offline === 0 && cams.length > 0;
  return (
    <span className={`chip ${ok ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200" : "border-red-400/50 bg-red-500/15 text-red-100"}`} title={cams.map((c) => `${c.camera_id}: ${c.offline ? "offline" : "online"}`).join("\n")}>
      {ok ? <Camera className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
      {cams.length - offline}/{cams.length} camera{cams.length === 1 ? "" : "s"} online
    </span>
  );
}

export function TopBar({ onMenu }: { onMenu?: () => void }) {
  const { snapshot, connected } = useLive();
  return (
    <header className="sticky top-0 z-20 flex flex-wrap items-center gap-3 border-b border-ink-700 bg-ink-900/90 px-4 py-3 backdrop-blur sm:px-6">
      {onMenu && (
        <button className="btn-ghost p-1.5 lg:hidden" onClick={onMenu} aria-label="Open menu">
          <Menu className="h-5 w-5" />
        </button>
      )}
      <div className="min-w-0">
        <p className="text-[11px] uppercase tracking-wider text-txt-faint">Restaurant</p>
        <p className="truncate text-sm font-semibold">{snapshot?.restaurant.name ?? "Connecting…"}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <ModeChip />
        <CameraHealthChip />
        {snapshot?.run?.label && <span className="hidden text-xs text-txt-muted md:inline">{snapshot.run.label}</span>}
      </div>
      <div className="ml-auto flex items-center gap-3">
        {!connected && (
          <span className="chip animate-soft-pulse border-amber-400/50 bg-amber-500/15 text-amber-100" role="status">
            Reconnecting to server…
          </span>
        )}
        <span className="flex items-center gap-1.5 font-mono text-sm tabular-nums text-txt" title={snapshot?.clock.simulated ? "Simulated time" : "Local time"}>
          <Clock className="h-4 w-4 text-txt-muted" aria-hidden />
          {clockTime(snapshot?.clock.iso)}
          {snapshot?.clock.simulated && <span className="text-[11px] text-fuchsia-300">sim</span>}
        </span>
      </div>
    </header>
  );
}

export function Shell() {
  const [menu, setMenu] = useState(false);
  const { snapshot } = useLive();
  useEffect(() => {
    document.title = snapshot?.summary.active_alerts ? `(${snapshot.summary.active_alerts}) TableWatch AI` : "TableWatch AI";
  }, [snapshot?.summary.active_alerts]);
  return (
    <div className="flex min-h-full">
      <aside className="hidden w-60 shrink-0 border-r border-ink-700 bg-ink-850 lg:block">
        <div className="sticky top-0 h-screen">
          <Sidebar />
        </div>
      </aside>
      {menu && (
        <div className="fixed inset-0 z-30 bg-ink-950/70 lg:hidden" onClick={() => setMenu(false)}>
          <aside className="h-full w-64 border-r border-ink-700 bg-ink-850" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-end p-2">
              <button className="btn-ghost p-1.5" onClick={() => setMenu(false)} aria-label="Close menu">
                <X className="h-5 w-5" />
              </button>
            </div>
            <Sidebar onNavigate={() => setMenu(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setMenu(true)} />
        {snapshot?.error && (
          <div role="alert" className="mx-4 mt-3 rounded-lg border border-red-400/40 bg-red-500/10 px-3 py-2 text-sm text-red-200 sm:mx-6">
            Server reported a problem: {snapshot.error}
          </div>
        )}
        <main className="flex-1 px-4 py-5 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
