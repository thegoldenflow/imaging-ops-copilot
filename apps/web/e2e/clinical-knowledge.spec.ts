import { expect, test, type Page } from "@playwright/test";

// Clinical knowledge Q&A: the requisition side panel and the full page, in mock mode
// (rule-based question reading and template answers over the in-memory graph).

async function signInAs(page: Page, role: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${role}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test.beforeAll(async ({ request }) => {
  const login = await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } });
  const { token } = await login.json();
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${token}` } });
});

test("radiologist looks up the clinical indication from a requisition", async ({ page }) => {
  await signInAs(page, "radiologist");
  await page.goto("/requisitions");
  await page.locator('[data-testid^="req-REQ-"]').first().click();
  const panel = page.getByTestId("kg-panel");
  await expect(panel).toBeVisible({ timeout: 20_000 });
  const question = panel.getByTestId("kg-question");
  await expect(question).not.toHaveValue("");  // prefilled with the extracted indication

  await question.fill("Swollen painful left calf for 2 days, query DVT.");
  await panel.getByTestId("kg-ask").click();
  await expect(panel.getByTestId("kg-statements")).toContainText("深静脉血栓");
  await expect(panel.getByTestId("kg-citation").first()).toBeVisible();
});

test("clinical knowledge page answers with citations, ranks diseases and says when the graph has nothing", async ({ page }) => {
  await signInAs(page, "medical_director");
  await page.getByRole("link", { name: "Clinical knowledge" }).click();
  await expect(page.getByRole("heading", { name: "Clinical knowledge" })).toBeVisible();

  await page.getByTestId("kg-question").fill("肺栓塞需要做哪些检查？");
  await page.getByTestId("kg-ask").click();
  await expect(page.getByTestId("kg-statements")).toContainText("胸部增强CT");
  await expect(page.getByTestId("kg-facts")).toContainText("核成像V/Q扫描");

  await page.getByTestId("kg-question").fill("Which diseases could present with chest pain and dyspnea?");
  await page.getByTestId("kg-ask").click();
  await expect(page.getByTestId("kg-answer")).toContainText("diseases ranked by matching symptoms");
  await expect(page.getByTestId("kg-facts")).toContainText("肺栓塞");

  await page.getByTestId("kg-question").fill("Symptoms of a meniscal tear");
  await page.getByTestId("kg-ask").click();
  await expect(page.getByTestId("kg-message")).toHaveText("The knowledge graph has no facts for this question.");
  await expect(page.getByText("Recent questions")).toBeVisible();
});

test("front desk has no access to clinical knowledge", async ({ page }) => {
  await signInAs(page, "front_desk");
  await expect(page.getByRole("link", { name: "Clinical knowledge" })).toHaveCount(0);
});
