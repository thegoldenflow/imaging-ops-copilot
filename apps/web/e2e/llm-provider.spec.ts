import { expect, test, type Page } from "@playwright/test";

// The header badge and the evaluations page name the model vendor. The demo API
// runs in mock mode, so the Gemini cases rewrite the API responses in the browser.

async function signInAs(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test("header shows mock mode from the API", async ({ page }) => {
  await signInAs(page, "admin");
  await expect(page.getByTestId("llm-mode")).toHaveText("AI: mock mode (no API key)");
});

test("header and evaluations show Gemini when the API runs on Gemini", async ({ page }) => {
  await page.route("**/api/meta", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), llm_mode: "gemini" } });
  });
  await page.route("**/api/evals", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.results.triage = { ...body.results.triage, mode: "gemini", model: "Gemini: gemini-3.8-flash" };
    body.results.protocol = { ...body.results.protocol, mode: "anthropic", model: "Claude: claude-sonnet-5-5" };
    await route.fulfill({ response, json: body });
  });

  await signInAs(page, "admin");
  await expect(page.getByTestId("llm-mode")).toHaveText("AI: Gemini API");

  await page.goto("/evals");
  await expect(page.getByTestId("eval-model-triage")).toHaveText("Gemini: gemini-3.8-flash");
  await expect(page.getByTestId("eval-model-protocol")).toHaveText("Claude: claude-sonnet-5-5");
  await expect(page.getByTestId("eval-model-extraction")).toHaveText("rule-based baseline");
});
