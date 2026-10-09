import { expect, test, type Page } from "@playwright/test";

// Hospital flow Control Tower (spec 7.1): the three boards following the day simulator, the drill-down
// unit -> bed grid -> patient card with fields hidden by role, and an exception narrated by the AI, approved by
// the bed manager into Tasks for the owner roles, with the approval in the audit log.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

const minutesOf = (hhmm: string) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));

async function login(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test.beforeAll(async ({ request }) => {
  const admin = await (await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } })).json();
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${admin.token}` } });
});

test("board: three columns from FHIR that follow the simulator within 2 seconds", async ({ page }) => {
  await login(page, "operations_manager");
  await page.getByRole("link", { name: "Control Tower" }).click();
  for (const board of ["board-ed", "board-beds", "board-or", "exception-stream"]) {
    await expect(page.getByTestId(board)).toBeVisible();
  }
  await expect(page.getByTestId("unit-list").locator("li")).toHaveCount(5);
  await expect(page.getByTestId("kpi-occupancy")).toContainText("%");
  await expect(page.getByTestId("kpi-ed-wait")).toContainText("min");
  await expect(page.getByTestId("or-row").first()).toBeVisible();
  await expect(page.getByTestId("exception-card").first()).toBeVisible();
  await expect(page.getByTestId("not-evaluated-control_tower")).toHaveCount(0); // only in the drawer
  await shot(page, "ct-01-board");

  // +30 min: once the simulator has committed the half hour, the board shows it within 2 s
  const clock = page.getByTestId("hospital-time");
  const before = await clock.textContent();
  const advanced = page.waitForResponse((r) => r.url().includes("/api/hospital/simulator/advance") && r.ok());
  await page.getByTestId("sim-advance").click();
  await advanced;
  await expect(clock).not.toHaveText(before ?? "", { timeout: 2000 });

  // running at 15 hospital hours per minute: three times, the board catches up with the simulator's own clock
  // (GET /api/hospital/simulator) within 2 s
  await page.getByTestId("sim-rate").selectOption("900");
  await page.getByTestId("sim-run").click();
  await expect(page.getByTestId("clock-state")).toContainText("running");
  const token = await page.evaluate(() => localStorage.getItem("ioc.token"));
  for (let i = 0; i < 3; i++) {
    const sim = await (await page.request.get("/api/hospital/simulator", { headers: { Authorization: `Bearer ${token}` } })).json();
    const simMinutes = minutesOf(String(sim.now).slice(11, 16));
    await expect.poll(async () => minutesOf(((await clock.textContent()) ?? "").slice(-5)), { timeout: 2000 }).toBeGreaterThanOrEqual(simMinutes);
    await page.waitForTimeout(700);
  }
  await expect(page.getByTestId("event-ticker")).not.toContainText("No events yet");
  await page.getByTestId("sim-pause").click();
  await expect(page.getByTestId("clock-state")).toContainText("paused");

  // the dark / light theme
  await expect(page.getByTestId("control-tower")).toHaveAttribute("data-theme", /dark|light/);
  const theme = await page.getByTestId("control-tower").getAttribute("data-theme");
  await page.getByTestId("theme-toggle").click();
  await expect(page.getByTestId("control-tower")).not.toHaveAttribute("data-theme", theme ?? "");
  await shot(page, "ct-02-theme");
});

test("drill-down: unit, bed grid, patient card with fields hidden by role", async ({ page }) => {
  // the bed manager: MRN, bed, admission and expected discharge only
  await login(page, "operations_manager");
  await page.goto("/control-tower");
  await page.getByTestId("unit-MEDA").click();
  const grid = page.getByTestId("bed-grid");
  await expect(grid).toBeVisible();
  const bed = grid.locator("button:not([disabled])").first();
  const bedId = (await bed.getAttribute("data-testid"))!.replace("bed-", "");
  await bed.click();
  const card = page.getByTestId("patient-card");
  await expect(card.getByTestId("card-fields")).toContainText("MRN");
  await expect(card.getByTestId("card-fields")).toContainText("Expected discharge");
  await expect(card.getByTestId("card-hidden")).toContainText("Hidden for your role");
  await expect(card.getByTestId("card-hidden")).toContainText("Name");
  await expect(card.locator('[data-field="Name"]')).toHaveCount(0);
  await expect(card.locator('[data-field="Reason for admission"]')).toHaveCount(0);
  await shot(page, "ct-03-card-ops");

  // the Medicine A nurse: the clinical card of the same bed
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Switch role" }).click();
  await login(page, "nurse");
  await page.goto("/control-tower");
  await page.getByTestId("unit-MEDA").click();
  await page.getByTestId(`bed-${bedId}`).click();
  await expect(card.locator('[data-field="Name"]')).not.toHaveText("–");
  await expect(card.locator('[data-field="Reason for admission"]')).toBeVisible();
  await expect(card.getByTestId("card-hidden")).toHaveCount(0);
  await shot(page, "ct-04-card-nurse");

  // the Medicine A physician on an ICU patient outside their units: break-glass needed
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Switch role" }).click();
  await login(page, "physician");
  await page.goto("/control-tower");
  await page.getByTestId("unit-ICU").click();
  await page.getByTestId("bed-ICU-01-A").click();
  await expect(page.getByTestId("card-restricted")).toContainText("Outside your units");
  await shot(page, "ct-05-card-restricted");
});

test("exception -> AI narrative -> approve -> Tasks -> audit", async ({ page, request }) => {
  await login(page, "operations_manager");
  await page.goto("/control-tower");
  const card = page.getByTestId("exception-card").first();
  const exceptionId = await card.getAttribute("data-exception");
  await card.click();
  const drawer = page.getByTestId("action-drawer");
  await expect(drawer.getByTestId("narrative")).not.toBeEmpty();
  await expect(drawer.getByText("AI-generated")).toBeVisible();
  await expect(drawer.getByTestId("not-evaluated-control_tower")).toBeVisible(); // eval pending: flagged in demo mode
  await expect(drawer.getByTestId("evidence").locator("code").first()).toBeVisible();
  await expect(drawer.getByTestId("actions").locator("li")).not.toHaveCount(0);
  await shot(page, "ct-06-drawer");
  await drawer.getByTestId("approve").click();
  const tasks = drawer.getByTestId("created-tasks");
  await expect(tasks).toBeVisible();
  await expect(tasks.locator("li").first()).toContainText("Task task-");
  await expect(drawer.getByTestId("decision")).toContainText("Approved by");
  await shot(page, "ct-07-approved");
  await page.keyboard.press("Escape");
  await expect(page.locator(`[data-exception="${exceptionId}"]`)).toContainText("approved by");

  // the Tasks exist in the EHR for the actions' owner roles
  const ops = await (await request.post("/api/auth/login", { data: { user_id: "U-OPS" } })).json();
  const detail = await (await request.get(`/api/control-tower/exceptions/${exceptionId}`, { headers: { Authorization: `Bearer ${ops.token}` } })).json();
  expect(detail.status).toBe("approved");
  expect(detail.decision.actions.every((a: { task_id: string | null }) => a.task_id)).toBe(true);

  // the approval is in the audit log
  await page.getByRole("button", { name: "Switch role" }).click();
  await login(page, "admin");
  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByRole("tab", { name: "Approvals" }).click();
  const approval = page.getByTestId("audit-approve").filter({ hasText: detail.review_task_id });
  await expect(approval.first()).toContainText("accept");
  await expect(approval.first()).toContainText("control_tower");
  await page.getByRole("tab", { name: "Tool calls" }).click();
  await expect(page.getByTestId("audit-tool_call").filter({ hasText: "createFlowTask" }).first()).toBeVisible();
  await expect(page.getByText(/Chain intact/)).toBeVisible();
  await shot(page, "ct-08-audit");
});
