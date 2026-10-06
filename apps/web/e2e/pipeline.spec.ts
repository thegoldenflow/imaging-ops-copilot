import { expect, test, type Page } from "@playwright/test";

// Phase 2 storyline: requisitions through extraction, triage, protocol,
// MRI safety, prep translations, contrast thresholds and prior imaging.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

async function signInAs(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

async function openRequisition(page: Page, id: string) {
  await page.goto("/requisitions");
  await page.getByTestId(`req-${id}`).click();
  await expect(page.getByTestId("requisition-text")).toBeVisible({ timeout: 20_000 });
}

test.beforeAll(async ({ request }) => {
  const { token } = await (await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } })).json();
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${token}` } });
});

test("requisition pipeline end to end", async ({ page, context }) => {
  test.setTimeout(120_000);

  // 1. Front desk receives a new requisition; the intake worker processes it.
  await signInAs(page, "front_desk");
  await page.goto("/requisitions");
  await expect(page.getByTestId("req-REQ-NEW1")).toContainText("Awaiting radiologist", { timeout: 30_000 });
  await page.getByTestId("new-requisition").click();
  await page.getByTestId("submit-requisition").click();
  await expect(page.getByText("AI processing").or(page.getByText("Awaiting radiologist")).first()).toBeVisible();
  await shot(page, "p2-01-queue");

  // 2. Radiologist reviews the MRI requisition that mentions a cochlear implant.
  await signInAs(page, "radiologist");
  await openRequisition(page, "REQ-NEW1");
  await expect(page.locator("mark").first()).toBeVisible();
  await expect(page.getByText("MRI brain with and without contrast, tumour")).toBeVisible();
  await page.getByRole("button", { name: /Confirm P\d/ }).click();
  await expect(page.getByText(/Confirmed by Dr. Priya Raman/)).toBeVisible();
  await page.getByTestId("approve-protocol").click();
  await expect(page.getByText(/Approved by Dr. Priya Raman/)).toBeVisible();
  await expect(page.getByText("MRI: needs review").first()).toBeVisible();
  await shot(page, "p2-02-requisition-detail");

  // 3. Front desk books it; prep falls back to English until Punjabi is approved.
  await signInAs(page, "front_desk");
  await openRequisition(page, "REQ-NEW1");
  await expect(page.getByText(/translation not approved yet/)).toBeVisible();
  await page.getByTestId("book-next").click();
  await expect(page.getByText(/min from protocol/)).toBeVisible();

  // 4. The patient answers the questionnaire (in Punjabi) from the link.
  const [patient] = await Promise.all([context.waitForEvent("page"), page.getByRole("link", { name: /Open questionnaire as patient/ }).click()]);
  await patient.setViewportSize({ width: 390, height: 844 });
  await expect(patient.getByText("MRI ਸੁਰੱਖਿਆ ਪ੍ਰਸ਼ਨਾਵਲੀ")).toBeVisible();
  for (const q of ["pacemaker", "aneurysm_clip", "neurostimulator", "metal_fragments", "drug_pump", "recent_surgery", "pregnant", "claustrophobic"]) {
    await patient.getByTestId(`${q}-no`).check({ force: true });
  }
  await patient.getByTestId("cochlear_implant-yes").check({ force: true });
  await patient.getByRole("textbox").fill("ਕੋਕਲੀਅਰ ਇਮਪਲਾਂਟ ਸੱਜੇ ਕੰਨ ਵਿੱਚ");
  await shot(patient, "p2-03-mri-questionnaire-mobile");
  await patient.getByRole("button", { name: "ਜਵਾਬ ਭੇਜੋ" }).click();
  await expect(patient.getByText(/ਧੰਨਵਾਦ/)).toBeVisible();

  // 5. Technologist reviews and clears.
  await signInAs(page, "technologist");
  await page.getByRole("link", { name: "MRI safety" }).click();
  const card = page.getByTestId("screening-REQ-NEW1");
  await expect(card).toContainText("Cochlear implant");
  await card.getByLabel("Review note").fill("Implant card checked; magnet removal arranged with audiology");
  await shot(page, "p2-04-mri-review");
  await card.getByRole("button", { name: "Clear for MRI" }).click();
  await expect(card).toContainText("Cleared by Sam Rivera");

  // 6. Medical director approves the Punjabi MRI translation and changes a threshold.
  await signInAs(page, "medical_director");
  await page.getByRole("link", { name: "Prep instructions" }).click();
  await page.getByTestId("prep-mri-pa").getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("prep-mri-pa")).toContainText("approved");
  await page.getByRole("link", { name: "Contrast checks" }).click();
  const reviewStat = page.getByText("Needs review", { exact: true }).locator("..");
  const before = Number(await reviewStat.locator("p").nth(1).textContent());
  await page.getByLabel("eGFR review threshold").fill("85");
  await page.getByTestId("save-contrast-config").click();
  await expect.poll(async () => Number(await reviewStat.locator("p").nth(1).textContent())).toBeGreaterThan(before);
  await shot(page, "p2-05-contrast");

  // 7. CT requisition: approve, book, and the outside prior is retrieved.
  await signInAs(page, "radiologist");
  await openRequisition(page, "REQ-NEW2");
  await expect(page.getByText("Needs premedication").first()).toBeVisible();
  await page.getByTestId("approve-protocol").click();
  await expect(page.getByText(/Approved by Dr. Priya Raman/)).toBeVisible();
  await signInAs(page, "front_desk");
  await openRequisition(page, "REQ-NEW2");
  await page.getByTestId("book-next").click();
  await expect(page.getByTestId("prior-received")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("link", { name: "Prior imaging" }).click();
  await expect(page.getByText("Lakeview Diagnostics").first()).toBeVisible();
  await shot(page, "p2-06-priors");

  // 8. Eval results are visible.
  await signInAs(page, "radiologist");
  await page.getByRole("link", { name: "AI evaluations" }).click();
  await expect(page.getByTestId("eval-triage")).toBeVisible();
  await shot(page, "p2-07-evals");
});
