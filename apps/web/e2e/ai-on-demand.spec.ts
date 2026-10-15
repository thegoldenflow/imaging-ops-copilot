import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// AI only as a visible consequence of what a person does (BACKGROUND_AI=on_demand, apps/api/app/agents/ondemand.py):
// list pages say what the rules checked and that the AI has not run; opening an item runs its AI and shows it running,
// then the result with when it was generated and how long it took; a failure says why and offers Retry; a batch states
// its time before it runs; "What the AI did in this demo" lists every AI action this browser caused. The API side:
// apps/api/tests/test_ai_on_demand.py and test_background_ai.py.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function auth(request: APIRequestContext, userId: string) {
  const { token } = await (await request.post("/api/auth/login", { data: { user_id: userId } })).json();
  return { Authorization: `Bearer ${token}` };
}

async function login(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

/** Hold the AI request a moment in the browser, so the running state is on screen long enough to check. */
async function slowAi(page: Page, pattern: string | RegExp, ms = 2500) {
  await page.route(pattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await new Promise((r) => setTimeout(r, ms));
    await route.continue();
  });
}

const NAPROXEN = "MedicationRequest/medrx-ms-82-naproxen";
let review = { id: "", mrn: "" };

test.beforeAll(async ({ request }, testInfo) => {
  testInfo.setTimeout(240_000);
  const health = await (await request.get("/api/health")).json();
  test.skip(health.background?.ai !== "on_demand", "the test API runs BACKGROUND_AI=eager");
  await request.post("/api/demo/reset", { headers: await auth(request, "U-ADMIN") });
  const queue = await (await request.get("/api/med-safety/queue?show=all", { headers: await auth(request, "U-PHAR-01") })).json();
  const naproxen = (queue.items as { id: string; order_ref?: string; mrn?: string }[]).find((i) => i.order_ref === NAPROXEN)!;
  review = { id: naproxen.id, mrn: naproxen.mrn ?? "" };
});

test("a list never runs the AI by itself; the activity panel starts empty", async ({ page }) => {
  await login(page, "pharmacist");
  await page.getByRole("link", { name: "Medication safety" }).click();
  const batch = page.getByTestId("ms-queue").getByTestId("ai-batch");
  await expect(batch).toContainText("checked by the rules, AI not run");
  await expect(page.getByTestId("ms-queue").getByTestId("ai-chip").first()).toHaveAttribute("data-state", "not_requested");
  await page.getByTestId("ai-activity-button").click();
  const panel = page.getByTestId("ai-activity-panel");
  await expect(panel.getByTestId("ai-activity-empty")).toBeVisible();
  await expect(panel.getByTestId("ai-totals")).toContainText("Model calls0");
  await shot(page, "ai-01-nothing-run");
});

test("opening an order review runs its AI: running, then the result with its time; the panel lists it", async ({ page }) => {
  await slowAi(page, /\/api\/med-safety\/reviews\/[^/]+\/commentary/);
  await login(page, "pharmacist");
  await page.getByRole("link", { name: "Medication safety" }).click();
  await page.getByTestId("queue-search").fill(review.mrn);
  await page.getByTestId(`queue-item-${review.id}`).click();
  const panel = page.getByTestId("review-panel");
  await expect(panel.getByTestId("finding").filter({ hasText: "naproxen + warfarin" })).toBeVisible(); // the rules at once
  const running = panel.getByTestId("ai-running");
  await expect(running).toContainText("AI is reviewing this order");
  await expect(running).toContainText("typically");
  await shot(page, "ai-02-running");
  await expect(panel.getByTestId("ai-concern").first()).toBeVisible({ timeout: 30_000 });
  const meta = panel.getByTestId("ai-meta");
  await expect(meta).toContainText("generated");
  await expect(meta).toContainText("took");
  await expect(meta).toContainText("when you opened it");
  await shot(page, "ai-03-done");

  // opened again: the result is cached, no second call
  await page.reload();
  await page.getByTestId("queue-search").fill(review.mrn);
  await page.getByTestId(`queue-item-${review.id}`).click();
  await expect(panel.getByTestId("ai-meta")).toContainText("when you opened it");
  await expect(panel.getByTestId("ai-running")).toHaveCount(0);

  await page.getByTestId("ai-activity-button").click();
  const activity = page.getByTestId("ai-activity-panel");
  const action = activity.getByTestId("ai-action");
  await expect(action).toHaveCount(1);
  await expect(action).toContainText("Reviewed an order");
  await expect(action.getByTestId("ai-action-trigger")).toContainText("when you opened it");
  await expect(action.getByTestId("ai-action-cost")).toContainText("$");
  await expect(activity.getByTestId("ai-totals")).toContainText("Model calls1");
  await shot(page, "ai-04-activity");
  await expect(activity.getByTestId("ai-scope-all")).toHaveCount(0); // everyone's actions: the operations roles only
});

