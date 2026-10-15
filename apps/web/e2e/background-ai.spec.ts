import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Background AI on demand (BACKGROUND_AI=on_demand): after a reset the background writes the rule results of order
// reviews, reconciliations and Control Tower exceptions, and their AI runs when a person opens one, so a deployment
// with a real model key pays for what visitors look at. The API side (no model call for what nobody opened, the
// locks between a person and the background) is apps/api/tests/test_background_ai.py.

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

const NAPROXEN = "MedicationRequest/medrx-ms-82-naproxen";
let review = { id: "", mrn: "" };

test.beforeAll(async ({ request }, testInfo) => {
  testInfo.setTimeout(240_000);
  const health = await (await request.get("/api/health")).json();
  test.skip(health.background?.ai !== "on_demand", "the test API runs BACKGROUND_AI=eager");
  await request.post("/api/demo/reset", { headers: await auth(request, "U-ADMIN") });
  const queue = await (await request.get("/api/med-safety/queue?show=all", { headers: await auth(request, "U-PHAR-01") })).json();
  const naproxen = (queue.items as { id: string; order_ref?: string; mrn?: string; commentary_status: string }[])
    .find((i) => i.order_ref === NAPROXEN)!;
  expect(naproxen.commentary_status).toBe("on_open");
  review = { id: naproxen.id, mrn: naproxen.mrn ?? "" };
});

test("an order review's AI commentary is written when the pharmacist opens it, not before", async ({ page, request }) => {
  const pharmacist = await auth(request, "U-PHAR-01");
  // someone is using the demo and the background loops run, yet they leave the review to whoever opens it
  // (eager, this major interaction would be among the first commentaries written)
  await page.waitForTimeout(6_000);
  const before = await (await request.get(`/api/med-safety/reviews/${review.id}`, { headers: pharmacist })).json();
  expect(before.commentary_status).toBe("on_open");
  expect(before.commentary).toBeNull();

  await login(page, "pharmacist");
  await page.getByRole("link", { name: "Medication safety" }).click();
  await page.getByTestId("queue-search").fill(review.mrn);
  await page.getByTestId(`queue-item-${review.id}`).click();
  const panel = page.getByTestId("review-panel");
  await expect(panel.getByTestId("finding").filter({ hasText: "naproxen + warfarin" })).toBeVisible(); // the rules at once
  await expect(panel.getByTestId("commentary")).toBeVisible({ timeout: 30_000 }); // then the AI, unasked
  await expect(panel.getByTestId("ai-concern").first()).toBeVisible();
  await expect(panel.getByText("AI commentary", { exact: true })).toBeVisible();
  await shot(page, "bg-01-review-opened");
  const after = await (await request.get(`/api/med-safety/reviews/${review.id}`, { headers: pharmacist })).json();
  expect(after.commentary_status).toBe("done");
});

test("a Control Tower exception is narrated when the bed manager opens it", async ({ page }) => {
  await login(page, "operations_manager");
  await page.goto("/control-tower");
  const card = page.getByTestId("exception-card").filter({ has: page.getByTestId("narrative-on-open") }).first();
  await expect(card).toBeVisible({ timeout: 30_000 });
  const id = await card.getAttribute("data-exception");
  await card.click();
  const drawer = page.getByTestId("action-drawer");
  await expect(drawer.getByTestId("narrative")).not.toBeEmpty();
  await expect(drawer.getByText("AI-generated")).toBeVisible();
  await shot(page, "bg-02-exception-opened");
  await page.keyboard.press("Escape");
  const same = page.locator(`[data-exception="${id}"]`);
  await expect(same.getByTestId("narrative-on-open")).toHaveCount(0, { timeout: 15_000 });
});

test("AI Ops says the background AI runs on demand", async ({ page }) => {
  await login(page, "admin");
  await page.getByRole("link", { name: "AI Ops" }).click();
  await expect(page.getByTestId("aiops-background-ai")).toContainText("written when someone opens the item");
});
