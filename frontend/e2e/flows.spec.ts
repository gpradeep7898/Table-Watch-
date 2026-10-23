import { expect, test, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8765";

async function startScenario(page: Page, scenario: string) {
  const r = await page.request.post(`${API}/api/sim/start`, { data: { scenario, autoplay: false } });
  expect(r.ok()).toBeTruthy();
}
async function advance(page: Page, seconds: number) {
  const r = await page.request.post(`${API}/api/sim/advance`, { data: { seconds } });
  expect(r.ok()).toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  await page.request.put(`${API}/api/settings/simulation`, { data: { staff_auto_confirm: false } });
  await page.request.post(`${API}/api/settings/thresholds/reset`);
});

test("overview shows ten tables and the simulation label", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("kpi-total")).toContainText("10");
  await expect(page.getByText("SIMULATED DATA").first()).toBeVisible();
  for (let i = 1; i <= 10; i++) await expect(page.getByTestId(`table-T${i}`)).toBeVisible();
});

test("select scenario, change speed, pause and resume", async ({ page }) => {
  await page.goto("/");
  await page.locator("#scenario-select").selectOption("rush");
  await expect(page.getByText("D: Peak-hour rush").first()).toBeVisible();
  await page.getByTestId("speed-10").click();
  await expect(page.getByTestId("speed-10")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("sim-playpause").click(); // pause
  await expect(page.getByRole("button", { name: "Play simulation" })).toBeVisible();
  const t1 = await (await page.request.get(`${API}/api/state`)).json();
  await page.waitForTimeout(800);
  const t2 = await (await page.request.get(`${API}/api/state`)).json();
  expect(t2.clock.t).toBe(t1.clock.t); // paused time does not move
  await page.getByTestId("sim-playpause").click(); // resume
  await expect(page.getByRole("button", { name: "Pause simulation" })).toBeVisible();
  await page.getByTestId("sim-playpause").click();
});

test("waiting alert, acknowledge, confirm service from the table panel", async ({ page }) => {
  await startScenario(page, "forgotten");
  await advance(page, 340);
  await page.goto("/floor");
  await page.getByTestId("table-T4").click();
  const panel = page.getByTestId("table-detail");
  await expect(panel).toContainText("Waiting too long");
  await panel.getByRole("button", { name: "Acknowledge" }).click();
  await expect(panel).toContainText("Acknowledged by");
  await panel.getByTestId("confirm-service").click();
  await expect(panel).toContainText("Served");
  await expect(panel.getByTestId("confirm-service")).toBeDisabled();
  await expect(panel).toContainText("Service confirmed");
});

test("confirm cleaning from the alert center", async ({ page }) => {
  await startScenario(page, "slow_cleaning");
  await advance(page, 450);
  await page.goto("/alerts");
  const row = page.getByTestId("alert-TABLE_NEEDS_CLEANING").first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Confirm cleaning" }).click();
  await expect(page.getByText("Cleaning confirmed").first()).toBeVisible();
  const state = await (await page.request.get(`${API}/api/state`)).json();
  expect(state.tables.find((t: any) => t.table_id === "T5").state).toBe("FREE");
});

test("invalid action is explained, correction needs a reason", async ({ page }) => {
  await startScenario(page, "normal");
  await page.goto("/floor?table=T2");
  const panel = page.getByTestId("table-detail");
  await expect(panel.getByTestId("confirm-cleaning")).toBeDisabled();
  await panel.getByTestId("correct-status").click();
  await page.getByLabel("Guests seated, not served").check();
  const save = page.getByRole("button", { name: "Save correction" });
  await expect(save).toBeDisabled();
  await page.getByLabel("Reason (required)").fill("Walk-in at the window");
  await save.click();
  await expect(panel).toContainText("Awaiting service");
  await expect(panel).toContainText("Status corrected");
});

test("analytics come from events and the staffing ratio is adjustable", async ({ page }) => {
  await startScenario(page, "normal");
  await advance(page, 1700);
  await page.goto("/analytics");
  const kpis = page.getByTestId("analytics-kpis");
  await expect(kpis).toContainText("Occupancy rate");
  await expect(kpis).toContainText("6 completed visits");
  await page.locator("#ratio").fill("2");
  await expect(page.getByText("2 tables per server")).toBeVisible();
});

test("zone editor saves and reloads a polygon", async ({ page }) => {
  await page.goto("/cameras");
  await page.getByTestId("tab-zones").click();
  const editor = page.getByTestId("zone-editor");
  await expect(editor).toContainText("Zones (10)");
  await page.getByRole("button", { name: "Delete zone T10" }).click();
  await page.getByTestId("new-zone").click();
  const svg = editor.getByRole("application");
  const box = (await svg.boundingBox())!;
  const pt = (x: number, y: number) => page.mouse.click(box.x + x * box.width, box.y + y * box.height);
  await pt(0.5, 0.74);
  await pt(0.64, 0.74);
  await pt(0.64, 0.93);
  await pt(0.5, 0.93);
  await page.getByRole("button", { name: "Finish zone" }).click();
  await expect(editor.locator("#zone-table-9")).toHaveValue("T10");
  await page.getByTestId("save-zones").click();
  await expect(page.getByText(/Zones saved \(version \d+\)/)).toBeVisible();
  const z = await (await page.request.get(`${API}/api/zones/sim-cam-1`)).json();
  const t10 = z.zones.find((x: any) => x.table_id === "T10");
  expect(t10.polygon[0][0]).toBeCloseTo(0.5, 1);
  await page.reload();
  await page.getByTestId("tab-zones").click();
  await expect(page.getByTestId("zone-editor")).toContainText("Zones (10)");
  await page.getByRole("button", { name: "Reset to demo zones" }).click();
});

test("thresholds validate and persist", async ({ page }) => {
  await page.goto("/settings");
  await page.locator("#th-unserved_warning_s").fill("5");
  await page.getByTestId("save-thresholds").click();
  await expect(page.getByRole("alert")).toContainText("between 30 and 7200");
  await page.locator("#th-unserved_warning_s").fill("240");
  await page.getByTestId("save-thresholds").click();
  await expect(page.getByText(/Thresholds saved/)).toBeVisible();
  await page.reload();
  await expect(page.locator("#th-unserved_warning_s")).toHaveValue("240");
});

test("presentation mode starts the guided story", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByText(/This is not camera footage/)).toBeVisible();
  await page.getByTestId("start-presentation").click();
  await expect(page.getByLabel("Scenario")).toHaveValue("guided");
  await page.request.post(`${API}/api/sim/control`, { data: { action: "pause" } });
  await advance(page, 340);
  await expect(page.getByText("4. The waiting alert appears")).toBeVisible();
  await page.getByTestId("alert-UNSERVED_TOO_LONG").filter({ hasText: "Table 6" }).getByRole("button", { name: "Confirm service" }).click();
  await expect(page.locator("li", { hasText: "5. Service is confirmed" }).locator("svg.text-st-free")).toBeVisible();
});
