import { expect as baseExpect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

// WP9 (spec 8.2): one synthetic patient through the whole hospital, walked on the guided storyline page. Each step
// switches to the person who plays it with the page's one-click switch, does what the presenter does in that
// module's own screens, and comes back to the page, which shows the step done with the three parts of its trail:
// AI proposed, a person signed, the audit events. Steps 4, 8 and 12 are the 8.1 roadmap stubs (Roadmap badge).
//
// The same clicks run offline (the API without Temporal) and with Temporal: set STORY_TEMPORAL=1 when the API runs
// with a Temporal server and worker (the test then also checks her InpatientJourneyWorkflow on the page); a short
// WORKFLOW_FOLLOWUP_DELAY_S keeps the journey's follow-up timer within the test.

// Every module's background work runs between the clicks (intakes, the Control Tower's refresh, the clock's
// fast-forwards): assertions wait up to 60 s here.
const expect = baseExpect.configure({ timeout: 60_000 });
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const TEMPORAL = process.env.STORY_TEMPORAL === "1";
const STAY = "stay-j0001";
const MRN = "40720001";

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

interface Cast { id: string; name: string; role: string }
interface Step {
  n: number; key: string; status: string; done: boolean; route: string; cast: Cast; permission: string; stub: boolean;
  proposal: { ref: string; label: string } | null; signed: { by?: string } | null; detail: string | null;
  audit: { event_type: string }[];
  step_ai?: { items: { kind: string; id: string; ai: { state: string } }[] };
}
interface View {
  current: number | null; steps: Step[]; job: { status: string } | null;
  patient: { call_lines: string[]; followup_lines: string[] } | null;
  temporal: { configured: boolean; status: { online?: boolean } }; journey: { id: string; status: string } | null;
}

const tokens: Record<string, string> = {};
async function auth(request: APIRequestContext, userId: string) {
  if (!tokens[userId]) tokens[userId] = (await (await request.post("/api/auth/login", { data: { user_id: userId } })).json()).token;
  return { Authorization: `Bearer ${tokens[userId]}` };
}

async function story(request: APIRequestContext): Promise<View> {
  return (await request.get("/api/storyline", { headers: await auth(request, "U-ADMIN") })).json();
}

async function stepOf(request: APIRequestContext, n: number): Promise<Step> {
  return (await story(request)).steps[n - 1];
}

const taskId = (ref: string) => ref.split("/")[1];

async function gotoStory(page: Page) {
  await page.goto("/storyline");
  await expect(page.getByTestId("story-step-12")).toBeVisible({ timeout: 60_000 }); // the status has loaded
}

/** A step card, opened (the current step's is open; a done or later one opens with a click on its header). */
async function card(page: Page, n: number): Promise<Locator> {
  const c = page.getByTestId(`story-step-${n}`);
  await expect(c).toBeVisible();
  if (!(await c.getByTestId(`say-${n}`).isVisible())) await c.locator("button").first().click();
  await expect(c.getByTestId(`say-${n}`)).toBeVisible();
  return c;
}

/** The page's one-click switch to the step's person, which opens the step's screen. */
async function play(page: Page, request: APIRequestContext, n: number): Promise<Step> {
  await gotoStory(page);
  const step = await stepOf(request, n);
  const c = await card(page, n);
  await expect(c).toContainText(step.permission.slice(0, 40)); // what switching to this role shows
  await c.getByTestId(`switch-${n}`).click();
  await expect(page).toHaveURL(new RegExp(step.route.replace("/", "\\/")));
  await expect(page.getByTestId("current-user")).toHaveText(step.cast.name);
  return step;
}

/** A demo login of another member of staff (for a second signature), as the login page would do. */
async function signInAs(page: Page, request: APIRequestContext, userId: string, path: string) {
  const token = (await auth(request, userId)).Authorization.split(" ")[1];
  await page.evaluate((t) => localStorage.setItem("ioc.token", t), token);
  await page.goto(path);
}

/** Press "Advance the clock" on the step until its moment has come (the fast-forward runs in the background). */
async function advanceClock(page: Page, request: APIRequestContext, n: number) {
  await gotoStory(page);
  const c = await card(page, n);
  const deadline = Date.now() + 12 * 60_000;
  while (Date.now() < deadline) {
    const view = await story(request);
    if (view.steps[n - 1].status !== "waiting") return;
    if (view.job?.status !== "running") {
      await c.getByTestId("advance").click({ timeout: 20_000 }).catch(() => undefined);
    }
    await page.waitForTimeout(3000);
  }
  throw new Error(`the clock did not reach step ${n}'s moment`);
}

/** The step done in the API, then on the page: Done, with its proposal, its signature and its audit events. */
async function expectDone(page: Page, request: APIRequestContext, n: number, audited = true) {
  await expect.poll(async () => (await stepOf(request, n)).done, { timeout: 180_000, intervals: [1000, 2000, 3000] }).toBe(true);
  await gotoStory(page);
  const c = await card(page, n);
  await expect(c).toHaveAttribute("data-status", "done");
  await expect(c.getByTestId(`story-proposal-${n}`)).not.toContainText("not yet");
  await expect(c.getByTestId(`story-signed-${n}`)).not.toContainText("Waiting for");
  if (audited) await expect(c.getByTestId(`story-audit-${n}`).locator("li").first()).toBeVisible();
  // "You are" follows the signed-in person (the step's cast, or the co-signer who signed last)
  await expect(page.getByTestId("you-name")).toHaveText(await page.getByTestId("current-user").innerText());
  return stepOf(request, n);
}

/** Accept the step's card in the review queue (the AI's suggestion, decided by the person it is for). With Temporal
 * on, the nurse's NEWS2 card is her journey's own Task (the roadmap stub raised the flag it answers). */
async function acceptCard(page: Page, request: APIRequestContext, n: number,
                          { roadmap = false, journey = false }: { roadmap?: boolean; journey?: boolean } = {}) {
  await expect.poll(async () => (await stepOf(request, n)).proposal?.ref ?? "", { timeout: 120_000 }).toMatch(/^Task\//);
  const ref = (await stepOf(request, n)).proposal!.ref;
  await page.goto("/reviews");
  const row = page.locator(`[data-task="${taskId(ref)}"]`);
  await expect(row).toBeVisible({ timeout: 30_000 });
  if (journey) await expect(row).toContainText("journey-stay-j0001");
  else await expect(row.getByText("AI", { exact: false }).first()).toBeVisible();
  if (roadmap) await expect(row).toContainText("Roadmap (8.1): preset data");
  await row.getByRole("textbox", { name: "Note" }).fill("Storyline e2e: decided by the person it is for");
  await row.getByTestId("approve").click();
  await expect(row).toHaveCount(0, { timeout: 30_000 });
  return row;
}

async function say(page: Page, box: string, text: string, pending: string) {
  await page.getByRole("textbox", { name: box }).fill(text, { timeout: 30_000 });
  await page.getByRole("button", { name: "Send" }).click({ timeout: 30_000 });
  await expect(page.getByText(pending)).toBeHidden({ timeout: 30_000 });
}

test("hospital storyline: twelve steps, each proposed by the AI, signed by a person and audited", async ({ page, request }) => {
  test.setTimeout(75 * 60_000); // the clock's fast-forwards and every module's background work, on a loaded machine
  const t0 = Date.now();
  const log = (m: string) => console.log(`[${((Date.now() - t0) / 1000).toFixed(0).padStart(4)} s] ${m}`);

  // ----- reset to the start (one click, as the presenter) -----
  await page.goto("/login");
  await page.getByTestId("login-admin").click();
  await expect(page.getByTestId("current-user")).toBeVisible();
  await page.getByRole("link", { name: "Hospital storyline" }).click();
  page.once("dialog", (d) => void d.accept());
  await page.getByTestId("story-reset").click();
  await expect(page.getByTestId("story-step-1")).toHaveAttribute("data-status", "active", { timeout: 300_000 });
  let view = await story(request);
  expect(view.current).toBe(1);
  expect(view.steps.filter((s) => s.stub).map((s) => s.n)).toEqual([4, 8, 12]);
  for (const n of [4, 8, 12]) await expect(page.getByTestId(`story-step-${n}`)).toContainText("Roadmap (8.1)");
  await expect(page.getByTestId("story-temporal")).toContainText(TEMPORAL ? "Temporal online" : "Temporal off");
  await shot(page, "story-00-start");
  log("reset");

  // ----- 1. the daughter's call in Mandarin; the clerk confirms the ED registration -----
  await play(page, request, 1);
  await page.getByTestId("start-call").click();
  const lines = view.patient!.call_lines;
  for (const line of lines.slice(0, 2)) await say(page, "Type what the caller says…", line, "The agent is answering…");
  const transcript = page.getByTestId("call-transcript");
  await expect(transcript).toContainText("急诊登记");
  await expect(page.getByTestId("call-details")).toContainText("Verified");
  await say(page, "Type what the caller says…", lines[2], "The agent is answering…");
  if (!(await page.getByTestId("call-ended").isVisible())) await page.getByRole("button", { name: "End call" }).click();
  await expect(page.getByTestId("call-summary")).toContainText("daughter");
  const registration = page.getByTestId("call-requests").locator("li").filter({ hasText: MRN })
    .filter({ has: page.getByTestId("registration-request") });
  await expect(registration).toContainText("pre-filled from the call");
  await registration.getByRole("button", { name: "Confirm registration" }).click();
  await expect(registration).toHaveCount(0);
  await shot(page, "story-01-call");
  await expectDone(page, request, 1);
  log("1 call");

  // ----- 2. triage: the clerk's queue does not show the nurse's card; the ED nurse accepts CTAS 2 -----
  await expect.poll(async () => (await stepOf(request, 2)).proposal?.ref ?? "", { timeout: 60_000 }).toMatch(/^Task\//);
  const triageRef = (await stepOf(request, 2)).proposal!.ref;
  await page.goto("/reviews");
  await expect(page.getByTestId("review-queue").or(page.getByText("Nothing waiting for you"))).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(`[data-task="${taskId(triageRef)}"]`)).toHaveCount(0); // the clerk's role
  await play(page, request, 2);
  const triageRow = page.locator(`[data-task="${taskId(triageRef)}"]`);
  await expect(triageRow).toContainText("CTAS 2");
  await expect(triageRow.getByTestId("recommendation")).toContainText("Admission probability 0.82");
  await acceptCard(page, request, 2);
  let step = await expectDone(page, request, 2);
  expect(step.detail).toContain("0.82");
  log("2 triage");

  // ----- 3. imaging orders: urgency and protocols from the imaging pipeline; the ED physician orders -----
  await play(page, request, 3);
  await acceptCard(page, request, 3);
  await expectDone(page, request, 3);
  log("3 imaging");

  // ----- 4. INR 3.8 (roadmap stub): routed to the ED physician -----
  await advanceClock(page, request, 4);
  await play(page, request, 4);
  await acceptCard(page, request, 4, { roadmap: true });
  await expectDone(page, request, 4);
  log("4 inr");

  // ----- 5. Orthopedics full: the bed manager approves early rounds on Surgery; she gets the first freed bed -----
  await advanceClock(page, request, 5);
  await expect.poll(async () => (await stepOf(request, 5)).proposal?.ref ?? "", { timeout: 180_000 }).toMatch(/^FlowException\//);
  const exceptionId = taskId((await stepOf(request, 5)).proposal!.ref);
  await play(page, request, 5);
  const exception = page.locator(`[data-exception="${exceptionId}"]`);
  await expect(exception).toContainText("Orthopedics");
  await exception.click();
  const drawer = page.getByTestId("action-drawer");
  await expect(drawer.getByTestId("narrative")).not.toBeEmpty({ timeout: 60_000 });
  await expect(drawer.getByText("AI-generated").first()).toBeVisible({ timeout: 60_000 });
  // the step's Advance asked for this narrative (BACKGROUND_AI=on_demand): written as that person's storyline step
  await expect(drawer.getByTestId("ai-meta")).toContainText(/Storyline step 5|when you opened it/);
  const rounds = drawer.getByTestId("action-overflow_discharge_rounds");
  if (!(await rounds.isVisible())) await drawer.getByText(/More actions from the engine/).click();
  if (!(await rounds.isChecked())) await rounds.check();
  await drawer.getByTestId("approve").click();
  await expect(drawer.getByTestId("decision")).toContainText("Approved by", { timeout: 30_000 });
  await shot(page, "story-05-approved");
  await page.keyboard.press("Escape");
  await advanceClock(page, request, 5); // her bed: the first one the early discharges free, after cleaning
  step = await expectDone(page, request, 5);
  expect(step.detail).toMatch(/^bed SURG-/);
  log(`5 bed (${step.detail})`);
  // that Advance (until she has a bed) asked for step 6's AI: the naproxen review and the admission reconciliation,
  // run as soon as they appeared; the storyline page shows them done
  await expect.poll(async () => ((await stepOf(request, 6)).step_ai?.items ?? []).map((i) => i.ai.state).join(","),
    { timeout: 180_000, intervals: [1000, 2000, 3000] }).toBe("done,done");

  // ----- 6. admission MedRec: the pharmacist requests a change to naproxen, handles each item, signs; the surgeon co-signs -----
  await play(page, request, 6);
  const pharmacist = await auth(request, "U-PHAR-01");
  let naproxen = "";
  await expect.poll(async () => {
    const items = (await (await request.get("/api/med-safety/queue?show=all", { headers: pharmacist })).json()).items as { id: string; order_ref?: string }[];
    naproxen = items.find((i) => i.order_ref === `MedicationRequest/medrx-${STAY}-03`)?.id ?? "";
    return naproxen;
  }, { timeout: 120_000 }).not.toBe("");
  await page.getByTestId("queue-search").fill(MRN);
  await page.getByTestId(`queue-item-${naproxen}`).click();
  const review = page.getByTestId("review-panel");
  await expect(review.getByTestId("finding").first()).toContainText(/naproxen \+ warfarin|warfarin \+ naproxen/);
  await review.getByTestId("decision-reason").fill("Stop naproxen: warfarin at home, INR 3.8");
  await review.getByTestId("request-change").click();
  await expect(review.getByTestId("decision")).toContainText("Change requested");
  let session = "";
  await expect.poll(async () => { // the intake writes her admission reconciliation after the admission
    const r = await request.get(`/api/med-safety/encounters/${STAY}`, { headers: pharmacist });
    const medrec = r.ok() ? ((await r.json()).medrec as { kind: string; id?: string; session_id?: string }[] | undefined) ?? [] : [];
    const found = medrec.find((m) => m.kind === "admission");
    session = found?.id ?? found?.session_id ?? "";
    return session;
  }, { timeout: 120_000, intervals: [1000, 2000, 3000] }).not.toBe("");
  await expect.poll(async () => { // and its AI step (the indications) has run
    const r = await request.get(`/api/med-safety/medrec/${session}`, { headers: pharmacist });
    return r.ok() ? (await r.json()).ai_status : "";
  }, { timeout: 120_000, intervals: [1000, 2000, 3000] }).not.toMatch(/^(pending|on_open|)$/);
  await page.goto(`/med-safety?encounter=${STAY}&document=medrec-${STAY}-admission`);
  const medrec = page.getByTestId("medrec-panel");
  await expect(medrec).toBeVisible({ timeout: 60_000 });
  await expect(medrec).toContainText(/amlodipine/i, { timeout: 60_000 });
  for (let left = await medrec.getByTestId("item-form").count(); left > 0; left--) {
    const row = medrec.locator("[data-testid^=medrec-row-]").filter({ has: page.getByTestId("item-form") }).first();
    const form = row.getByTestId("item-form");
    if (/amlodipine/i.test(await row.innerText())) {
      await form.getByTestId("item-action").selectOption("modify");
      await form.getByTestId("item-change").fill("Resume amlodipine 5 mg once daily");
      await form.getByTestId("item-reason").fill("Home antihypertensive left out on admission");
    } else {
      await form.getByTestId("item-reason").fill(/warfarin/i.test(await row.innerText())
        ? "Held on purpose: INR 3.8 before surgery" : "Reviewed with the orthopaedic team");
    }
    await form.getByTestId("item-save").click();
    await expect(medrec.getByTestId("item-form")).toHaveCount(left - 1);
  }
  await medrec.getByTestId("sign-button").click();
  await expect(medrec.getByTestId("medrec-status")).toContainText("Signed by the pharmacist");
  await signInAs(page, request, "U-SURG-02", `/med-safety?encounter=${STAY}&document=medrec-${STAY}-admission`);
  await expect(page.getByTestId("current-user")).toHaveText("Dr. Victor Osei");
  await expect(medrec.getByTestId("sign-button")).toHaveText(/Co-sign as physician/, { timeout: 30_000 });
  await medrec.getByTestId("sign-button").click();
  await expect(medrec.getByTestId("medrec-status")).toContainText("the record is final");
  await shot(page, "story-06-medrec");
  await expectDone(page, request, 6);
  log("6 medrec");

  // ----- 7. the OR case: predicted past its booking and the block; only the OR coordinator may adjust it -----
  let appointment = "";
  const ops = await auth(request, "U-OPS");
  await expect.poll(async () => {
    const board = await (await request.get("/api/control-tower/board", { headers: ops })).json();
    const c = (board.or_cases as { mrn?: string; appointment_id?: string; predicted_minutes?: number }[]).find((x) => x.mrn === MRN && x.predicted_minutes);
    appointment = c?.appointment_id ?? "";
    return appointment;
  }, { timeout: 120_000 }).not.toBe("");
  const refused = await request.post(`/api/control-tower/or-cases/${appointment}/adjust`, {
    headers: await auth(request, "U-SURG-02"), data: { note: "not mine to change" } });
  expect(refused.status()).toBe(403); // the surgeon (current role) may not change the booking
  await play(page, request, 7);
  const orRow = page.locator(`[data-testid="or-row"][data-mrn="${MRN}"]`);
  await expect(orRow.getByTestId("block-overtime")).toBeVisible({ timeout: 30_000 });
  await orRow.getByTestId("adjust-booking").click();
  await expect(orRow.getByTestId("adjust-booking")).toHaveCount(0, { timeout: 30_000 });
  await shot(page, "story-07-or");
  step = await expectDone(page, request, 7);
  expect(step.detail).toBe("booked 115 min");
  log("7 or");

  // ----- 8. the first night: NEWS2 7 (roadmap stub); the ward nurse responds -----
  await advanceClock(page, request, 8);
  await play(page, request, 8);
  await acceptCard(page, request, 8, TEMPORAL ? { journey: true } : { roadmap: true });
  await expectDone(page, request, 8);
  log("8 news2");

  // ----- 9. the discharge summary: the validator blocks an untraceable medicine until the surgeon removes it -----
  await advanceClock(page, request, 9);
  const surgeon = await auth(request, "U-SURG-02");
  await expect.poll(async () => {
    const r = await request.get("/api/documentation/worklist", { headers: surgeon });
    const items = (await r.json()).items as { encounter_id: string; discharge_table: string }[];
    return items.find((i) => i.encounter_id === STAY)?.discharge_table ?? "";
  }, { timeout: 180_000 }).toBe("final"); // the discharge MedRec (scripted off-screen) is signed
  await play(page, request, 9);
  await page.getByTestId(`work-${STAY}`).click();
  const draft = page.getByTestId("draft-summary");
  const summary = page.getByTestId("summary-review");
  await expect(draft.or(summary)).toBeVisible({ timeout: 60_000 });
  if (await draft.isVisible()) await draft.click();
  await expect(summary).toBeVisible({ timeout: 90_000 });
  await expect(summary.getByText("AI draft")).toBeVisible();
  await expect(page.getByTestId("validation-bar")).toContainText("signing is blocked");
  const red = summary.locator('[data-testid="summary-item"][data-status="red"]').first();
  await expect(red).toContainText(/lisinopril/i);
  await expect(page.getByTestId("sign-summary")).toBeDisabled();
  await shot(page, "story-09-blocked");
  await page.getByTestId("edit-summary").click();
  await red.getByTestId("remove-item").click();
  await page.getByTestId("save-summary").click();
  await expect(page.getByTestId("validation-bar")).toContainText("Every item traces to its sources");
  await page.getByTestId("sign-summary").click();
  await expect(page.getByTestId("signed")).toContainText("Signed by");
  await expectDone(page, request, 9);
  log("9 summary");

  // ----- 10. the patient instructions in Simplified Chinese; the ward nurse signs -----
  await play(page, request, 10);
  await page.getByTestId(`work-${STAY}`).click();
  const start = page.getByTestId("draft-instructions");
  const instructions = page.getByTestId("instructions-review");
  await expect(start.or(instructions)).toBeVisible({ timeout: 60_000 });
  if (await start.isVisible()) await start.click();
  await expect(instructions).toBeVisible({ timeout: 90_000 });
  const rendered = page.getByTestId("rendered");
  await expect(rendered).toHaveAttribute("lang", "zh-Hans");
  await expect(rendered).toContainText("绿叶蔬菜");
  await page.getByTestId("sign-instructions").click();
  await expect(page.getByTestId("signed")).toContainText("Signed by");
  await shot(page, "story-10-instructions");
  await expectDone(page, request, 10);
  log("10 instructions");

  // ----- 11. 48 hours later: the follow-up call in Mandarin escalates heavy wound drainage; the nurse calls back -----
  await advanceClock(page, request, 11);
  const nurse = await auth(request, "U-NURS-13");
  await expect.poll(async () => {
    const r = await request.get(`/api/voice/followups/FU-${STAY}`, { headers: nurse });
    return r.ok() ? (await r.json()).status : "";
  }, { timeout: 240_000, intervals: [2000, 3000, 5000] }).toBe("scheduled");
  await play(page, request, 11);
  await page.getByRole("tab", { name: "Calls" }).click();
  await page.getByTestId(`followup-FU-${STAY}`).click();
  await page.getByTestId("place-call").click();
  const call = page.getByTestId("followup-call-transcript");
  await expect(call).toContainText("出生日期");
  const answers = view.patient!.followup_lines;
  await say(page, "输入患者的回答…", answers[0], "The agent is answering…");
  await expect(call).toContainText("感觉怎么样");
  await say(page, "输入患者的回答…", answers[1], "The agent is answering…");
  await expect(call).toContainText("护士会在30分钟内给您回电话");
  // the remaining questions (medicines, follow-ups, red flags): "nothing else" until the call ends
  const answerBox = page.getByRole("textbox", { name: "输入患者的回答…" });
  const ended = page.getByTestId("followup-transcript");
  for (let i = 0; i < 8; i++) {
    await expect(answerBox.or(ended).first()).toBeVisible();
    if (await ended.isVisible()) break;
    await say(page, "输入患者的回答…", "没有了。", "The agent is answering…");
  }
  await expect(ended).toContainText("再见");
  await page.getByRole("tab", { name: "Queue" }).click();
  const top = page.getByTestId("followup-list").locator("li").first();
  await expect(top).toContainText("Urgent");
  await expect(top).toContainText("Heavy wound drainage");
  await top.click();
  await expect(page.getByTestId("patient-words")).toContainText("伤口一直在渗液");
  await page.getByRole("textbox", { name: "Nurse note" }).fill("Called back: wound reviewed tomorrow at the fracture clinic");
  await page.getByRole("button", { name: "Save call-back note" }).click();
  await expect(page.getByTestId("nurse-actions")).toContainText("Call-back note");
  await shot(page, "story-11-followup");
  await expectDone(page, request, 11);
  log("11 followup");

  // ----- 12. the coding suggestion (roadmap stub): the coder accepts -----
  await play(page, request, 12);
  await acceptCard(page, request, 12, { roadmap: true });
  await expectDone(page, request, 12);
  log("12 coding");

  // ----- the whole story: twelve steps done; with Temporal, her journey -----
  await gotoStory(page);
  for (let n = 1; n <= 12; n++) await expect(page.getByTestId(`story-step-${n}`)).toHaveAttribute("data-status", "done");
  view = await story(request);
  expect(view.current).toBeNull();
  for (const s of view.steps.filter((x) => ["call", "triage", "imaging", "inr", "bed", "news2", "coding"].includes(x.key))) {
    expect(s.audit.some((a) => a.event_type === "approve" || a.event_type === "sign"), `audit of ${s.key}`).toBeTruthy();
  }
  if (TEMPORAL) {
    expect(view.temporal.status.online).toBeTruthy();
    await expect(page.getByTestId("story-journey")).toContainText("journey-stay-j0001");
    await expect.poll(async () => (await story(request)).journey?.status ?? "", { timeout: 120_000 }).toBe("completed");
  }
  await shot(page, "story-12-done");
  log("done");
});
