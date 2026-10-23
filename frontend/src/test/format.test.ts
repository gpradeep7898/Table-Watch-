import { duration, eventDetail, pct, tableLook } from "../lib/format";

test("duration formats seconds for timers", () => {
  expect(duration(0)).toBe("0:00");
  expect(duration(65)).toBe("1:05");
  expect(duration(3725)).toBe("1h 02m");
  expect(duration(null)).toBe("—");
});

test("overdue waiting table gets red treatment and a text label", () => {
  const look = tableLook({ state: "SEATED_UNSERVED", unserved_overdue: true, cleaning_overdue: false, health: "OK" });
  expect(look.label).toBe("Waiting too long");
  expect(look.color).toBe("#F87171");
  expect(tableLook({ state: "NEEDS_CLEANING", unserved_overdue: false, cleaning_overdue: true, health: "OK" }).label).toBe("Cleaning overdue");
});

test("visits are described as unverified, never as service", () => {
  expect(eventDetail({ type: "POSSIBLE_STAFF_VISIT", payload: { kind: "possible_service_visit", duration_s: 30 } })).toMatch(/not counted as service/);
  expect(pct(0.456)).toBe("46%");
});
