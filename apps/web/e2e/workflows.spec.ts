import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Durable workflows (spec 6.5). Without Temporal (TEMPORAL_ADDRESS unset) the workflow view says "offline" and the
// modules work. With Temporal and a worker (TEMPORAL_ADDRESS, TEMPORAL_WORKER_IN_API=1 for the API Playwright
// starts): a patient journey with a deliberate MedRec failure that Temporal retries, sign-offs in the review queue,
// a step skipped by the bed manager (audited), the follow-up booking approved by the clerk, and the Control
// Tower's patient card linking to the journey. Each test skips when its mode is not the run's.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function login(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

async function switchTo(page: Page, role: string) {
  await page.getByRole("button", { name: "Switch role" }).click();
  await login(page, role);
}

const tokens = new Map<string, Record<string, string>>();

async function as(request: APIRequestContext, userId: string): Promise<Record<string, string>> {
  if (!tokens.has(userId)) {
    const body = await (await request.post("/api/auth/login", { data: { user_id: userId } })).json();
    tokens.set(userId, { Authorization: `Bearer ${body.token}` });
  }
  return tokens.get(userId)!;
}

interface StepJson { key: string; status: string; attempts: number; waiting_for: { refs: string[] } | null; refs: string[] }

async function step(request: APIRequestContext, workflowId: string, key: string): Promise<StepJson | undefined> {
  const r = await request.get(`/api/workflows/${workflowId}`, { headers: await as(request, "U-OPS") });
  if (!r.ok()) return undefined;
  return ((await r.json()).steps as StepJson[]).find((s) => s.key === key);
}

async function waitStep(request: APIRequestContext, workflowId: string, key: string, status: string, timeout = 60_000) {
  await expect.poll(async () => (await step(request, workflowId, key))?.status, { timeout, intervals: [500] }).toBe(status);
  return (await step(request, workflowId, key))!;
}

async function sign(request: APIRequestContext, ref: string, userId: string) {
  const r = await request.post(`/api/hospital/documents/${ref.split("/")[1]}/sign`, { headers: await as(request, userId) });
  expect(r.ok(), await r.text()).toBeTruthy();
}

/** A Medicine A inpatient with AI-processing and follow-up-call consent (the demo physician's unit). */
async function journeyPatient(request: APIRequestContext) {
  const doc = await as(request, "U-DOC-09");
  const unit = await (await request.get("/api/control-tower/units/MEDA", { headers: doc })).json();
  for (const bed of unit.beds as { id: string; encounter_id: string | null; mrn: string | null }[]) {
    if (!bed.encounter_id || !bed.mrn) continue;
    const chart = await (await request.get(`/api/hospital/patients/${bed.mrn}`, { headers: doc })).json();
    if (chart.consents?.ai_processing === "permit" && chart.consents?.followup_call === "permit") {
      return { encounter: bed.encounter_id, bed: bed.id, mrn: bed.mrn };
    }
  }
  throw new Error("no Medicine A inpatient with both consents");
}

async function temporal(request: APIRequestContext) {
  return (await request.get("/api/workflows/status", { headers: await as(request, "U-OPS") })).json();
}

test.beforeAll(async ({ request }) => {
  tokens.clear();
  await request.post("/api/demo/reset", { headers: await as(request, "U-ADMIN") });
  tokens.clear();
});

test("offline: without Temporal the workflow view says so and the modules keep working", async ({ page, request }) => {
  const status = await temporal(request);
  test.skip(status.configured, "Temporal is configured for this run");
  await login(page, "operations_manager");
  await page.getByRole("link", { name: "Workflows" }).click();
  await expect(page.getByTestId("temporal-offline")).toContainText("Offline");
  await expect(page.getByText("No workflows yet")).toBeVisible();
  await shot(page, "wf-01-offline");
  await page.getByRole("link", { name: "Control Tower" }).click();
  await expect(page.getByTestId("board-beds")).toBeVisible();
  await expect(page.getByTestId("exception-card").first()).toBeVisible();
});

