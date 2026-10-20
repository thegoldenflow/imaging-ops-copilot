import { expect as baseExpect, test } from "@playwright/test";

// The storyline's English-speaking patient (spec 8.2, WP9; the reset's language "en"): Margaret Ellis instead of Wang
// Xiuying, for an English-speaking audience. The same twelve steps; here demo mode with the spotlight through step 1
// (her daughter's call in English: the identity check, the registration the clerk confirms) and step 11 (the follow-up
// call in English escalating heavy wound drainage, the nurse's call-back). The demo's automation jumps over 2-10.

// The reset rebuilds the demo data and each step waits for the API's background work: allow a minute per wait.
const expect = baseExpect.configure({ timeout: 60_000 });

test("the English-speaking patient: steps 1 and 11 in demo mode with the spotlight", async ({ page, request }) => {
  test.setTimeout(1_200_000);
  const { token } = await (await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } })).json();
  const auth = { Authorization: `Bearer ${token}` };
  const reset = await request.post("/api/storyline/reset", { headers: auth, data: { language: "en" }, timeout: 180_000 });
  expect(reset.ok()).toBeTruthy();
  expect((await reset.json()).language).toBe("en");
  const story = await (await request.get("/api/storyline", { headers: auth })).json();
  const lines: string[] = story.patient.call_lines;
  expect(lines[1]).toContain("Her name is Margaret Ellis, born");
  expect(story.patient.translations).toEqual({});

  await page.goto("/login");
  await page.evaluate((t) => {
    localStorage.setItem("ioc.token", t);
    localStorage.removeItem("ioc.demo");
    localStorage.removeItem("ioc.subtitles");
  }, token);
  await page.goto("/storyline");
  await expect(page.getByTestId("story-patient")).toContainText("Margaret Ellis");
  await expect(page.getByTestId("story-reset-language")).toHaveValue("en"); // a reset keeps her unless another is picked
  await page.getByTestId("story-demo").click();

  // ----- step 1: her daughter calls, in English -----
  const intro = page.getByTestId("demo-intro-1");
  await expect(intro).toContainText("A daughter calls the hospital");
  await expect(intro).toContainText("ED registration, Margaret Ellis");
  await expect(intro).not.toContainText("Mandarin");
  const spot = page.getByTestId("demo-spotlight-label");
  await expect(spot).toContainText("Start step 1");
  await page.getByTestId("demo-start").click();
  await expect(page).toHaveURL(/\/call-centre/);
  await expect(page.getByTestId("current-user")).toHaveText("Maya Nakamura");
  await expect(page.getByTestId("demo-say-0")).toBeDisabled();
  await expect(page.getByTestId("demo-say-translation-0")).toHaveCount(0); // English lines: nothing to translate
  await expect(spot).toHaveText("Start the call");
  await page.getByTestId("start-call").click();
  await expect(spot).toHaveText("Say the daughter's first line");
  await page.getByTestId("demo-say-0").click();
  const transcript = page.getByTestId("call-transcript");
  await expect(transcript).toContainText(lines[0]);
  await expect(transcript).toContainText("First I need to confirm who the patient is");
  await expect(spot).toHaveText("Say who the patient is (name, birth date, card)");
  await page.getByTestId("demo-say-1").click();
  await expect(page.getByTestId("call-details")).toContainText("Verified");
  // one reply, one thank-you (it read "…the record for Margaret. Thank you. I have prepared…")
  await expect(transcript).toContainText("Thank you, I've found the record for Margaret. I have prepared Margaret's registration");
  await expect(transcript).not.toContainText("Thank you. I have prepared");
  await expect(page.getByTestId("subtitles-toggle")).toHaveCount(0); // an English call has nothing to subtitle
  // the agent asked "anything else?": her goodbye ends the call, then the clerk confirms
  await expect(spot).toHaveText("Say the daughter's last line: the call ends");
  await page.getByTestId("demo-say-2").click();
  await expect(page.getByTestId("call-summary")).toContainText("Call from the patient's daughter");
  await expect(spot).toHaveText("Confirm the registration the AI pre-filled");
  await page.getByRole("button", { name: "Confirm registration" }).click();
  await expect(page.getByTestId("demo-ask-1")).toContainText("Continue to the next step?");
  await expect(spot).toHaveText("Continue when the audience is ready");
  await page.getByTestId("demo-continue").click();

  // ----- steps 2-10: the demo's automation (Jump to step 11 in the guide) -----
  await expect(page.getByTestId("demo-intro-2")).toBeVisible();
  await page.getByTestId("demo-jump-select").selectOption("11");
  await page.getByTestId("demo-jump").click();
  await expect(page.getByTestId("demo-automating")).toBeVisible();
  const intro11 = page.getByTestId("demo-intro-11");
  await expect(intro11).toContainText("the follow-up call escalates heavy wound drainage", { timeout: 900_000 });
  await expect(intro11).not.toContainText("Mandarin");
  await expect(intro11).not.toContainText("Chinese");
  const instructions = (await (await request.get("/api/storyline", { headers: auth })).json()).steps[9];
  expect(instructions.done && instructions.title).toBe("Patient instructions: warfarin diet and wound red flags");

  // ----- step 11: Start switches to the ward nurse, opens her call and moves the clock 48 hours -----
  await expect(spot).toContainText("Start step 11");
  await page.getByTestId("demo-start").click();
  await expect(page).toHaveURL(/\/follow-up\?call=FU-stay-j0001/);
  await expect(page.getByTestId("current-user")).toHaveText("Vera Uchida");
  // her call is opened before the clock reaches it: "not scheduled yet", asked again until it is (it stayed on a 404,
  // with no Place the call, before; casebook 11.7)
  await expect(page.getByTestId("followup-not-scheduled")).toBeVisible();
  await expect(spot).toHaveText("Place the follow-up call", { timeout: 300_000 }); // once the call is scheduled
  await expect(page.getByTestId("followup-detail")).toContainText("English");
  // the button is where the guide sits (bottom right, at this window size): the guide moves to the bottom left
  await expect(page.getByTestId("demo-guide")).toHaveAttribute("data-dock", "left");
  await page.getByTestId("place-call").click();
  const call = page.getByTestId("followup-call-transcript");
  await expect(call).toContainText("please tell me your date of birth");
  await expect(spot).toHaveText("Answer as the patient: her birth date");
  await page.getByTestId("demo-say-0").click();
  await expect(call).toContainText("How have you been feeling since you went home?");
  await expect(spot).toHaveText("Answer: the wound keeps leaking");
  await page.getByTestId("demo-say-1").click();
  await expect(call).toContainText("A nurse will call you back within 30 minutes");
  await expect(page.getByTestId("subtitles-toggle")).toHaveCount(0);
  // the remaining questions: "nothing else" until the call ends
  const ended = page.getByTestId("followup-transcript");
  for (let i = 0; i < 8 && !(await ended.isVisible()); i++) {
    await expect(spot).toHaveText("Answer the remaining questions: nothing else");
    await page.getByTestId("demo-say-2").click();
    // the agent's next question (the button listens again) or the end of the call
    await expect(page.getByTestId("demo-say-2").and(page.locator(":enabled")).or(ended).first()).toBeVisible();
  }
  await expect(ended).toContainText("Goodbye");
  // the nurse's queue: her escalation on top, her own words, the call-back
  await expect(spot).toHaveText("Open the nurse's queue");
  await page.getByRole("tab", { name: "Queue" }).click();
  const top = page.getByTestId("followup-list").locator("li").first();
  await expect(top).toContainText("Urgent");
  await expect(top).toContainText("Heavy wound drainage");
  // the queue opens its top item, hers: the spotlight goes straight on to the note
  await expect(page.getByTestId("patient-words")).toContainText("the wound keeps leaking");
  await expect(spot).toHaveText("Write what you did when you called back");
  await page.getByRole("textbox", { name: "Nurse note" }).fill("Called back: wound reviewed tomorrow at the fracture clinic");
  await expect(spot).toHaveText("Save the call-back note");
  await page.getByRole("button", { name: "Save call-back note" }).click();
  const ask = page.getByTestId("demo-ask-11");
  await expect(ask).toContainText("Continue to the next step?");
  await expect(ask).toContainText("by Vera Uchida");
  if (process.env.E2E_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/demo-english-11.png` });
});
