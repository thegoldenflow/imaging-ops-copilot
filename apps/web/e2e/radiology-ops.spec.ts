import { expect, test, type Page } from "@playwright/test";

// Phase 3 storyline: exams finish, the backlog is balanced and read, a critical
// result escalates and is closed, peer review and QA, CT dose monitoring.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

async function signInAs(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

const count = async (page: Page, testId: string) => Number((await page.getByTestId(testId).textContent())?.replace(/\D/g, ""));

test.beforeAll(async ({ request }) => {
  const { token } = await (await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } })).json();
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${token}` } });
});

test("radiology operations end to end", async ({ page }) => {
  test.setTimeout(180_000);

  // 1. Technologist marks an exam done; it lands in the backlog (and CT exams get a dose record).
  await signInAs(page, "technologist");
  await page.goto("/scheduling?tab=appointments");
  const done = page.locator('[data-testid^="complete-"]').first();
  await expect(done).toBeVisible({ timeout: 20_000 });
  await done.click();
  await expect(page.getByText("completed").first()).toBeVisible();

  // 2. Operations manager balances the backlog.
  await signInAs(page, "operations_manager");
  await page.getByRole("link", { name: "Reading backlog" }).click();
  await expect(page.getByTestId("kpi-unread")).toBeVisible();
  await shot(page, "p3-01-backlog");
  await page.getByRole("tab", { name: /Radiologists/ }).click();
  await expect(page.getByTestId("reader-U-RAD5")).toContainText("Off shift");
  await shot(page, "p3-02-suggestions");
  await page.getByTestId("apply-all-suggestions").click();
  await expect(page.getByTestId("reader-U-RAD5")).toContainText("0 studies queued");

  // 3. Radiologist reads and signs a study from her queue.
  await signInAs(page, "radiologist");
  await page.getByRole("link", { name: "Reading backlog" }).click();
  await expect(page.getByTestId("kpi-unread")).toBeVisible();
  const before = await count(page, "kpi-unread");
  await page.locator('[data-testid^="read-"]').first().click();
  await expect(page.getByTestId("read-panel")).toBeVisible();
  await expect(page.getByLabel("Impression")).not.toHaveValue("");
  await shot(page, "p3-03-dictation");
  await page.getByTestId("sign-dictated").click();
  await expect(page.getByTestId("read-panel")).toHaveCount(0);
  await expect.poll(() => count(page, "kpi-unread")).toBe(before - 1);

  // 4. The seeded critical case is never acknowledged and escalates to the medical director.
  await signInAs(page, "medical_director");
  await page.getByRole("link", { name: "Critical results" }).click();
  await expect.poll(() => count(page, "kpi-escalated"), { timeout: 90_000, intervals: [2000] }).toBeGreaterThan(0);
  await page.locator('[data-testid^="case-CR-"]').filter({ hasText: "Escalated" }).first().click();
  await expect(page.getByTestId("case-timeline")).toContainText("escalated to Dr. Daniel Okafor");
  await expect(page.getByTestId("close-case")).toBeDisabled();
  await shot(page, "p3-04-critical-escalated");
  await page.getByTestId("ack-name").fill("Dr. Michael Campbell");
  await page.getByLabel("Role").fill("Ordering physician");
  await page.getByTestId("ack-submit").click();
  await expect(page.getByTestId("case-timeline")).toContainText("Acknowledged by Dr. Michael Campbell");
  await page.getByLabel("Closing note").fill("Patient sent to emergency");
  await page.getByTestId("close-case").click();
  await expect(page.getByText(/Closed by Dr. Daniel Okafor/)).toBeVisible();

  // 5. Radiologist completes a blinded peer review.
  await signInAs(page, "radiologist");
  await page.getByRole("link", { name: "Peer review" }).click();
  await expect(page.getByText("Original reader hidden")).toBeVisible();
  await page.getByTestId("score-minor").check();
  await page.getByLabel("Discrepancy type").selectOption("clarity");
  await shot(page, "p3-05-peer-review");
  await page.getByTestId("submit-review").click();
  await expect(page.getByText(/^Submitted /)).toBeVisible();

  // 6. QA lead sees the report and exports it.
  await signInAs(page, "medical_director");
  await page.getByRole("link", { name: "Peer review" }).click();
  await expect(page.getByTestId("qa-row-U-RAD2")).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-qa").click()]);
  expect(download.suggestedFilename()).toMatch(/^peer-review-.*\.csv$/);
  await shot(page, "p3-06-qa-report");

  // 7. CT dose: every CT has a record; exceptions and the drifting scanner are visible.
  await page.getByRole("link", { name: "CT dose" }).click();
  await expect(page.getByTestId("dose-coverage")).toHaveText(/^(\d+) \/ \1$/);
  await page.locator('[data-testid^="dose-DOSE-"]').first().click();
  await expect(page.getByTestId("dose-drawer")).toContainText("TID 10011");
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("tab", { name: "Trends" }).click();
  await expect(page.getByText("EVW-CT1").first()).toBeVisible();
  await shot(page, "p3-07-dose-trends");
});

test("QA report is for the QA lead only", async ({ page }) => {
  await signInAs(page, "admin");
  const res = await page.request.get("/api/peer-review/qa", {
    headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem("ioc.token"))}` },
  });
  expect(res.status()).toBe(403);
});