test("journey: a deliberate failure retried, sign-offs, a skip by the bed manager, linked from the Control Tower", async ({ page, request }) => {
  test.setTimeout(240_000);
  const status = await temporal(request);
  test.skip(!status.configured || !status.online || status.workers < 1, "needs Temporal and a worker");
  const ops = await as(request, "U-OPS");
  const patient = await journeyPatient(request);
  expect((await request.post("/api/workflows/faults", { headers: ops, data: { step: "medRecAdmission", times: 1 } })).ok()).toBeTruthy();
  const started = await request.post("/api/workflows/journeys", { headers: ops, data: { encounter_id: patient.encounter, followup_delay_s: 5 } });
  expect(started.status(), await started.text()).toBe(202);
  const wf: string = (await started.json()).workflow_id;

  // the bed manager follows it in the workflow view
  await login(page, "operations_manager");
  await page.getByRole("link", { name: "Workflows" }).click();
  await expect(page.getByTestId("temporal-status")).toContainText("Temporal online");
  await expect(page.locator(`[data-workflow="${wf}"]`)).toBeVisible({ timeout: 30_000 });
  await page.locator(`[data-workflow="${wf}"] a`).first().click();
  await expect(page.locator('[data-step="orderReview"]')).toHaveAttribute("data-step-status", "waiting", { timeout: 30_000 });
  await expect(page.getByTestId("current-stage")).toContainText("Order review");
  await shot(page, "wf-02-waiting-for-pharmacist");

  // the pharmacist confirms the order review in the review queue
  await switchTo(page, "pharmacist");
  await page.getByRole("link", { name: "Review queue" }).click();
  const signoff = page.locator('[data-kind="workflow-signoff"]').filter({ hasText: patient.encounter }).first();
  await expect(signoff).toBeVisible({ timeout: 15_000 });
  await shot(page, "wf-03-review-queue");
  await signoff.getByTestId("approve").click();
  await expect(signoff).toHaveCount(0);

  // MedRec: the injected failure after the draft was written is retried by Temporal; one draft, co-signed
  const medrec = await waitStep(request, wf, "medRecAdmission", "waiting");
  expect(medrec.attempts).toBe(2);
  const draft = medrec.waiting_for!.refs[0];
  await sign(request, draft, "U-PHAR-01");
  await sign(request, draft, "U-DOC-09");

  // the bed manager skips ward monitoring (the patient left on paper in this demo); audited
  await switchTo(page, "operations_manager");
  await page.goto(`/workflows/${wf}`);
  const medrecRow = page.locator('[data-step="medRecAdmission"]');
  await expect(medrecRow).toHaveAttribute("data-step-status", "done", { timeout: 30_000 });
  await expect(medrecRow).toContainText("attempt 2");
  await expect(page.locator('[data-step="wardMonitoring"]')).toHaveAttribute("data-step-status", "waiting", { timeout: 30_000 });
  await shot(page, "wf-04-timeline");
  await page.getByTestId("skip-wardMonitoring").click();
  await page.getByLabel("Reason").fill("discharged on paper (demo)");
  await page.getByTestId("confirm-skip").click();
  await expect(page.locator('[data-step="wardMonitoring"]')).toHaveAttribute("data-step-status", "skipped", { timeout: 30_000 });

  // the discharge documents signed, then the clerk approves the follow-up call's booking
  await sign(request, (await waitStep(request, wf, "draftDischargeSummary", "waiting")).waiting_for!.refs[0], "U-DOC-09");
  await sign(request, (await waitStep(request, wf, "draftPatientInstructions", "waiting")).waiting_for!.refs[0], "U-NURS-05");
  const call = await waitStep(request, wf, "scheduleFollowupCall", "waiting", 60_000);
  const appointment = call.refs.find((r) => r.startsWith("Appointment/"))!.split("/")[1];
  await switchTo(page, "clerk");
  await page.getByRole("link", { name: "Review queue" }).click();
  const approval = page.locator('[data-kind="approval-request"]').filter({ hasText: appointment }).first();
  await expect(approval).toBeVisible({ timeout: 15_000 });
  await approval.getByTestId("approve").click();
  await expect.poll(async () => (await (await request.get(`/api/workflows/${wf}`, { headers: ops })).json()).status, { timeout: 60_000 }).toBe("completed");

  // the Control Tower's patient card links to the journey
  await switchTo(page, "operations_manager");
  await page.goto("/control-tower");
  await page.getByTestId("unit-MEDA").click();
  await page.getByTestId(`bed-${patient.bed}`).click();
  const link = page.getByTestId("patient-card").getByTestId("journey-link");
  await expect(link).toContainText("Patient journey");
  await link.click();
  await expect(page.getByTestId("workflow-id")).toHaveText(wf);
  await expect(page.locator('[data-step="codingStub"]')).toHaveAttribute("data-step-status", "done");
  await shot(page, "wf-05-completed");

  // the skip is in the audit log
  const audit = await (await request.get("/api/admin/audit?module=workflow_engine", { headers: await as(request, "U-ADMIN") })).json();
  const events = (audit.events ?? audit) as { action: string; user_id: string; resource_id: string }[];
  expect(events.some((e) => e.action === "workflow_skip" && e.user_id === "U-OPS" && e.resource_id === wf)).toBeTruthy();
});

test("low-confidence loop: a nurse corrects the AI triage and the agent gains an eval case", async ({ page, request }) => {
  test.setTimeout(120_000);
  const status = await temporal(request);
  test.skip(!status.configured || !status.online || status.workers < 1, "needs Temporal and a worker");
  const ops = await as(request, "U-OPS");
  const patient = await journeyPatient(request);

  await login(page, "nurse");
  await page.goto(`/hospital/patients/${patient.mrn}`);
  await page.getByLabel("Message from the patient or family").fill("I think I left my reading glasses in the room, can someone keep them?");
  await page.getByRole("button", { name: "Triage with AI" }).click();
  await expect(page.getByTestId("triage-result")).toContainText("AI triage");

  await page.getByRole("link", { name: "Review queue" }).click();
  const low = page.locator('[data-kind="ai-review"]').filter({ hasText: "Low AI confidence" }).first();
  await expect(low).toBeVisible({ timeout: 30_000 });
  await low.getByTestId("correct").click();
  const form = page.getByTestId("correction-form");
  await expect(form).toContainText("patient_message_triage answered with confidence 0.50");
  await form.locator('select[data-field="category"]').selectOption("administrative");
  await shot(page, "wf-06-correction");
  await form.getByTestId("save-correction").click();
  await expect(form).toHaveCount(0);

  const reviews = async () => (await (await request.get("/api/workflows?type=LowConfidenceReviewWorkflow", { headers: ops })).json()).workflows as { id: string; status: string }[];
  await expect.poll(async () => (await reviews()).some((w) => w.status === "completed"), { timeout: 60_000 }).toBeTruthy();
  const done = (await reviews()).find((w) => w.status === "completed")!;
  const detail = await (await request.get(`/api/workflows/${done.id}`, { headers: ops })).json();
  const steps = Object.fromEntries((detail.steps as { key: string; detail: string | null }[]).map((s) => [s.key, s.detail ?? ""]));
  expect(steps.writeCorrectedOutput).toContain("category: other -> administrative");
  expect(steps.appendToEvalSet).toContain("source human_review");
  expect(steps.triggerRegression).toMatch(/cases pass/);
});
