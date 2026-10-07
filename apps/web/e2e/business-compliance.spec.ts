import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Phase 4: business and compliance (systems 15-21). One test per system, run in order
// against a freshly reset demo.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

async function signInAs(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

async function tokenFor(request: APIRequestContext, userId: string) {
  const { token } = await (await request.post("/api/auth/login", { data: { user_id: userId } })).json();
  return { Authorization: `Bearer ${token}` };
}

test.beforeAll(async ({ request }) => {
  await request.post("/api/demo/reset", { headers: await tokenFor(request, "U-ADMIN") });
});

test("inventory: a contrast CT deducts stock and raises a low-stock alert (system 15)", async ({ page, request }) => {
  // Robert Taylor's contrast CT at Lakeshore (storyline appointment).
  const day = await demoDay(request);

  await signInAs(page, "technologist");
  await page.getByRole("link", { name: "Inventory" }).click();
  const qty = page.getByTestId("qty-INV-LKS-IOHEXOL-350");
  await expect(qty).toBeVisible();
  const before = Number(await qty.textContent());
  await expect(page.getByTestId("alert-low_stock-INV-LKS-IOHEXOL-350")).toHaveCount(0);
  await shot(page, "p4-01-inventory");

  await page.goto("/scheduling?tab=appointments");
  await page.getByLabel("Day").fill(day);
  await page.getByTestId("complete-AP-DEMO2").click();
  await expect(page.getByTestId("appt-AP-DEMO2")).toContainText("completed");

  await page.getByRole("link", { name: "Inventory" }).click();
  await expect(qty).toHaveText(String(before - 1));
  await expect(page.getByTestId("alert-low_stock-INV-LKS-IOHEXOL-350")).toContainText("drafted");
  await expect(page.getByTestId("inventory-alerts")).toContainText("Expiring soon");
  await page.getByTestId("item-INV-LKS-IOHEXOL-350").click();
  await expect(page.getByTestId("drawer-quantity")).toHaveText(String(before - 1));
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("tab", { name: "Movements" }).click();
  await expect(page.locator('[data-testid^="move-"]').filter({ hasText: "Exam AP-DEMO2" }).first()).toBeVisible();
  await shot(page, "p4-02-inventory-low");

  // Operations submits the drafted order.
  await signInAs(page, "operations_manager");
  await page.goto("/inventory");
  await page.getByRole("tab", { name: /Purchase orders/ }).click();
  const draft = page.locator('[data-testid^="submit-PO-"]').first();
  await draft.click();
  await expect(page.locator('[data-testid^="receive-PO-"]').first()).toBeVisible();
});

async function demoDay(request: APIRequestContext) {
  // AP-DEMO2 is on the next day Lakeshore is open.
  const headers = await tokenFor(request, "U-OPS");
  for (let i = 1; i <= 3; i++) {
    const d = new Date();
    d.setDate(d.getDate() + i);
    const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const { appointments } = await (await request.get(`/api/scheduling/appointments?site_id=LKS&day=${day}`, { headers })).json();
    if (appointments.some((a: { id: string }) => a.id === "AP-DEMO2")) return day;
  }
  throw new Error("AP-DEMO2 not found");
}

test("referral analytics: filters, visit list and an AI summary whose numbers match the tiles (system 16)", async ({ page }) => {
  await signInAs(page, "operations_manager");
  await page.getByRole("link", { name: "Referral analytics" }).click();
  await expect(page.getByTestId("kpi-last-week")).toBeVisible();
  const all = Number(await page.getByTestId("kpi-avg").textContent());
  await page.getByLabel("Modality", { exact: true }).selectOption("CT");
  await expect.poll(async () => Number(await page.getByTestId("kpi-avg").textContent())).toBeLessThan(all);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByTestId("visit-list").locator('[data-testid^="visit-R-"]').first()).toBeVisible();

  await page.getByTestId("generate-summary").click();
  const summary = page.getByTestId("weekly-summary");
  await expect(summary).toBeVisible();
  await expect(page.getByText("AI draft", { exact: true })).toBeVisible();
  // Every filled-in value is shown on the dashboard tile it names.
  const facts = summary.locator("[data-tile]");
  const n = await facts.count();
  expect(n).toBeGreaterThan(5);
  for (let i = 0; i < n; i++) {
    const fact = facts.nth(i);
    const tile = await fact.getAttribute("data-tile");
    const value = (await fact.textContent())!.trim();
    await expect(page.getByTestId(tile!).first()).toContainText(value);
  }
  await facts.first().click();
  await shot(page, "p4-03-referrals");
  await page.getByTestId("approve-summary").click();
  await expect(page.getByText(/Approved by Jordan Lee/)).toBeVisible();
  await page.locator('[data-testid^="plan-R-"]').first().click();
  await expect(page.getByTestId("visit-list")).toContainText("planned by Jordan Lee");
});
