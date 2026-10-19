import { expect as baseExpect, test } from "@playwright/test";

// Demo mode for the hospital storyline (spec 8.2, WP9): the presenter's guide introduces a step, switches to the
// person who plays it and opens their screen, gives the caller's lines as "Say it" buttons, and once the step is
// done shows its trail and asks "Continue to the next step?" in the panel (the screen behind stays usable). "Not
// yet" pauses the guide, which survives a reload (per browser) and resumes on the next step's introduction.

// The reset rebuilds the demo data and each step waits for the API's background work: allow a minute per wait.
const expect = baseExpect.configure({ timeout: 60_000 });

test("demo mode: introduce the step, play it, ask the audience before the next one", async ({ page, request }) => {
  test.setTimeout(300_000);
  const { token } = await (await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } })).json();
  // the Mandarin-speaking patient (the default is the English one): her call with its English subtitles
  const reset = await request.post("/api/storyline/reset", { headers: { Authorization: `Bearer ${token}` }, data: { language: "zh" },
    timeout: 180_000 });
  expect(reset.ok()).toBeTruthy();
  const story = await (await request.get("/api/storyline", { headers: { Authorization: `Bearer ${token}` } })).json();
  const lines: string[] = story.patient.call_lines;
  const translations: Record<string, string> = story.patient.translations;

  await page.goto("/login");
  await page.evaluate((t) => {
    localStorage.setItem("ioc.token", t);
    localStorage.removeItem("ioc.demo");
    localStorage.removeItem("ioc.subtitles");
  }, token);
  await page.goto("/storyline");
  await page.getByTestId("story-demo").click();

  // 1. the introduction: what the step shows, who plays it, what to say
  const intro = page.getByTestId("demo-intro-1");
  await expect(intro).toContainText("A daughter calls in Mandarin");
  await expect(intro).toContainText("Maya Nakamura");
  await expect(intro).toContainText("The audience will see");
  // the spotlight: an amber ring and a label on the button to press next, for someone who has never seen the app
  const spot = page.getByTestId("demo-spotlight-label");
  await expect(spot).toContainText("Start step 1");
  await page.getByTestId("demo-start").click();

  // 2. the presenter's turn: switched to the clerk on the call centre, the caller's lines as buttons
  await expect(page).toHaveURL(/\/call-centre/);
  await expect(page.getByTestId("current-user")).toHaveText("Maya Nakamura");
  const doing = page.getByTestId("demo-doing-1");
  await expect(doing).toContainText("Waiting for the registration clerk");
  await expect(page.getByTestId("demo-say-0")).toBeDisabled(); // no call open yet: a line would go nowhere
  // English subtitles for an English-speaking audience: each Chinese line with its fixed translation
  await expect(page.getByTestId("demo-say-translation-0")).toHaveText(`Translation: ${translations[lines[0]]}`);
  await expect(page.getByTestId("demo-say-translation-1")).toContainText("Her name is Wang Xiuying, born");
  await expect(spot).toHaveText("Start the call");
  await expect(page.getByTestId("demo-next")).toContainText("Next: Start the call");
  await page.getByTestId("start-call").click();
  await expect(spot).toHaveText("Say the daughter's first line"); // a button of the guide panel
  await page.getByTestId("demo-say-0").click();
  await expect(page.getByTestId("call-transcript")).toContainText(lines[0]);
  // and under the call's Chinese turns, while demo mode is on: hers (the script's) and the agent's (its hand-written
  // English reply)
  await expect(page.getByTestId("subtitles-toggle")).toBeChecked();
  const glosses = page.getByTestId("call-transcript").getByTestId("turn-gloss");
  await expect(glosses.nth(0)).toHaveText(`Translation: ${translations[lines[0]]}`);
  await expect(glosses.nth(1)).toContainText("First I need to confirm who the patient is");
  await expect(spot).toHaveText("Say who the patient is (name, birth date, card)");
  await page.getByTestId("demo-say-1").click();
  await expect(page.getByTestId("call-transcript")).toContainText(lines[1]);
  await expect(page.getByTestId("call-details")).toContainText("Verified");
  await expect(glosses.nth(3)).toContainText("Thank you, I've found the record for Xiuying.");
  await expect(spot).toHaveText("Confirm the registration the AI pre-filled");
  if (process.env.E2E_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/demo-spotlight.png` });
  // the guide never signs: the clerk confirms the AI's pre-filled registration on the screen
  await page.getByRole("button", { name: "Confirm registration" }).click();

  // 3. done: the trail and the question, in the panel; the call can still be ended behind it
  const ask = page.getByTestId("demo-ask-1");
  await expect(ask).toContainText("Continue to the next step?");
  await expect(ask).toContainText("AI proposed");
  await expect(ask).toContainText("by Maya Nakamura");
  await expect(ask).toContainText("Eun-ji Abernathy"); // who plays the next step
  await expect(spot).toHaveText("End the call");
  await page.getByRole("button", { name: "End call" }).click();
  await expect(page.getByTestId("call-ended")).toBeVisible();
  await expect(spot).toHaveText("Continue when the audience is ready");

  // Not yet: a pill, kept across a reload; Resume opens the next step's introduction
  await page.getByTestId("demo-not-yet").click();
  await expect(page.getByTestId("demo-paused")).toContainText("Demo paused after step 1");
  await page.reload();
  await expect(page.getByTestId("demo-paused")).toBeVisible();
  await page.getByTestId("demo-resume").click();
  await expect(page.getByTestId("demo-intro-2")).toContainText("Triage");
  await page.getByTestId("demo-start").click();
  await expect(page).toHaveURL(/\/reviews/);
  await expect(page.getByTestId("current-user")).toHaveText("Eun-ji Abernathy");
  await expect(page.getByTestId("demo-doing-2")).toContainText("Accept");
  await expect(spot).toHaveText("Accept the AI's CTAS 2 suggestion"); // on her card in the review queue

  await page.getByTestId("demo-exit").click();
  await expect(page.getByTestId("demo-guide")).toHaveCount(0);
});
