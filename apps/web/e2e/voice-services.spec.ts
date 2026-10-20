import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// WP8 (spec 7.4): the hospital call centre (a text-simulated inbound call: FAQ without identity, a reschedule after
// the identity check, confirmed by the clerk in one click), the Chinese follow-up call whose wound-drainage answer
// escalates at once, and the nurse follow-up queue (the patient's words, a call-back note, a referral; RBAC).
// The planted demo cases come from the generator (app/ehr/seed/voice.py) via GET /api/voice/demo.

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

interface Demo {
  followups: Record<string, { stay: string; patient: string; language: string; unit: string }>;
  callers: Record<string, { patient: string; appointments: string[]; identity_line?: string; identity_line_zh?: string;
    label?: string }>;
}
let demo: Demo;

test.beforeAll(async ({ request }, testInfo) => {
  testInfo.setTimeout(240_000);
  const admin = await token(request, "U-ADMIN");
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${admin}` } });
  const clerk = { Authorization: `Bearer ${await token(request, "U-CLER-01")}` };
  demo = await (await request.get("/api/voice/demo", { headers: clerk })).json();
  const nurse = { Authorization: `Bearer ${await token(request, "U-NURS-05")}` };
  expect((await request.post("/api/voice/followups/sweep", { headers: nurse })).ok()).toBeTruthy();
});

async function say(page: Page, text: string) {
  const input = page.getByRole("textbox", { name: "Type what the caller says…" });
  await input.fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("The agent is answering…")).toBeHidden({ timeout: 20_000 });
}

test("call centre: FAQ without identity, then a reschedule the clerk confirms in one click", async ({ page, request }) => {
  const caller = demo.callers.en_fracture;
  expect(caller.identity_line, "the clerk reads the planted caller's identity line").toBeTruthy();
  await login(page, "clerk");
  await page.getByRole("link", { name: "Call centre" }).click();
  await page.getByTestId("start-call").click();
  const transcript = page.getByTestId("call-transcript");
  await expect(transcript).toContainText("Lakeside General Hospital");

  await say(page, "What are the visiting hours?");
  await expect(transcript).toContainText("11 in the morning to 8 in the evening");
  await expect(page.getByTestId("call-details")).toContainText("Not verified");

  await say(page, "I need to reschedule my fracture clinic appointment.");
  await expect(transcript).toContainText("full name, date of birth");
  // the planted caller's shortcut reads as a visitor understands it (who is calling, what they have booked) and says
  // the identity line
  const quick = page.getByLabel("Quick phrases");
  await expect(quick.getByRole("button", { name: /^ID:/ })).toHaveCount(0);
  const shortcut = quick.getByRole("button", { name: caller.label!, exact: true });
  await expect(shortcut).toHaveText(/^I'm .+ \(fracture clinic, CT scan\)$/);
  await shortcut.click();
  await expect(page.getByText("The agent is answering…")).toBeHidden({ timeout: 20_000 });
  await expect(transcript).toContainText(caller.identity_line!);
  await expect(page.getByTestId("call-details")).toContainText("Verified");
  await expect(transcript).toContainText("I can offer");
  await say(page, "The second one");
  await expect(transcript).toContainText("To confirm: move your");
  await say(page, "Yes, please");
  await expect(transcript).toContainText("asked our booking clerk");
  await expect(page.getByTestId("call-actions")).toContainText("Reschedule requested");
  await shot(page, "voice-call-centre");

  // the clerk's one click: the approval of the privileged booking (the old time is cancelled in the same step)
  const requests = page.getByTestId("call-requests");
  await expect(requests).toContainText("Reschedule");
  const confirmed = page.waitForResponse((r) => r.url().includes("/decision") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Confirm reschedule" }).first().click();
  const decision = await (await confirmed).json();
  expect(decision.decision).toBe("approve");
  expect(decision.execution.status, "the booking ran on the clerk's approval").toBe("ok");
  expect(decision.execution.output.replaced).toBe(caller.appointments[0]);
  await expect(page.getByText("Nothing to confirm")).toBeVisible();
  const clerk = { Authorization: `Bearer ${await token(request, "U-CLER-01")}` };
  expect((await (await request.get("/api/voice/requests", { headers: clerk })).json()).requests).toEqual([]);

  // a clinical question is never answered: the nurse line gets it
  await say(page, "Is it normal that my ankle is swollen?");
  await expect(transcript).toContainText("not able to answer medical questions");
  await expect(page.getByTestId("call-actions")).toContainText("Nurse line");
  await say(page, "No, that's all, thanks");
  await expect(page.getByTestId("call-ended")).toBeVisible();
  await expect(page.getByTestId("call-summary")).toContainText("reschedule");
});

test("follow-up: the Chinese call escalates heavy wound drainage at once and tops the nurse queue", async ({ page }) => {
  const zh = demo.followups.zh;
  await login(page, "nurse");
  await page.getByRole("link", { name: /Follow-up calls/ }).click();
  await page.getByRole("tab", { name: "Calls" }).click();
  await page.getByTestId(`followup-FU-${zh.stay}`).click();
  await page.getByTestId("place-call").click();
  const transcript = page.getByTestId("followup-call-transcript");
  await expect(transcript).toContainText("出生日期");
  await page.getByRole("button", { name: "出生日期" }).click();
  await expect(transcript).toContainText("感觉怎么样");
  await page.getByRole("textbox", { name: "输入患者的回答…" }).fill("还行，就是伤口一直在渗液，纱布都湿透了");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(transcript).toContainText("护士会在30分钟内给您回电话");
  // the last answer ends the call: the server runs the AI classifier and writes the observations and the call record
  // before it replies, which can take more than an expectation's 5 s in a full run, so wait for that reply
  const last = page.waitForResponse((r) => r.url().endsWith("/answer") && r.request().method() === "POST");
  await page.getByRole("button", { name: "没有了" }).click();
  expect((await (await last).json()).ended, "the last answer ends the call").toBe(true);
  // the call has ended: the detail shows the whole call (and the AI's de-identified view of it)
  await expect(page.getByTestId("followup-transcript")).toContainText("再见");
  await expect(page.getByRole("button", { name: "As the AI saw it (de-identified)" })).toBeVisible();
  await shot(page, "voice-followup-call");

  await page.getByRole("tab", { name: "Queue" }).click();
  const first = page.getByTestId("followup-list").locator("li").first();
  await expect(first).toContainText("Urgent");
  await expect(first).toContainText("Heavy wound drainage");
  await expect(page.getByTestId("escalation-count")).toBeVisible();
  await first.click();
  await expect(page.getByTestId("red-flags")).toContainText("伤口大量渗液");
  await expect(page.getByTestId("patient-words")).toContainText("伤口一直在渗液");
});

test("nurse queue: call-back note and referral write Communications; clerks and physicians cannot act", async ({ page, request }) => {
  const zh = demo.followups.zh;
  const nurse = { Authorization: `Bearer ${await token(request, "U-NURS-05")}` };
  let item = await (await request.get(`/api/voice/followups/FU-${zh.stay}`, { headers: nurse })).json();
  if (!item.escalated) {  // this test alone: place the escalating call through the API first
    await request.post(`/api/voice/followups/FU-${zh.stay}/start`, { headers: nurse });
    const birth = (await (await request.get("/api/voice/demo", { headers: nurse })).json()).followups.zh.birth_date_line.zh;
    for (const text of [birth, "伤口一直在渗液，纱布都湿透了", "没有了"]) {
      await request.post(`/api/voice/followups/FU-${zh.stay}/answer`, { headers: nurse, data: { text } });
    }
    item = await (await request.get(`/api/voice/followups/FU-${zh.stay}`, { headers: nurse })).json();
  }
  expect(item.escalated).toBeTruthy();

  const clerk = { Authorization: `Bearer ${await token(request, "U-CLER-01")}` };
  expect((await request.get("/api/voice/followups", { headers: clerk })).status()).toBe(403);
  const doctor = { Authorization: `Bearer ${await token(request, "U-DOC-09")}` };
  expect((await request.post(`/api/voice/followups/FU-${zh.stay}/actions`, { headers: doctor,
    data: { kind: "close", note: "not mine" } })).status()).toBe(403);

  await login(page, "nurse");
  await page.getByRole("link", { name: /Follow-up calls/ }).click();
  await page.getByTestId(`followup-FU-${zh.stay}`).click();
  await page.getByRole("textbox", { name: "Nurse note" }).fill("Called back: the dressing is soaked twice a day; asked her to come in today.");
  await page.getByRole("button", { name: "Save call-back note" }).click();
  await expect(page.getByTestId("nurse-actions")).toContainText("Call-back note");
  await page.getByRole("textbox", { name: "Nurse note" }).fill("Wound review by the physician today.");
  await page.getByRole("button", { name: "Refer to a physician" }).click();
  await expect(page.getByTestId("nurse-actions")).toContainText("Referred to a physician");
  await shot(page, "voice-nurse-queue");

  item = await (await request.get(`/api/voice/followups/FU-${zh.stay}`, { headers: nurse })).json();
  expect(item.review_status).toBe("referred");
  expect(item.nurse_actions.map((a: { kind: string }) => a.kind)).toEqual(["callback", "refer"]);
  expect(item.nurse_actions.every((a: { communication_id: string }) => a.communication_id)).toBeTruthy();
});
