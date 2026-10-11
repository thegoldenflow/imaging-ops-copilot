import { expect, test, type Page } from "@playwright/test";

// Architecture pages (spec 8.1, 8.3, WP10). Roadmap: one card per 8.1 row with the fixed footer; the stubs the
// hospital storyline walks through link to their step and show their registry entry. Safety tiers: the three tiers
// with the registered agents and their signers, the tool side-effect levels, the Level 0-4 mapping, guardrails with
// their latest eval result and the honest boundaries, all read from the registries and the eval runs. No demo reset:
// the pages read configuration, not demo data.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function signIn(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test("roadmap: every 8.1 row is a card with the footer; the storyline stubs link to their step and registry entry", async ({ page }) => {
  await signIn(page, "clerk");
  await page.getByRole("link", { name: "Roadmap" }).click();
  await expect(page).toHaveURL(/\/roadmap$/);
  const cards = page.locator('[data-testid^="roadmap-card-"]');
  await expect(cards).toHaveCount(14);
  await expect(page.getByTestId("roadmap-footer")).toHaveCount(14);
  for (const footer of await page.getByTestId("roadmap-footer").allTextContents()) {
    expect(footer).toBe("Roadmap item, not implemented. Architecture: docs/data-model.md and the registry (apps/api/config/agents/).");
  }

  const critical = page.getByTestId("roadmap-card-critical_value_routing");
  await expect(critical).toContainText("Critical results are not seen in time.");
  await expect(critical).toContainText("clinical_ds");
  await expect(critical.getByTestId("roadmap-registry-critical_value_routing")).toContainText("critical_value_routing");
  await expect(critical.getByTestId("roadmap-registry-critical_value_routing")).toContainText("apps/api/config/agents/critical_value_routing.yaml");
  await expect(page.getByTestId("roadmap-registry-deterioration_alert")).toContainText("Sign-off: Nurse");
  await expect(page.getByTestId("roadmap-registry-coding_assist")).toContainText("documentation");
  await expect(page.getByTestId("roadmap-step-mpi")).toContainText("Storyline step 3");
  await expect(page.getByTestId("roadmap-card-supply_chain").getByTestId("roadmap-demo-supply_chain")).toHaveText("Card only.");

  // The filter "In the storyline" leaves the four preset cards.
  await page.getByTestId("roadmap-filter-storyline").click();
  await expect(cards).toHaveCount(4);
  await shot(page, "roadmap-01-storyline-cards");

  // A stub's link opens its storyline step.
  const link = page.getByTestId("roadmap-step-critical_value_routing");
  await expect(link).toContainText("Storyline step 4: INR 3.8");
  await expect(link).toHaveAttribute("href", "/storyline#step-4");
  await link.click();
  await expect(page).toHaveURL(/\/storyline#step-4$/);
  await expect(page.getByTestId("storyline")).toBeVisible();
});

test("safety tiers: tiers, signers and tools from the registries, guardrails with their latest eval run, honest boundaries", async ({ page, request }) => {
  await signIn(page, "physician");
  await page.getByRole("link", { name: "Safety tiers" }).click();
  await expect(page).toHaveURL(/\/safety$/);

  const token = (await (await request.post("/api/auth/login", { data: { user_id: "U-DOC-09" } })).json()).token;
  const safety = await (await request.get("/api/roadmap/safety", { headers: { Authorization: `Bearer ${token}` } })).json();

  await expect(page.getByTestId("safety-positioning")).toContainText("One healthcare AI operations platform running many governed clinical and operational workflows.");
  await expect(page.getByTestId("safety-positioning")).toContainText(`${safety.platform.entries}`);
  for (const tier of ["ops", "documentation", "clinical_ds"]) {
    await expect(page.getByTestId(`tier-${tier}`)).toBeVisible();
  }
  await expect(page.getByTestId("tier-clinical_ds")).toContainText("never block");
  await expect(page.getByTestId("tier-clinical_ds")).toContainText("SaMD");
  await expect(page.getByTestId("tier-signoff-clinical_ds")).toContainText("Pharmacist");
  await expect(page.getByTestId("tier-signoff-documentation")).toContainText("Physician");
  const opsEntries = page.getByTestId("tier-entries-ops");
  await opsEntries.locator("summary").click();
  await expect(opsEntries).toContainText("control_tower");

  // The second axis: four levels, privileged tools with their approvers.
  for (const level of ["read", "recommend", "action", "privileged"]) {
    await expect(page.getByTestId(`level-${level}`)).toBeVisible();
  }
  const privileged = page.getByTestId("level-privileged");
  await privileged.locator("summary").click();
  await expect(privileged).toContainText("signDocumentFinal");
  await expect(privileged).toContainText("approved by");
  await expect(page.getByTestId("safety-reach")).toBeVisible();
  await expect(page.getByTestId("safety-mapping")).toContainText("Level 4");

  // Guardrails: the de-identification and injection results come from the latest eval run.
  const deid = page.getByTestId("guardrail-live-phi_minimisation");
  await expect(deid).toContainText("recall");
  await expect(deid).toContainText(safety.guardrails.find((g: { id: string }) => g.id === "phi_minimisation").live.run_id);
  await expect(page.getByTestId("guardrail-live-injection")).toContainText("0 attack calls executed");

  const boundaries = page.getByTestId("safety-boundaries");
  await expect(boundaries).toContainText("All data is synthetic");
  await expect(boundaries).toContainText("commercial drug knowledge base");
  await expect(page.getByTestId("boundary-run-facts")).toContainText(`${safety.platform.runs.live_runs} on a real model`);
  await shot(page, "safety-01-tiers");
});
