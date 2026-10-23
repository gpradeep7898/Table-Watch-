import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FloorPlan, Legend } from "../components/FloorPlan";
import { SimControls } from "../components/SimControls";
import { TableDetail } from "../components/TableDetail";
import { Card, Empty, Toggle } from "../components/ui";
import { useLive } from "../hooks/useLive";
import { getLocal, setLocal } from "../lib/format";

export function LiveFloorPage() {
  const { snapshot, layout, frames } = useLive();
  const [params, setParams] = useSearchParams();
  const selected = params.get("table");
  const [people, setPeople] = useState<boolean>(() => getLocal("tw.floor.people", true));
  const [zones, setZones] = useState<boolean>(() => getLocal("tw.floor.zones", false));
  if (!snapshot || !layout) return <p className="text-sm text-txt-muted">Connecting…</p>;
  const sim = snapshot.mode === "simulation";
  const simFrame = frames?.frames.find((f) => f.simulated);
  const select = (id: string | null) => setParams(id ? { table: id } : {});
  return (
    <div className="space-y-5">
      <SimControls />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_26rem]">
        <Card
          title="Live floor"
          actions={
            <div className="flex flex-wrap gap-4">
              {sim && (
                <Toggle id="people" label="Simulated people" checked={people} onChange={(v) => { setPeople(v); setLocal("tw.floor.people", v); }} />
              )}
              <Toggle id="zones" label="Seating zones" checked={zones} onChange={(v) => { setZones(v); setLocal("tw.floor.zones", v); }} />
            </div>
          }
        >
          <FloorPlan
            layout={layout}
            tables={snapshot.tables}
            selected={selected}
            onSelect={select}
            people={sim && people ? simFrame?.people : undefined}
            showZones={zones}
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <Legend />
            {sim && people && (
              <p className="text-xs text-txt-faint">
                Dots are simulated people (light: guests, amber: staff). Hollow dots were not detected this frame.
              </p>
            )}
          </div>
        </Card>
        {selected ? (
          <TableDetail tableId={selected} onClose={() => select(null)} />
        ) : (
          <Card title="Table details">
            <Empty title="Select a table" body="Click or tab to any table on the floor plan to see its timers, evidence and actions." />
          </Card>
        )}
      </div>
    </div>
  );
}
