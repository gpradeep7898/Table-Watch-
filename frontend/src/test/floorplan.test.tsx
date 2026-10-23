import { fireEvent, render, screen } from "@testing-library/react";
import { FloorPlan } from "../components/FloorPlan";
import type { Layout, TableView } from "../types/api";

const layout: Layout = {
  id: "x", name: "Test Bistro", width_m: 10, height_m: 6, areas: [], fixtures: [],
  tables: [
    { id: "T1", label: "Table 1", area: "Main", seats: 2, center: [2, 2], size: [1, 1], shape: "rect", chairs: [[1.2, 2], [2.8, 2]] },
    { id: "T2", label: "Table 2", area: "Main", seats: 2, center: [6, 2], size: [1, 1], shape: "rect", chairs: [] },
  ],
  sim_camera: { id: "sim", anchor: "centroid", zones_version: 0, zones: [] },
};

function view(id: string, over: Partial<TableView>): TableView {
  return {
    table_id: id, label: id, seats: 2, area: "Main", camera_id: "sim", state: "FREE", session_id: null, seated_t: null, service_t: null,
    service_source: null, left_t: null, cleaning_required_t: null, wait_s: null, occupied_s: null, cleaning_s: null,
    unserved_overdue: false, cleaning_overdue: false, guest_count: 0, present_count: 0, uncertain_count: 0, seating_candidate_s: null,
    absence_s: null, visits: 0, last_visit_t: null, reassociations: 0, health: "OK", uncertainty: [], alerts: [], ...over,
  };
}

test("tables show status text, timers, alerts and uncertainty, and are clickable", () => {
  const onSelect = vi.fn();
  render(
    <FloorPlan
      layout={layout}
      tables={[
        view("T1", { state: "SEATED_UNSERVED", wait_s: 330, unserved_overdue: true, alerts: [{ id: "a", type: "UNSERVED_TOO_LONG", severity: "warning", status: "ACTIVE", dedup_key: "k", table_id: "T1", camera_id: null, session_id: null, message: "" }] }),
        view("T2", { health: "OFFLINE" }),
      ]}
      onSelect={onSelect}
    />,
  );
  const t1 = screen.getByRole("button", { name: /Table 1: Waiting too long, 5:30, 1 alert/ });
  expect(t1).toBeInTheDocument();
  expect(screen.getByText(/Waiting too long · 5:30/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Table 2: Available.*offline/ })).toBeInTheDocument();
  fireEvent.click(t1);
  expect(onSelect).toHaveBeenCalledWith("T1");
  fireEvent.keyDown(screen.getByTestId("table-T2"), { key: "Enter" });
  expect(onSelect).toHaveBeenCalledWith("T2");
});
