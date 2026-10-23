import { memo } from "react";
import { duration, isUncertain, tableLook } from "../lib/format";
import type { Layout, LayoutTable, Person, TableView, ZoneDef } from "../types/api";

interface Props {
  layout: Layout;
  tables: TableView[];
  selected?: string | null;
  onSelect?: (id: string) => void;
  people?: Person[];
  showZones?: boolean;
  zones?: ZoneDef[];
  showTrackIds?: boolean;
  compact?: boolean;
  className?: string;
}

function timerFor(t: TableView): string | null {
  if (t.state === "SEATED_UNSERVED") return duration(t.wait_s);
  if (t.state === "SERVED") return duration(t.occupied_s);
  if (t.state === "NEEDS_CLEANING") return duration(t.cleaning_s);
  return null;
}

function Table({ geo, view, selected, onSelect }: { geo: LayoutTable; view?: TableView; selected: boolean; onSelect?: (id: string) => void }) {
  const [cx, cy] = geo.center;
  const [w, h] = geo.size;
  const x = cx - w / 2;
  const y = cy - h / 2;
  const look = view ? tableLook(view) : { color: "#64748B", label: "Unknown" };
  const uncertain = view ? isUncertain(view.health) : true;
  const overdue = !!view && (view.unserved_overdue || view.cleaning_overdue);
  const alerts = view?.alerts.filter((a) => a.status !== "RESOLVED").length ?? 0;
  const timer = view ? timerFor(view) : null;
  const booth = geo.shape === "booth";
  const tagY = cy + h / 2 + 1.08;
  const label = geo.label.replace("Table ", "T").replace("Booth ", "B");
  const aria = `${geo.label}: ${look.label}${timer ? `, ${timer}` : ""}${alerts ? `, ${alerts} alert(s)` : ""}${uncertain && view ? `, ${view.health.toLowerCase().replace("_", " ")}` : ""}`;
  return (
    <g
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={aria}
      data-testid={`table-${geo.id}`}
      className={onSelect ? "cursor-pointer outline-none [&:focus-visible>rect.focus]:opacity-100" : ""}
      onClick={() => onSelect?.(geo.id)}
      onKeyDown={(e) => {
        if (onSelect && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onSelect(geo.id);
        }
      }}
    >
      <title>{aria}</title>
      <rect className="focus opacity-0 transition" x={x - 0.75} y={y - 0.75} width={w + 1.5} height={h + 1.5} rx={0.3} fill="none" stroke="#2DD4BF" strokeWidth={0.06} />
      {selected && <rect x={x - 0.75} y={y - 0.75} width={w + 1.5} height={h + 1.5} rx={0.3} fill="#2DD4BF" fillOpacity={0.07} stroke="#2DD4BF" strokeWidth={0.07} />}
      {booth ? (
        <>
          <rect x={x - 0.1} y={y - 0.55} width={w + 0.2} height={0.38} rx={0.12} fill="#2A3858" />
          <rect x={x - 0.1} y={y + h + 0.17} width={w + 0.2} height={0.38} rx={0.12} fill="#2A3858" />
        </>
      ) : (
        geo.chairs.map(([chx, chy], i) => <circle key={i} cx={chx} cy={chy} r={0.2} fill="#2A3858" stroke="#3B4B70" strokeWidth={0.03} />)
      )}
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={0.14}
        fill={look.color}
        fillOpacity={view?.state === "FREE" ? 0.12 : 0.26}
        stroke={look.color}
        strokeWidth={overdue ? 0.09 : 0.055}
        strokeDasharray={uncertain && view?.health !== "UNKNOWN" ? "0.18 0.12" : undefined}
        className={overdue ? "animate-soft-pulse" : undefined}
      />
      {uncertain && view?.health !== "UNKNOWN" && <rect x={x} y={y} width={w} height={h} rx={0.14} fill="url(#hatch)" />}
      <text x={cx} y={cy + 0.12} textAnchor="middle" fontSize={0.36} fontWeight={700} fill="#E7EDF7">
        {label}
      </text>
      {view && (
        <g>
          <rect x={cx - 1.3} y={tagY - 0.22} width={2.6} height={0.4} rx={0.2} fill="#0B1220" fillOpacity={0.9} stroke={look.color} strokeWidth={0.03} />
          <text x={cx} y={tagY + 0.06} textAnchor="middle" fontSize={0.2} fill={look.color} fontWeight={600}>
            {look.label}
            {timer ? ` · ${timer}` : ""}
          </text>
        </g>
      )}
      {alerts > 0 && (
        <g>
          <circle cx={x + w + 0.05} cy={y - 0.05} r={0.22} fill="#EF4444" stroke="#0B1220" strokeWidth={0.05} />
          <text x={x + w + 0.05} y={y + 0.03} textAnchor="middle" fontSize={0.26} fontWeight={800} fill="#fff">
            !
          </text>
        </g>
      )}
      {uncertain && view && view.health !== "UNKNOWN" && (
        <g>
          <circle cx={x - 0.05} cy={y - 0.05} r={0.2} fill="#94A3B8" stroke="#0B1220" strokeWidth={0.05} />
          <text x={x - 0.05} y={y + 0.03} textAnchor="middle" fontSize={0.24} fontWeight={800} fill="#0B1220">
            ?
          </text>
        </g>
      )}
    </g>
  );
}

