import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// WP7 (spec 7.3): the physician's discharge summary review with the validator's block, the nurse's bilingual
// patient instructions, and the SBAR handoff confirmation. The planted cases come from the generator
// (app/ehr/seed/documentation.py); their discharge medication reconciliations are signed first through the
// pharmacist workbench's API, as the pharmacist and a physician would.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;
async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function token(request: APIRequestContext, userId: string): Promise<string> {
  return (await (await request.post("/api/auth/login", { data: { user_id: userId } })).json()).token;
}

async function login(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

interface Cases { warfarin_zh: string; hallucination: string; discharged: string | null }
let cases: Cases;

async function signDischargeMedrec(request: APIRequestContext, encounter: string) {
  const pharmacist = { Authorization: `Bearer ${await token(request, "U-PHAR-01")}` };
  const physician = { Authorization: `Bearer ${await token(request, "U-DOC-09")}` };
  // the queue runs the medication-safety intake first (it opens the discharge reconciliations after the reset), so the
  // reconciliation fetched below is the one the intake keeps (one per encounter and kind)
  await request.get("/api/med-safety/queue?show=all", { headers: pharmacist });
  let session: { id?: string; status?: string; items: unknown[] } = { items: [] };
  for (let round = 0; round < 8 && session.status !== "pharmacist_signed"; round++) {
    const r = await request.post(`/api/med-safety/encounters/${encounter}/medrec`, { headers: pharmacist, data: { kind: "discharge" } });
    if (!r.ok()) {
      await new Promise((done) => setTimeout(done, 1000));
      continue;
    }
    session = await r.json();
    if (session.status !== "open") break;
    for (const item of session.items as { id: string; discrepancy: boolean; handled: boolean; indication?: string }[]) {
      if ((item.discrepancy && !item.handled) || item.indication === "pending") {
        await request.post(`/api/med-safety/medrec/${session.id}/items/${item.id}`, {
          headers: pharmacist, data: { action: "accept", reason: "Intended by the team (e2e)" },
        });
      }
    }
    const sign = await request.post(`/api/med-safety/medrec/${session.id}/sign`, { headers: pharmacist });
    if (sign.ok()) session = await sign.json();
  }
  expect(session.status, `the pharmacist's signature on the discharge reconciliation of ${encounter}`).toBe("pharmacist_signed");
  const cosigned = await request.post(`/api/med-safety/medrec/${session.id}/sign`, { headers: physician });
  expect(cosigned.ok(), `co-signature of ${session.id}: ${await cosigned.text()}`).toBeTruthy();
}

test.beforeAll(async ({ request }, testInfo) => {
  testInfo.setTimeout(240_000);
  const admin = await token(request, "U-ADMIN");
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${admin}` } });
  const doctor = { Authorization: `Bearer ${await token(request, "U-DOC-09")}` };
  cases = await (await request.get("/api/documentation/demo", { headers: doctor })).json();
  expect(cases.warfarin_zh, "the planted Chinese-speaking warfarin patient").toBeTruthy();
  expect(cases.hallucination, "the planted tempting note").toBeTruthy();
  await signDischargeMedrec(request, cases.warfarin_zh);
  await signDischargeMedrec(request, cases.hallucination);
});

test("physician: the validator blocks an untraceable medicine until it is removed, then the summary is signed", async ({ page, request }) => {
  test.setTimeout(120_000);
  // without the signed discharge medication table nothing is drafted (an unprepared stay)
  const doctor = { Authorization: `Bearer ${await token(request, "U-DOC-09")}` };
  const list = (await (await request.get("/api/documentation/worklist", { headers: doctor })).json()).items as { encounter_id: string; discharge_table: string; status: string }[];
  const unprepared = list.find((i) => i.discharge_table === "none" && i.status === "in-progress");
  if (unprepared) {
    const r = await (await request.post(`/api/documentation/encounters/${unprepared.encounter_id}/discharge-summary`, { headers: doctor, data: {} })).json();
    expect(r.status).toBe("insufficient");
    expect(r.missing.join(" ")).toContain("discharge medication table");
  }

  await login(page, "physician");
  await page.getByRole("link", { name: "Documentation" }).click();
  await expect(page.getByTestId("doc-worklist")).toBeVisible();
  await page.getByTestId(`work-${cases.hallucination}`).click();
  await page.getByTestId("draft-summary").click();
  const review = page.getByTestId("summary-review");
  await expect(review).toBeVisible({ timeout: 30_000 });
  await expect(review.getByText("AI draft")).toBeVisible();
  await expect(page.getByTestId("validation-bar")).toContainText("signing is blocked");
  const redItem = review.locator('[data-testid="summary-item"][data-status="red"]').first();
  await expect(redItem).toBeVisible();
  await expect(redItem.getByTestId("issue-red").first()).toBeVisible();
  await expect(page.getByTestId("sign-summary")).toBeDisabled();
  await shot(page, "doc-summary-blocked");

  // clicking a sentence highlights its sources
  const course = review.locator('[data-testid="summary-item"][data-path="hospital_course[0]"]');
  await course.click();
  await expect(page.locator('[data-testid="source"][data-highlighted="true"]').first()).toBeVisible();

  // the physician removes the untraceable item, saves (re-validated) and signs
  await page.getByTestId("edit-summary").click();
  await redItem.getByTestId("remove-item").click();
  await page.getByTestId("save-summary").click();
  await expect(page.getByTestId("validation-bar")).toContainText("Every item traces to its sources");
  await expect(page.getByTestId("sign-summary")).toBeEnabled();
  await page.getByTestId("sign-summary").click();
  await expect(page.getByTestId("signed")).toContainText("Signed by");
  await expect(page.getByTestId("raw-issues")).toBeVisible(); // the AI draft's untraceable item stays on record
  await shot(page, "doc-summary-signed");
});

test("nurse: bilingual instructions from the signed summary, the same items in three languages, signed and delivered", async ({ page, request }) => {
  test.setTimeout(120_000);
  const doctor = { Authorization: `Bearer ${await token(request, "U-DOC-09")}` };
  const draft = await (await request.post(`/api/documentation/encounters/${cases.warfarin_zh}/discharge-summary`, { headers: doctor, data: {} })).json();
  expect(draft.blocking, "the warfarin case's draft traces").toBeFalsy();
  const signed = await request.post(`/api/documentation/documents/${draft.id}/sign`, { headers: doctor });
  expect(signed.ok()).toBeTruthy();

  await login(page, "nurse");
  await page.getByRole("link", { name: "Documentation" }).click();
  await page.getByTestId(`work-${cases.warfarin_zh}`).click();
  // drafted in the background once the summary is final, or right now
  const start = page.getByTestId("draft-instructions");
  if (await start.isVisible().catch(() => false)) await start.click();
  const review = page.getByTestId("instructions-review");
  await expect(review).toBeVisible({ timeout: 30_000 });
  await expect(review.getByText("AI draft")).toBeVisible();

  // the patient's language (Chinese) is shown first; switching changes the words, never the items
  const rendered = page.getByTestId("rendered");
  await expect(rendered).toHaveAttribute("lang", /zh-Han[st]/);
  const zhItems = await rendered.getByTestId("fact").count();
  await expect(rendered).toContainText(/华法林|華法林/);
  await page.getByRole("tab", { name: "English" }).click();
  await expect(rendered).toHaveAttribute("lang", "en");
  await expect(rendered).toContainText("warfarin");
  await expect(rendered.getByTestId("fact")).toHaveCount(zhItems);
  await page.getByRole("tab", { name: "繁體中文" }).click();
  await expect(rendered).toContainText("華法林");
  await expect(rendered.getByTestId("fact")).toHaveCount(zhItems);
  await page.getByRole("tab", { name: "简体中文" }).click();
  await expect(rendered).toContainText("华法林");
  await expect(rendered).toContainText("绿叶蔬菜"); // warfarin food advice
  await expect(page.getByTestId("readability")).toContainText("No medical abbreviations");
  await expect(page.getByTestId("fk-grade")).toContainText("Grade");
  await shot(page, "doc-instructions");

  await page.getByTestId("sign-instructions").click();
  await expect(page.getByTestId("signed")).toContainText("Signed by");
  await page.getByTestId("deliver-portal").click();
  await expect(page.getByTestId("delivered")).toContainText("portal");
  await page.getByTestId("portal-link").click();
  await expect(page.getByTestId("portal-page")).toBeVisible();
  await expect(page.getByTestId("portal-rendered")).toContainText(/华法林|華法林/);
});

test("nurse: the SBAR handoff of the shift, confirmed (final with one signature)", async ({ page }) => {
  await login(page, "nurse");
  await page.getByRole("link", { name: "Documentation" }).click();
  await page.getByRole("tab", { name: "Shift handoff" }).click();
  await page.getByTestId(`work-${cases.warfarin_zh}`).click();
  await page.getByTestId("draft-handoff").click();
  const review = page.getByTestId("handoff-review");
  await expect(review).toBeVisible({ timeout: 30_000 });
  for (const section of ["situation", "background", "assessment", "recommendation"]) {
    await expect(page.getByTestId(`sbar-${section}`).getByTestId("sbar-item").first()).toBeVisible();
  }
  await expect(page.getByTestId("sbar-assessment")).toContainText("not recorded");
  await page.getByTestId("sbar-situation").getByTestId("sbar-item").click();
  await expect(page.locator('[data-testid="source"][data-highlighted="true"]').first()).toBeVisible();
  await page.getByTestId("confirm-handoff").click();
  await expect(page.getByTestId("confirmed")).toContainText("Confirmed by");
  await shot(page, "doc-handoff");
});
