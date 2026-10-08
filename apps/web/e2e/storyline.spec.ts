import { expect, test, type Page } from "@playwright/test";

// Walks the demo storyline end to end across roles. Requires the API and
// Vite dev server (started by playwright.config.ts if not already running).

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function signInAs(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test.beforeAll(async ({ request }) => {
  const login = await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } });
  const { token } = await login.json();
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${token}` } });
});

test("login page lists every role and role switch works", async ({ page }) => {
  await page.goto("/login");
  for (const role of ["front_desk", "radiologist", "operations_manager", "referrer", "admin"]) {
    await expect(page.getByTestId(`login-${role}`)).toBeVisible();
  }
  await shot(page, "01-login");
  await page.getByTestId("login-front_desk").click();
  await expect(page.getByTestId("current-user")).toHaveText("Alex Morgan");
  await page.getByRole("button", { name: "Switch role" }).click();
  await page.getByTestId("login-radiologist").click();
  await expect(page.getByTestId("current-user")).toHaveText("Dr. Priya Raman");
  await shot(page, "02-home");
});

test("storyline: report, phone cancellation, backfill, pre-registration, audit", async ({ page, context }) => {
  // 1. Radiologist drafts and signs Mei Chen's chest X-ray.
  await signInAs(page, "radiologist");
  await page.getByRole("link", { name: "Reading room" }).click();
  await page.getByTestId("study-ST-DEMO1").getByRole("button", { name: "Draft with AI" }).click();
  await expect(page.getByText("AI-generated preliminary draft")).toBeVisible();
  await expect(page.getByText("Possible right upper lobe pulmonary nodule")).toBeVisible();
  await shot(page, "03-report-draft");
  for (const label of ["Lungs", "Pleura", "Heart and mediastinum", "Bones", "Lines and tubes", "Impression"]) {
    await page.getByRole("button", { name: `Accept ${label}` }).click();
    await expect(page.getByRole("button", { name: `Accept ${label}` })).toHaveCount(0);
  }
  await page.getByLabel("Confirm Possible right upper lobe pulmonary nodule").check();
  await page.getByRole("button", { name: "Sign report" }).click();
  await expect(page.getByText(/Signed by Dr. Priya Raman/)).toBeVisible();
  await expect(page.getByText(/Critical result case opened/)).toBeVisible();
  await shot(page, "04-report-signed");

  // 2. Referring physician sees the signed report.
  await page.getByRole("button", { name: "Switch role" }).click();
  await page.getByTestId("login-referrer").click();
  await page.getByRole("link", { name: "My reports" }).click();
  await expect(page.getByText("Mei Chen · X-ray Chest")).toBeVisible();
  // ... and acknowledges the critical result in the portal (System 12).
  await page.getByRole("button", { name: "I have received this result" }).click();
  await expect(page.getByTestId(/^my-critical-/).first()).toContainText("Acknowledged");

  // 3. Robert Taylor calls to cancel his CT.
  await page.getByRole("button", { name: "Switch role" }).click();
  await page.getByTestId("login-front_desk").click();
  await page.getByRole("link", { name: "Front desk" }).click();
  await page.getByTestId("start-call").click();
  const say = async (text: string, expectReply: RegExp) => {
    await page.getByLabel("Caller says").fill(text);
    await page.getByLabel("Caller says").press("Enter");
    await expect(page.getByTestId("transcript")).toContainText(expectReply);
  };
  await say("Hi, this is Robert Taylor, born April 12, 1968.", /you're verified/);
  await say("I need to cancel my appointment.", /you'd like to cancel/);
  await say("Yes, please.", /Your appointment is cancelled/);
  await shot(page, "05-phone-agent");
  await page.getByRole("button", { name: "End call" }).click();

  // 4. Operations manager backfills the slot with Mei Chen.
  await page.getByRole("button", { name: "Switch role" }).click();
  await page.getByTestId("login-operations_manager").click();
  await expect(page.getByTestId("current-user")).toHaveText("Jordan Lee"); // token stored before navigating
  await page.goto("/scheduling?tab=backfill");
  const mei = page.getByTestId("candidate-PT-DEMO1");
  await expect(mei).toBeVisible();
  await expect(page.locator("tbody tr").first()).toHaveAttribute("data-testid", "candidate-PT-DEMO1");
  await shot(page, "06-backfill");
  await page.getByRole("button", { name: /Send offer/ }).click();
  await mei.getByRole("button", { name: "Simulate “YES”" }).click();
  await expect(page.getByText(/Filled · AP-/)).toBeVisible();
  await page.goto("/scheduling");
  await expect(page.getByText("Utilization by site")).toBeVisible();
  await shot(page, "07-scheduling-overview");

  // 5. Messages went out in Chinese; the patient completes pre-registration.
  await page.goto("/front-desk?tab=outbox");
  const confirmation = page.getByTestId("msg-booking_confirmation").first();
  await expect(confirmation).toContainText("预约成功");
  await shot(page, "08-outbox");
  const [patientPage] = await Promise.all([context.waitForEvent("page"), confirmation.getByRole("link").click()]);
  await expect(patientPage.getByText("您好, Mei")).toBeVisible();
  await patientPage.setViewportSize({ width: 390, height: 844 });
  await patientPage.getByLabel("电话").fill("+1-416-555-0168");
  await patientPage.getByLabel("电子邮箱").fill("mei.chen@example.com");
  await patientPage.getByLabel("地址").fill("88 Example St, Demo City, ON");
  await patientPage.getByLabel("健康卡号码").fill("4827103956");
  await patientPage.getByLabel("版本码").fill("MC");
  await patientPage.getByRole("checkbox").check();
  await shot(patientPage, "09-prereg-mobile");
  await patientPage.getByRole("button", { name: "提交" }).click();
  await expect(patientPage.getByText("谢谢，您的预登记已完成。")).toBeVisible();

  // 6. Admin: denied access is audited and the chain verifies.
  await page.getByRole("button", { name: "Switch role" }).click();
  await page.getByTestId("login-admin").click();
  await page.getByRole("link", { name: "Audit log" }).click();
  await expect(page.getByText(/Chain intact/)).toBeVisible();
  await shot(page, "10-audit");
  await page.getByRole("link", { name: "AI usage" }).click();
  await expect(page.getByRole("cell", { name: "cxr_draft", exact: true }).first()).toBeVisible();
});

test("drafts are hidden from referrers (403)", async ({ page, request }) => {
  const rad = await (await request.post("/api/auth/login", { data: { user_id: "U-RAD" } })).json();
  const report = await (await request.post("/api/reports/studies/ST-00001/draft", { headers: { Authorization: `Bearer ${rad.token}` } })).json();
  const ref = await (await request.post("/api/auth/login", { data: { user_id: "U-REF" } })).json();
  const res = await request.get(`/api/reports/${report.id}`, { headers: { Authorization: `Bearer ${ref.token}` } });
  expect(res.status()).toBe(403);
  await signInAs(page, "referrer");
  await page.goto("/reading");
  await expect(page.getByText("Not available for your role")).toBeVisible();
});
