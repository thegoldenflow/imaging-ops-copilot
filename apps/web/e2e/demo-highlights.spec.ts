import { expect as baseExpect, test } from "@playwright/test";

// Demo mode's highlights preset for the hospital storyline (spec 8.2, WP9): only steps 1, 5, 9 and 11 are presented.
// The default story (the reset without a choice: Margaret Ellis, who speaks English). The presenter plays step 1 (her
// daughter's call) by hand; "Yes, continue" then has the demo's automation do steps 2-4
// in the API's background, as the people who play them, and the guide introduces step 5. The steps it did are marked
// on the storyline page, and so is what it signed ("Demo automation on behalf of ...").

// The reset rebuilds the demo data and each step waits for the API's background work: allow a minute per wait.
const expect = baseExpect.configure({ timeout: 60_000 });

test("highlights: play step 1, the automation does steps 2-4, the guide introduces step 5", async ({ page, request }) => {
  test.setTimeout(420_000);
  const { token } = await (await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } })).json();
  const reset = await request.post("/api/storyline/reset", { headers: { Authorization: `Bearer ${token}` }, timeout: 180_000 });
  expect(reset.ok()).toBeTruthy();
  expect((await reset.json()).language).toBe("en"); // no choice: the English story

  await page.goto("/login");
  await page.evaluate((t) => { localStorage.setItem("ioc.token", t); localStorage.removeItem("ioc.demo"); }, token);
  await page.goto("/storyline");
  await page.getByTestId("story-demo-highlights").click();

  // step 1, a highlight: introduced and played by hand
  const intro = page.getByTestId("demo-intro-1");
  await expect(intro).toContainText("A daughter calls the hospital");
  await expect(intro).toContainText("Highlights");
  await expect(page.getByTestId("demo-highlights")).toBeChecked();
  await page.getByTestId("demo-start").click();
  await expect(page).toHaveURL(/\/call-centre/);
  await page.getByTestId("start-call").click();
  await page.getByTestId("demo-say-0").click();
  await expect(page.getByTestId("demo-say-1")).toBeEnabled();
  await page.getByTestId("demo-say-1").click();
  await expect(page.getByTestId("call-details")).toContainText("Verified");
  await page.getByRole("button", { name: "Confirm registration" }).click();

  // done: the next one shown is the next highlight, with the steps the automation does on the way
  const ask = page.getByTestId("demo-ask-1");
  await expect(ask).toContainText("Next highlight · step 5");
  await expect(ask).toContainText("Jordan Lee");
  await expect(page.getByTestId("demo-ask-skipping")).toContainText("steps 2–4");
  await page.getByRole("button", { name: "End call" }).click();
  await page.getByTestId("demo-continue").click();

  // the automation at work: steps 2-4 in the API's background, as the triage nurse and the ED physician
  await expect(page.getByTestId("demo-guide")).toContainText("getting to step 5");
  const automating = page.getByTestId("demo-automating");
  await expect(automating).toContainText("Every record it signs says");
  await expect(automating).toContainText("started by Maya Nakamura");

  // step 5, the next highlight: introduced (the automation took 1-2 minutes against the mock model)
  const intro5 = page.getByTestId("demo-intro-5");
  await expect(intro5).toContainText("Orthopedics is full", { timeout: 240_000 });
  await expect(intro5).toContainText("Jordan Lee");

  // the storyline page: steps 2-4 done by the automation, and the triage card's decision says so
  await page.goto("/storyline");
  for (const n of [2, 3, 4]) {
    await expect(page.getByTestId(`story-step-${n}`)).toHaveAttribute("data-status", "done");
    await expect(page.getByTestId(`story-automated-${n}`)).toBeVisible();
  }
  await expect(page.getByTestId("story-automated-1")).toHaveCount(0); // the presenter played step 1
  // Orthopedics is flagged from 07:00 (the preset fills it), but her step waits for the admission decision: it was
  // "active" with no Advance anywhere, and the presenter was stuck (live run 10-18)
  await expect(page.getByTestId("story-step-5")).toHaveAttribute("data-status", "waiting");
  await expect(page.getByTestId("story-step-5").getByTestId("advance")).toBeVisible();
  await page.getByTestId("story-step-2").getByRole("button").first().click(); // open the done step's card
  await expect(page.getByTestId("story-signed-note-2")).toContainText("Demo automation on behalf of Eun-ji Abernathy (nurse), started by Maya Nakamura");

  // Start step 5 as usual: the bed manager's Control Tower, the clock moving to the admission decision
  await page.getByTestId("demo-start").click();
  await expect(page).toHaveURL(/\/control-tower/);
  await expect(page.getByTestId("current-user")).toHaveText("Jordan Lee");
  await expect(page.getByTestId("demo-doing-5")).toContainText("Approve");
  // Start moved the clock to the decision: her step is ready, and the exception is high with her boarding
  const auth = { Authorization: `Bearer ${token}` };
  await expect.poll(async () => {
    const view = await (await request.get("/api/storyline", { headers: auth })).json();
    return view.steps[4].status;
  }, { timeout: 120_000, intervals: [1000, 2000, 3000] }).toBe("active");
  // polled: the Control Tower's sync of the decision commits with the hospital loop's pass, a moment after her record
  const ops = (await (await request.post("/api/auth/login", { data: { user_id: "U-OPS" } })).json()).token;
  await expect.poll(async () => {
    const list = await (await request.get("/api/control-tower/exceptions", { headers: { Authorization: `Bearer ${ops}` } })).json();
    return list.exceptions.find((e: { key: string; status: string }) => e.key === "unit_occupancy:ORTH" && e.status === "open")?.severity;
  }, { timeout: 60_000, intervals: [1000, 2000, 3000] }).toBe("high");
});