test("the AI unavailable: why, Retry, and the rules still shown", async ({ page, request }) => {
  const pharmacist = await auth(request, "U-PHAR-01");
  const queue = await (await request.get("/api/med-safety/queue?show=all", { headers: pharmacist })).json();
  const other = (queue.items as { id: string; kind: string; ai_state: string; commentary_status: string; mrn: string; status: string }[])
    .find((i) => i.kind === "order_review" && i.ai_state === "not_requested" && i.commentary_status === "on_open" && i.status === "pending")!;
  // the server answers the first opening as if the model were down
  let failed = false;
  await page.route(new RegExp(`/api/med-safety/reviews/${other.id}/commentary`), async (route) => {
    if (failed) return route.continue();
    failed = true;
    const response = await route.fetch({ url: route.request().url().replace(/\/commentary.*$/, ""), method: "GET" });
    const body = await response.json();
    body.ai = { ...body.ai, state: "unavailable", error: "AI is temporarily unavailable", retryable: true };
    await route.fulfill({ response, json: body });
  });
  await login(page, "pharmacist");
  await page.getByRole("link", { name: "Medication safety" }).click();
  await page.getByTestId("queue-search").fill(other.mrn);
  await page.getByTestId(`queue-item-${other.id}`).click();
  const panel = page.getByTestId("review-panel");
  const unavailable = panel.getByTestId("ai-unavailable");
  await expect(unavailable).toContainText("AI unavailable: AI is temporarily unavailable");
  await expect(unavailable).toContainText("stand on their own");
  await expect(panel.getByTestId("finding").first()).toBeVisible();
  await shot(page, "ai-05-unavailable");
  await unavailable.getByTestId("ai-retry").click();
  await expect(panel.getByTestId("ai-meta")).toContainText("you pressed Ask AI", { timeout: 30_000 });
});

test("a batch states its time before it runs, then the items come back done", async ({ page }) => {
  await login(page, "front_desk");
  await page.getByRole("link", { name: "Requisitions" }).click();
  const bar = page.getByTestId("ai-batch");
  await expect(bar.getByTestId("ai-batch-count")).toContainText("3 requisitions received, AI not run");
  await bar.getByTestId("ai-batch-open").click();
  const confirm = bar.getByTestId("ai-batch-confirm");
  await expect(confirm).toContainText("About 9 model calls, roughly");
  await shot(page, "ai-06-batch-confirm");
  await confirm.getByTestId("ai-batch-run").click();
  await expect(page.getByTestId("ai-batch")).toHaveCount(0, { timeout: 60_000 }); // nothing left without AI
  await expect(page.getByTestId("req-REQ-NEW1").getByTestId("ai-chip")).toHaveCount(0);
  await page.getByTestId("ai-activity-button").click();
  const actions = page.getByTestId("ai-activity-panel").getByTestId("ai-action");
  await expect(actions).toHaveCount(3);
  await expect(actions.first().getByTestId("ai-action-trigger")).toContainText("batch");
  await expect(actions.first()).toContainText("3 calls");
});

