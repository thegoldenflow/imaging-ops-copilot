import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Orders and medication safety (spec 7.2): the pharmacist works the queue (blocking first), reads a rule finding and
// the AI commentary with its evidence, handles every discrepancy of an admission reconciliation, signs; a Medicine A
// physician co-signs and the MedRec record becomes final. RBAC: the workbench is the pharmacist's and the physician's,
// a blocking flag is the pharmacist's, test orders are the physician's.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function token(request: APIRequestContext, userId: string): Promise<string> {
  return (await (await request.post("/api/auth/login", { data: { user_id: userId } })).json()).token;
}

interface QueueItem { kind: string; id: string; encounter_id: string | null; unit_id: string | null; order_ref?: string; mrn?: string }

let medrec = { id: "", mrn: "", doc: "" }; // the planted Medicine A admission reconciliation (quetiapine without indication)
let allergyReview = ""; // the planted blocking allergy on Medicine A
let interaction = { id: "", mrn: "" }; // the 8.2 case: naproxen added to warfarin

async function login(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test.beforeAll(async ({ request }, testInfo) => {
  testInfo.setTimeout(240_000);
  const admin = await token(request, "U-ADMIN");
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${admin}` } });
  const pharmacist = await token(request, "U-PHAR-01");
  const auth = { Authorization: `Bearer ${pharmacist}` };
  const queue = await (await request.get("/api/med-safety/queue?show=all", { headers: auth })).json();
  const items = queue.items as QueueItem[];
  allergyReview = items.find((i) => i.order_ref === "MedicationRequest/medrx-ms-allergy-piperacillin")!.id;
  const naproxen = items.find((i) => i.order_ref === "MedicationRequest/medrx-ms-82-naproxen")!;
  interaction = { id: naproxen.id, mrn: naproxen.mrn ?? "" };
  for (const item of items.filter((i) => i.kind === "medrec" && i.unit_id === "MEDA")) {
    const session = await (await request.get(`/api/med-safety/medrec/${item.id}`, { headers: auth })).json();
    if (session.kind === "admission" && session.items.some((x: { id: string }) => x.id === "new-51272")) {
      // its draft record is written by its AI step: on demand, when someone opens it (as the web app asks here)
      const opened = await (await request.post(`/api/med-safety/medrec/${item.id}/ai?trigger=opened`, { headers: auth })).json();
      medrec = { id: item.id, mrn: session.patient.mrn, doc: opened.document_id };
      break;
    }
  }
  expect(medrec.id, "the planted Medicine A admission reconciliation").not.toBe("");
});

test("the patient chart offers no Sign for a MedRec record: it links to the workbench", async ({ page, request }) => {
  // the generic signing path refuses a MedRec record (WP6: only the workbench records every decision)
  const generic = await request.post(`/api/hospital/documents/${medrec.doc}/sign`, {
    headers: { Authorization: `Bearer ${await token(request, "U-PHAR-01")}` } });
  expect(generic.status()).toBe(403);
  expect((await generic.json()).detail).toContain("signed in Medication safety");

  await login(page, "pharmacist");
  await page.goto(`/hospital/patients/${medrec.mrn}`);
  const row = page.getByTestId(`doc-${medrec.doc}`);
  await expect(row).toContainText("Medication reconciliation record");
  await expect(row).toContainText("Signed in Medication safety");
  await expect(row.getByRole("button", { name: /sign/i })).toHaveCount(0);
  await row.getByTestId("sign-in-med-safety").click();
  await expect(page).toHaveURL(/\/med-safety\?encounter=/);
  const panel = page.getByTestId("medrec-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("doc-status")).toContainText("preliminary");
  await expect(panel.getByTestId("sign-button")).toBeDisabled(); // every discrepancy first
  await shot(page, "ms-00-chart-link");

  // the physician (a co-signer) gets the link too, never a Sign button
  await login(page, "physician");
  await page.goto(`/hospital/patients/${medrec.mrn}`);
  await expect(page.getByTestId(`doc-${medrec.doc}`).getByTestId("sign-in-med-safety")).toBeVisible();
  await expect(page.getByTestId(`doc-${medrec.doc}`).getByRole("button", { name: /sign/i })).toHaveCount(0);
});

test("pharmacist: queue, rule and AI flags, every item handled, sign; physician co-signs", async ({ page }) => {
  await login(page, "pharmacist");
  await page.getByRole("link", { name: "Medication safety" }).click();
  const queue = page.getByTestId("ms-queue");
  await expect(queue.locator("[data-testid^=queue-item-]").first()).toBeVisible();
  // blocking flags first
  await expect(queue.locator("[data-testid^=queue-item-]").first().getByTestId("severity")).toHaveText("Blocking");
  await expect(queue.locator("[aria-label='Test order']")).toHaveCount(0); // tests are the physician's

  // the 8.2 interaction: a rule finding with its rule name, evidence, and the AI commentary with its confidence
  await page.getByTestId("queue-search").fill(interaction.mrn);
  await page.getByTestId(`queue-item-${interaction.id}`).click();
  const review = page.getByTestId("review-panel");
  // not necessarily the first finding: a hip-fracture patient may also be on enoxaparin (another major interaction)
  const warfarin = review.getByTestId("finding").filter({ hasText: "Interaction (major): naproxen + warfarin" });
  await expect(warfarin).toBeVisible();
  await expect(warfarin.getByTestId("source-rule")).toContainText("Rule: Drug interaction");
  // opening the review asks the AI for its commentary (BACKGROUND_AI=on_demand); the panel shows it running, then the
  // result with when it was generated (or follows the eager background's run)
  await expect(review.getByTestId("ai-meta")).toContainText("generated", { timeout: 30_000 });
  await expect(review.getByTestId("ai-concern").first()).toBeVisible({ timeout: 30_000 });
  await expect(review.getByTestId("source-ai").first()).toContainText("confidence");
  await review.getByTestId("ai-concern").first().getByTestId("view-evidence").click();
  await expect(page.getByTestId("evidence-entry").first()).toBeVisible();
  await page.getByRole("dialog", { name: "Evidence" }).getByRole("button", { name: "Close" }).click();
  await expect(page.getByTestId("medrec-panel")).toContainText("Omission"); // the antihypertensive missed at admission
  await shot(page, "ms-01-interaction");

  // the Medicine A admission reconciliation: every discrepancy needs an action and a reason
  await page.getByTestId("queue-search").fill(medrec.mrn);
  await page.getByTestId(`queue-item-${medrec.id}`).click();
  const panel = page.getByTestId("medrec-panel");
  await expect(panel).toBeVisible();
  const sign = panel.getByTestId("sign-button");
  await expect(sign).toBeDisabled();
  const quetiapine = panel.getByTestId("medrec-row-new-51272");
  await expect(quetiapine).toContainText("New without indication");
  await expect(quetiapine.getByTestId("source-ai")).toContainText("confidence");
  await quetiapine.getByTestId("item-action").selectOption("modify");
  await quetiapine.getByTestId("item-change").fill("Stop quetiapine: no diagnosis explains it");
  await quetiapine.getByTestId("item-reason").fill("No psychiatric or dementia diagnosis on the list");
  await quetiapine.getByTestId("item-save").click();
  await expect(quetiapine.getByTestId("item-handled")).toContainText("modify");
  for (let left = await panel.getByTestId("item-form").count(); left > 0; left--) {
    const form = panel.getByTestId("item-form").first();
    await form.getByTestId("item-reason").fill("Reviewed with the team: intended");
    await form.getByTestId("item-save").click();
    await expect(panel.getByTestId("item-form")).toHaveCount(left - 1);
  }
  await expect(panel.getByTestId("item-form")).toHaveCount(0);
  await expect(sign).toBeEnabled();
  await shot(page, "ms-02-handled");
  await sign.click();
  await expect(panel.getByTestId("medrec-status")).toContainText("Signed by the pharmacist");
  await expect(panel.getByTestId("doc-status")).toContainText("preliminary");

  // the physician co-signs: the MedRec record becomes final, authenticated by the pharmacist
  await login(page, "physician");
  await page.goto("/med-safety");
  await page.getByTestId("queue-search").fill(medrec.mrn);
  await page.getByTestId(`queue-item-${medrec.id}`).click();
  const cosign = page.getByTestId("medrec-panel");
  await expect(cosign.getByTestId("sign-button")).toHaveText(/Co-sign as physician/);
  await cosign.getByTestId("sign-button").click();
  await expect(cosign.getByTestId("medrec-status")).toContainText("the record is final");
  await expect(cosign.getByTestId("doc-status")).toContainText("final");
  await expect(cosign.getByTestId("doc-status")).toContainText("authenticated by Rosa Pemberton");
  await shot(page, "ms-03-final");
});

test("RBAC: who may open the workbench and decide what", async ({ page, request }) => {
  // the nurse has no workbench (nav, page and API)
  await login(page, "nurse");
  await expect(page.getByRole("link", { name: "Medication safety" })).toHaveCount(0);
  await page.goto("/med-safety");
  await expect(page.getByText("Not available for your role")).toBeVisible();
  const nurse = await token(request, "U-NURS-05");
  expect((await request.get("/api/med-safety/queue", { headers: { Authorization: `Bearer ${nurse}` } })).status()).toBe(403);
  const clerk = await token(request, "U-CLER-01");
  expect((await request.get("/api/med-safety/queue", { headers: { Authorization: `Bearer ${clerk}` } })).status()).toBe(403);

  // the physician sees the blocking allergy on their unit but cannot clear it: a pharmacist must
  await login(page, "physician");
  await page.goto("/med-safety");
  await page.getByTestId("queue-search").fill("piperacillin");
  await page.getByTestId(`queue-item-${allergyReview}`).click();
  const review = page.getByTestId("review-panel");
  await expect(review.getByTestId("finding").first()).toContainText("Allergy");
  await expect(review.getByTestId("why-not")).toContainText("must be handled by a pharmacist");
  await expect(review.getByTestId("accept-order")).toBeDisabled();
  await expect(review.getByTestId("request-change")).toBeDisabled();

  // the physician's queue is their units' only; test orders are theirs
  const doc = await token(request, "U-DOC-09");
  const mine = await (await request.get("/api/med-safety/queue?show=all", { headers: { Authorization: `Bearer ${doc}` } })).json();
  expect(mine.items.length).toBeGreaterThan(0);
  expect(mine.items.every((i: QueueItem) => i.unit_id === "MEDA")).toBe(true);
  expect(mine.items.some((i: QueueItem) => i.order_ref === "ServiceRequest/ord-ms-duplicate")).toBe(true);

  // the pharmacist clears it, with a reason
  await login(page, "pharmacist");
  await page.goto("/med-safety");
  await page.getByTestId("queue-search").fill("piperacillin");
  await page.getByTestId(`queue-item-${allergyReview}`).click();
  const pharm = page.getByTestId("review-panel");
  await expect(pharm.getByTestId("request-change")).toBeDisabled(); // no reason yet
  await pharm.getByTestId("decision-reason").fill("Anaphylaxis to penicillin: choose a non-beta-lactam");
  await pharm.getByTestId("request-change").click();
  await expect(pharm.getByTestId("decision")).toContainText("Change requested from the prescriber");
});
