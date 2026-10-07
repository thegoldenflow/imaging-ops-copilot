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

test("referrer portal: online requisition reaches triage; another doctor's patient is 403 and audited (system 17)", async ({ page }) => {
  await signInAs(page, "referrer");
  await page.getByRole("link", { name: "My patients" }).click();
  await expect(page.getByTestId("portal-patient-PT-DEMO1")).toBeVisible();
  await page.getByRole("link", { name: "New requisition" }).first().click();
  await page.getByLabel("New patient").check();
  await page.getByLabel("First name").fill("Ana");
  await page.getByLabel("Last name").fill("Example");
  await page.getByLabel("Date of birth").fill("1958-02-11");
  await page.getByLabel("Health card number").fill("1234567890");
  await page.getByLabel("Version code").fill("AB");
  await page.getByLabel("Exam requested").fill("CT abdomen and pelvis with contrast");
  await page.getByLabel("Clinical information").fill("Right lower quadrant pain and 6 kg weight loss over 2 months.");
  await page.getByLabel("Relevant history").fill("Type 2 diabetes on metformin");
  await page.getByLabel("Urgent").check();
  await shot(page, "p4-04-portal-form");
  await page.getByTestId("submit-requisition").click();
  await expect(page.getByTestId("requisition-sent")).toBeVisible();
  const reqId = (await page.getByTestId("requisition-sent").textContent())!.match(/REQ-\d+/)![0];
  await page.getByRole("link", { name: "My patients" }).first().click();
  await expect(page.getByTestId(`portal-req-${reqId}`)).toContainText("With the radiologist for review", { timeout: 20_000 });

  // Someone else's patient: refused and audited.
  await page.getByLabel("Patient ID").fill("PT-00001");
  await page.getByRole("button", { name: "Open patient" }).click();
  await expect(page.getByTestId("portal-denied")).toBeVisible();
  await shot(page, "p4-05-portal-denied");

  // Staff see the AI triage and protocol suggestion for the portal requisition.
  await signInAs(page, "radiologist");
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByText("CT abdomen and pelvis with contrast").first()).toBeVisible();
  await expect(page.getByText(/P[12]/).first()).toBeVisible();
  await shot(page, "p4-06-portal-req-staff");

  await signInAs(page, "admin");
  await page.goto("/audit");
  await page.getByRole("tab", { name: "Denied (403)" }).click();
  await expect(page.getByRole("row").filter({ hasText: "PT-00001" }).first()).toContainText("Dr. Helen Park");
});

test("billing QA: every kind of planted discrepancy is listed, worked and exported (system 18)", async ({ page }) => {
  await signInAs(page, "admin");
  await page.getByRole("link", { name: "Billing QA" }).click();
  await expect(page.getByTestId("kpi-open")).toBeVisible();
  for (const kind of ["missing", "duplicate", "code_mismatch", "amount_mismatch", "rejected", "not_performed"]) {
    await expect.poll(async () => Number((await page.getByTestId(`kind-${kind}`).textContent())!.replace(/\D/g, ""))).toBeGreaterThan(0);
  }
  await shot(page, "p4-07-billing");
  const open = Number(await page.getByTestId("kpi-open").textContent());
  await page.getByTestId("kind-duplicate").click();
  await page.locator('[data-testid^="disc-duplicate."]').first().click();
  await expect(page.getByTestId("billing-drawer")).toContainText("claims with");
  await page.getByLabel("Note").fill("Second claim voided with the payer");
  await page.getByTestId("resolve-discrepancy").click();
  await expect(page.getByTestId("kpi-open")).toHaveText(String(open - 1));
  const [file] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-billing").click()]);
  expect(file.suggestedFilename()).toMatch(/billing-discrepancies-.*\.csv/);
});

test("patient feedback: survey on a phone in Chinese, low rating alerts the site manager, AI labels (system 19)", async ({ page, browser }) => {
  await signInAs(page, "operations_manager");
  await page.getByRole("link", { name: "Patient feedback" }).click();
  await expect(page.getByTestId("kpi-alerts")).toBeVisible();
  const alertsBefore = Number(await page.getByTestId("kpi-alerts").textContent());
  await page.getByRole("tab", { name: "Surveys sent" }).click();
  const zh = page.locator('[data-testid^="survey-SRV-"]').filter({ hasText: "中文" }).first();
  const href = await zh.locator('[data-testid^="survey-link-"]').getAttribute("href");

  const phone = await browser.newPage({ viewport: { width: 390, height: 780 } });
  await phone.goto(href!);
  await expect(phone.getByText("您这次就诊感觉如何？")).toBeVisible();
  await phone.getByTestId("star-1").click();
  await phone.getByLabel("意见（可选）").fill("等了一个多小时，前台态度差，也没人解释原因。");
  if (SHOTS) await phone.screenshot({ path: `${SHOTS}/p4-08-survey-phone.png` });
  await phone.getByTestId("send-feedback").click();
  await expect(phone.getByTestId("feedback-done")).toContainText("感谢您的反馈");
  await phone.close();

  await expect(page.getByTestId("kpi-alerts")).toHaveText(String(alertsBefore + 1), { timeout: 10_000 });
  const alert = page.getByTestId("feedback-alerts").locator("li").filter({ hasText: "也没人解释原因" });
  await expect(alert).toContainText("1-star rating");
  await page.getByRole("tab", { name: "Responses" }).click();
  const row = page.locator('[data-testid^="fb-FB-"]').filter({ hasText: "也没人解释原因" });
  await expect(row).toContainText("AI label", { timeout: 15_000 });
  await expect(row).toContainText("Wait time");
  await expect(row).toContainText("AI summary in English");
  await row.getByRole("button", { name: "Confirm labels" }).click();
  await expect(row).toContainText("Confirmed by Jordan Lee");
  await alert.getByLabel("Follow-up note").fill("Called the patient and apologised");
  await alert.getByRole("button", { name: "Record follow-up" }).click();
  await expect(page.getByTestId("kpi-alerts")).toHaveText(String(alertsBefore));
  await page.getByRole("tab", { name: "Trends" }).click();
  await shot(page, "p4-09-feedback");
});