test("opening a Control Tower exception writes its narrative while the drawer shows it", async ({ page }) => {
  await slowAi(page, /\/api\/control-tower\/exceptions\/[^/]+\/narrate/);
  await login(page, "operations_manager");
  await page.goto("/control-tower");
  const card = page.getByTestId("exception-card").filter({ has: page.getByTestId("narrative-on-open") }).first();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await card.click();
  const drawer = page.getByTestId("action-drawer");
  await expect(drawer.getByTestId("ai-running")).toContainText("AI is writing the narrative");
  await expect(drawer.getByTestId("facts")).toBeVisible(); // the engine's facts meanwhile
  await expect(drawer.getByTestId("ai-meta")).toContainText("when you opened it", { timeout: 30_000 });
  await expect(drawer.getByText("AI-generated")).toBeVisible();
  await shot(page, "ai-07-exception");
  await page.keyboard.press("Escape");
  // the bed manager may see everyone's actions since the reset (the operations roles)
  await page.getByTestId("ai-activity-button").click();
  const panel = page.getByTestId("ai-activity-panel");
  await panel.getByTestId("ai-scope-all").click();
  await expect(panel.getByTestId("ai-action").first()).toBeVisible();
  const sid = await page.evaluate(() => localStorage.getItem("ioc.demoSession"));
  expect(sid).toBeTruthy();
  await expect(panel).not.toContainText(sid!); // the session id is how a browser finds its actions: never shown
});

test("a portal requisition runs at once and its progress shows where the referrer is looking", async ({ page }) => {
  await login(page, "referrer");
  await page.goto("/portal/new");
  await page.getByLabel("New patient").check();
  await page.getByLabel("First name").fill("Lina");
  await page.getByLabel("Last name").fill("Example");
  await page.getByLabel("Date of birth").fill("1971-05-03");
  await page.getByLabel("Health card number").fill("2345678901");
  await page.getByLabel("Exam requested").fill("MRI lumbar spine");
  await page.getByLabel("Clinical information").fill("Low back pain radiating to the left leg for 8 weeks.");
  await page.getByTestId("submit-requisition").click();
  await expect(page.getByTestId("requisition-sent")).toBeVisible();
  await expect(page.getByTestId("submitted-ai-done")).toContainText("AI read, triaged and protocolled it", { timeout: 30_000 });
  await shot(page, "ai-08-portal");
  await page.getByTestId("ai-activity-button").click();
  await expect(page.getByTestId("ai-activity-panel").getByTestId("ai-action-trigger").first()).toContainText("submitted in the portal");
});

test("a portal requisition still waiting after two minutes: the page stops following and says so", async ({ page }) => {
  // its job lost (a server restart): the requisition stays queued, so every poll brings the same answer
  await page.route("**/api/portal/requisitions", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    body.requisitions = (body.requisitions as { ai?: object }[]).map((r) => (r.ai ? { ...r, ai: { ...r.ai, state: "queued" } } : r));
    await route.fulfill({ response, json: body });
  });
  await login(page, "referrer");
  await page.clock.install();
  await page.goto("/portal/new");
  await page.getByLabel("New patient").check();
  await page.getByLabel("First name").fill("Noor");
  await page.getByLabel("Last name").fill("Example");
  await page.getByLabel("Date of birth").fill("1968-11-21");
  await page.getByLabel("Health card number").fill("3456789012");
  await page.getByLabel("Exam requested").fill("CT head");
  await page.getByLabel("Clinical information").fill("New persistent headaches for 3 weeks.");
  await page.getByTestId("submit-requisition").click();
  await expect(page.getByTestId("submitted-ai")).toBeVisible();
  await page.clock.fastForward("02:01");
  await expect(page.getByTestId("submitted-ai-late")).toContainText("taking longer than usual");
  await expect(page.getByTestId("submitted-ai")).toHaveCount(0);
});