function FloorPlanImpl({ layout, tables, selected, onSelect, people, showZones, zones, showTrackIds, compact, className = "" }: Props) {
  const W = layout.width_m;
  const H = layout.height_m;
  const byId = new Map(tables.map((t) => [t.table_id, t]));
  const zoneList = zones ?? layout.sim_camera.zones;
  const fixtureColor: Record<string, string> = { service: "#1D2944", bar: "#1A2440", host: "#23304D", door: "#2DD4BF", restroom: "#1D2944" };
  return (
    <svg viewBox={`-0.3 -0.3 ${W + 0.6} ${H + 0.6}`} className={`h-auto w-full select-none ${className}`} role="group" aria-label={`${layout.name} floor plan`}>
      <defs>
        <pattern id="floor" width="1" height="1" patternUnits="userSpaceOnUse">
          <path d="M 1 0 L 0 0 0 1" fill="none" stroke="#17223A" strokeWidth="0.02" />
        </pattern>
        <pattern id="hatch" width="0.3" height="0.3" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="0.3" stroke="#94A3B8" strokeOpacity="0.45" strokeWidth="0.07" />
        </pattern>
      </defs>
      <rect x={0} y={0} width={W} height={H} rx={0.25} fill="#0E1628" />
      <rect x={0} y={0} width={W} height={H} rx={0.25} fill="url(#floor)" />
      <rect x={0} y={0} width={W} height={H} rx={0.25} fill="none" stroke="#3B4B70" strokeWidth={0.08} />
      {!compact &&
        layout.areas.map((a) => (
          <text key={a.id} x={a.rect[0] + 0.15} y={a.rect[1] + 0.3} fontSize={0.26} fill="#64748B" letterSpacing={0.04} fontWeight={600}>
            {a.name.toUpperCase()}
          </text>
        ))}
      {layout.fixtures.map((f) => {
        const [x0, y0, x1, y1] = f.rect;
        if (f.kind === "door")
          return (
            <g key={f.id}>
              <rect x={x0 - 0.05} y={y0} width={0.18} height={y1 - y0} fill="#2DD4BF" fillOpacity={0.5} rx={0.05} />
              {!compact && (
                <text x={x0 + 0.3} y={(y0 + y1) / 2 + 0.45} fontSize={0.24} fill="#5EEAD4" transform={`rotate(-90 ${x0 + 0.3} ${(y0 + y1) / 2 + 0.45})`}>
                  ENTRANCE
                </text>
              )}
            </g>
          );
        return (
          <g key={f.id}>
            <rect x={x0} y={y0} width={x1 - x0} height={y1 - y0} rx={0.15} fill={fixtureColor[f.kind] ?? "#1D2944"} stroke="#2A3858" strokeWidth={0.04} />
            {!compact && (
              <text x={(x0 + x1) / 2} y={(y0 + y1) / 2 + 0.1} textAnchor="middle" fontSize={0.24} fill="#93A3BE">
                {f.name}
              </text>
            )}
          </g>
        );
      })}
      {showZones &&
        zoneList.map((z) => (
          <polygon
            key={z.table_id}
            points={z.polygon.map(([px, py]) => `${px * W},${py * H}`).join(" ")}
            fill="#38BDF8"
            fillOpacity={0.05}
            stroke="#38BDF8"
            strokeOpacity={0.6}
            strokeWidth={0.04}
            strokeDasharray="0.2 0.12"
          />
        ))}
      {layout.tables.map((g) => (
        <Table key={g.id} geo={g} view={byId.get(g.id)} selected={selected === g.id} onSelect={onSelect} />
      ))}
      {people?.map((p) => {
        const fill = p.role === "staff" ? "#F59E0B" : p.role === "passerby" ? "#94A3B8" : "#E2E8F0";
        return (
          <g key={p.id} transform={`translate(${p.x * W} ${p.y * H})`} style={{ transition: "transform 450ms linear" }} pointerEvents="none">
            <circle r={0.21} fill={fill} fillOpacity={p.detected ? 0.95 : 0.25} stroke={p.detected ? "#0B1220" : fill} strokeWidth={0.04} strokeDasharray={p.detected ? undefined : "0.08 0.06"} />
            {showTrackIds && p.track_id !== null && (
              <text y={-0.3} textAnchor="middle" fontSize={0.2} fill="#BAE6FD" fontFamily="monospace">
                #{p.track_id}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export const FloorPlan = memo(FloorPlanImpl);

export function Legend() {
  const items = [
    { c: "#2DD4BF", l: "Available" },
    { c: "#60A5FA", l: "Awaiting service" },
    { c: "#F87171", l: "Waiting too long" },
    { c: "#A78BFA", l: "Served" },
    { c: "#FBBF24", l: "Needs cleaning" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-txt-muted">
      {items.map((i) => (
        <span key={i.l} className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: i.c }} />
          {i.l}
        </span>
      ))}
      <span className="inline-flex items-center gap-1.5">
        <span className="hatch h-2.5 w-2.5 rounded-sm border border-slate-400/60" />
        Uncertain / camera offline
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="flex h-3 w-3 items-center justify-center rounded-full bg-red-500 text-[8px] font-bold text-white">!</span>
        Open alert
      </span>
    </div>
  );
}
