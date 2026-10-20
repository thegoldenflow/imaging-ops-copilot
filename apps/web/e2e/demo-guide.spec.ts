import { expect as baseExpect, test, type Locator, type Page } from "@playwright/test";

// Demo mode for the hospital storyline (spec 8.2, WP9): the presenter's guide introduces a step, switches to the
// person who plays it and opens their screen, gives the caller's lines as "Say it" buttons, and once the step is
// done shows its trail and asks "Continue to the next step?" in the panel (the screen behind stays usable). "Not
// yet" pauses the guide, which survives a reload (per browser) and resumes on the next step's introduction.

// The reset rebuilds the demo data and each step waits for the API's background work: allow a minute per wait.
const expect = baseExpect.configure({ timeout: 60_000 });

type Box = { x: number; y: number; width: number; height: number };
const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

/** The spotlight's label is placed and covers neither the button it marks nor any of `others` (the target's own row,
 * the guide panel, the sign-in page's other cards): it covered the very line to say, the registration card's heading
 * and Jordan Lee's card before. */
async function labelClear(page: Page, ...others: Locator[]): Promise<boolean> {
  const label = await page.getByTestId("demo-spotlight-label").boundingBox();
  const ring = await page.getByTestId("demo-spotlight-ring").boundingBox();
  if (!label || !ring || overlaps(label, ring)) return false;
  for (const other of others) {
    for (const el of await other.all()) {
      const b = await el.boundingBox();
      if (b && overlaps(label, b)) return false;
    }
  }
  return true;
}

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
  // the way in is marked too: the presenter's card on the sign-in page, then the storyline page's Demo mode button
  const spot = page.getByTestId("demo-spotlight-label");
  await expect(spot).toContainText("sign in as Casey Brooks");
  await expect.poll(() => labelClear(page, page.locator("[data-testid^=login-]"))).toBe(true); // on no card's text
  await page.evaluate((t) => {
    localStorage.setItem("ioc.token", t);
    localStorage.removeItem("ioc.demo");
    localStorage.removeItem("ioc.subtitles");
  }, token);
  await page.goto("/storyline");
  await expect(spot).toContainText("Start the guided demo");
  await page.getByTestId("story-demo").click();

  // 1. the introduction: what the step shows, who plays it, what to say
  const intro = page.getByTestId("demo-intro-1");
  await expect(intro).toContainText("A daughter calls in Mandarin");
  await expect(intro).toContainText("Maya Nakamura");
  await expect(intro).toContainText("The audience will see");
  // inside demo mode the spotlight marks the button to press next, for someone who has never seen the app
  await expect(spot).toContainText("Start step 1");
  const guide = page.getByTestId("demo-guide");
  await expect.poll(() => labelClear(page, guide)).toBe(true); // a button of the guide: the label goes outside it
  // the role strip under the header: who presents, and who plays the step
  const strip = page.getByTestId("demo-role-strip");
  await expect(strip.getByTestId("demo-role-name")).toHaveText("Casey Brooks · Administrator");
  await expect(strip).toContainText("Step 1 is played by Maya Nakamura · Registration clerk");
  await page.getByTestId("demo-start").click();

  // 2. the presenter's turn: switched to the clerk on the call centre, the caller's lines as buttons
  await expect(page).toHaveURL(/\/call-centre/);
  await expect(page.getByTestId("current-user")).toHaveText("Maya Nakamura");
  // the switch is announced for a few seconds, then the strip stays with what this role may see in this step
  await expect(strip).toHaveAttribute("data-switched", "true");
  await expect(strip.getByTestId("demo-role-name")).toHaveText("Maya Nakamura · Registration clerk");
  await expect(strip.getByTestId("demo-role-notice")).toHaveText("switched from Casey Brooks · Administrator");
  await expect(strip.getByTestId("demo-role-permission")).toHaveText(`Step 1: ${story.steps[0].permission}`);
  const doing = page.getByTestId("demo-doing-1");
  await expect(doing).toContainText("Waiting for the registration clerk");
  // the clicks listed in the guide are the ones the spotlight marks, in its order, with the buttons' own words
  await expect(page.getByTestId("demo-clicks").locator("li")).toHaveText(["Call centre", "Start a call",
    "Say the daughter's three lines, one at a time (her goodbye ends the call)", "Requests to confirm: ED registration", "Confirm"]);
  await expect(page.getByTestId("start-call")).toHaveText("Start a call");
  await expect(page.getByTestId("demo-say-0")).toBeDisabled(); // no call open yet: a line would go nowhere
  // English subtitles for an English-speaking audience: each Chinese line with its fixed translation
  await expect(page.getByTestId("demo-say-translation-0")).toHaveText(`Translation: ${translations[lines[0]]}`);
  await expect(page.getByTestId("demo-say-translation-1")).toContainText("Her name is Wang Xiuying, born");
  await expect(spot).toHaveText("Start the call");
  await expect(page.getByTestId("demo-next")).toContainText("Next: Start the call");
  await expect.poll(() => labelClear(page, strip, guide)).toBe(true); // the strip is in the page's flow, under nothing
  await expect(strip).toHaveAttribute("data-switched", "false", { timeout: 15_000 }); // the notice is over
  await page.getByTestId("start-call").click();
  await expect(spot).toHaveText("Say the daughter's first line"); // a button of the guide panel
  await expect.poll(() => labelClear(page, guide)).toBe(true); // outside the panel, not over the line to say
  // one line at a time: only the next one can be said
  await expect(page.getByTestId("demo-say-0")).toBeEnabled();
  await expect(page.getByTestId("demo-say-1")).toBeDisabled();
  await expect(page.getByTestId("demo-say-2")).toBeDisabled();
  // the quick phrases under the call input, without the developer's seeded-caller shortcuts ("ID: en_heart")
  const quick = page.getByLabel("Quick phrases");
  await expect(quick.getByRole("button").first()).toBeVisible();
  await expect(quick.getByRole("button", { name: /^ID:/ })).toHaveCount(0);
  await page.getByTestId("demo-say-0").click();
  await expect(page.getByTestId("call-transcript")).toContainText(lines[0]);
  // and under the call's Chinese turns, while demo mode is on: hers (the script's) and the agent's (its hand-written
  // English reply)
  await expect(page.getByTestId("subtitles-toggle")).toBeChecked();
  const glosses = page.getByTestId("call-transcript").getByTestId("turn-gloss");
  await expect(glosses.nth(0)).toHaveText(`Translation: ${translations[lines[0]]}`);
  await expect(glosses.nth(1)).toContainText("First I need to confirm who the patient is");
  await expect(spot).toHaveText("Say who the patient is (name, birth date, card)");
  await expect(page.getByTestId("demo-say-0")).toHaveText("Said");
  await expect(page.getByTestId("demo-say-0")).toBeDisabled();
  await expect(page.getByTestId("demo-say-2")).toBeDisabled();
  await page.getByTestId("demo-say-1").click();
  await expect(page.getByTestId("call-transcript")).toContainText(lines[1]);
  await expect(page.getByTestId("call-details")).toContainText("Verified");
  // found her and prepared the registration in one reply, with one thank-you (it said "Thank you." twice)
  await expect(glosses.nth(3)).toContainText("Thank you, I've found the record for Xiuying. I have prepared Xiuying's registration");
  // the agent asks "anything else?": her last line is marked, and her goodbye ends the call
  await expect(spot).toHaveText("Say the daughter's last line: the call ends");
  await page.getByTestId("demo-say-2").click();
  await expect(page.getByTestId("call-ended")).toBeVisible();
  // the call's Start button is back, but the call is done: the spotlight goes on to the registration
  await expect(spot).toHaveText("Confirm the registration the AI pre-filled");
  const registration = page.getByTestId("call-requests").locator("li").filter({ has: page.getByTestId("registration-request") });
  await expect.poll(() => labelClear(page, registration, guide)).toBe(true); // not over "ED registration · MRN …"
  if (process.env.E2E_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/demo-spotlight.png` });
  // the guide never signs: the clerk confirms the AI's pre-filled registration on the screen
  await page.getByRole("button", { name: "Confirm registration" }).click();

  // 3. done: the trail and the question, in the panel (her goodbye ended the call: no End call left to press)
  const ask = page.getByTestId("demo-ask-1");
  await expect(ask).toContainText("Continue to the next step?");
  await expect(ask).toContainText("AI proposed");
  await expect(ask).toContainText("by Maya Nakamura");
  await expect(ask).toContainText("Eun-ji Abernathy"); // who plays the next step
  await expect(spot).toHaveText("Continue when the audience is ready");
  await expect(page.getByRole("button", { name: "End call" })).toHaveCount(0);

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
  await expect(strip.getByTestId("demo-role-notice")).toHaveText("switched from Maya Nakamura · Registration clerk");
  await expect(strip.getByTestId("demo-role-name")).toHaveText("Eun-ji Abernathy · Nurse");
  await expect(page.getByTestId("demo-doing-2")).toContainText("Accept");
  await expect(spot).toHaveText("Accept the AI's CTAS 2 suggestion"); // on her card in the review queue

  await page.getByTestId("demo-exit").click();
  await expect(page.getByTestId("demo-guide")).toHaveCount(0);
});
