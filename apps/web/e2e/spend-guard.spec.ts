import { expect, test, type Page } from "@playwright/test";

// Keeping a deployment with a real model key cheap: the idle pause (IDLE_PAUSE_MINUTES) holds the hospital clock
// while nobody uses the demo, and the daily AI budget (LLM_DAILY_BUDGET_USD) refuses model calls past the cap.
// The test API runs without a cap and with a person active, so the held and capped states rewrite its responses
// in the browser; the API side is covered by apps/api/tests/test_spend_guard.py.

async function login(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test("every API request says how long the person has been idle", async ({ page }) => {
  await login(page, "operations_manager");
  const board = page.waitForRequest((r) => r.url().includes("/api/control-tower/board"));
  await page.getByRole("link", { name: "Control Tower" }).click();
  const idle = (await board).headers()["x-idle-seconds"];
  expect(idle).toMatch(/^\d+$/);
  expect(Number(idle)).toBeLessThan(60);
});

test("the control bar shows the clock on hold while nobody uses the demo", async ({ page }) => {
  await page.route("**/api/control-tower/board", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.clock = { ...body.clock, running: true, rate: 60,
      idle: { ...body.clock.idle, paused: true, pause_minutes: 15 } };
    await route.fulfill({ response, json: body });
  });
  await login(page, "operations_manager");
  await page.getByRole("link", { name: "Control Tower" }).click();
  await expect(page.getByTestId("clock-state")).toContainText("on hold while idle");
});

test("AI Ops shows today's spend against the daily budget, and when it is reached", async ({ page }) => {
  await login(page, "admin");
  await page.getByRole("link", { name: "AI Ops" }).click();
  await expect(page.getByTestId("aiops-budget")).toContainText("No daily AI budget set");

  await page.route("**/api/aiops/overview?*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.budget = { ...body.budget, limit_usd: 5, spent_usd: 5.0123, remaining_usd: 0, reached: true, refused_today: 3 };
    await route.fulfill({ response, json: body });
  });
  await page.reload();
  await expect(page.getByTestId("aiops-budget")).toContainText("$5.01 of $5.00 spent today");
  await expect(page.getByTestId("aiops-budget-reached")).toContainText("new model calls are refused");
  await expect(page.getByTestId("aiops-budget-reached")).toContainText("3 calls refused today");
});
