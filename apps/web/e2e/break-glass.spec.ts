import { expect, test, type Page } from "@playwright/test";

// Hospital platform (spec 6.3): a Medicine A physician opens an ICU patient with
// break-glass (reason dialog, 4-hour grant, red banner), the administrator reviews
// the access and the decision lands in the audit log.

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

let icuMrn = "";

test.beforeAll(async ({ request }) => {
  const admin = await (await request.post("/api/auth/login", { data: { user_id: "U-ADMIN" } })).json();
  await request.post("/api/demo/reset", { headers: { Authorization: `Bearer ${admin.token}` } });
  // The bed manager's ICU census lists MRNs (no names): pick an ICU patient outside the physician's scope. Some ICU
  // stays are Medicine A admissions under the demo physician (ICU first, then the ward), which open without break-glass.
  const ops = await (await request.post("/api/auth/login", { data: { user_id: "U-OPS" } })).json();
  const census = await (await request.get("/api/hospital/census?unit=ICU", { headers: { Authorization: `Bearer ${ops.token}` } })).json();
  const doc = await (await request.post("/api/auth/login", { data: { user_id: "U-DOC-09" } })).json();
  for (const p of census.patients as { mrn: string | null; bed_id: string | null }[]) {
    if (!p.mrn || !p.bed_id) continue;
    const r = await request.get(`/api/hospital/patients/${p.mrn}`, { headers: { Authorization: `Bearer ${doc.token}` } });
    if (r.status() === 403 && (await r.json()).code === "break_glass_required") {
      icuMrn = p.mrn;
      break;
    }
  }
  expect(icuMrn, "an ICU inpatient outside the physician's scope").not.toBe("");
});

test("break-glass: reason dialog, banner, admin review, audit", async ({ page }) => {
  // 1. The physician works on Medicine A and sees that unit's census.
  await page.goto("/login");
  await page.getByTestId("login-physician").click();
  await expect(page.getByTestId("current-user")).toBeVisible();
  await page.getByRole("link", { name: "Patients" }).click();
  await expect(page.getByTestId("census")).toBeVisible();
  await expect(page.getByTestId("unit-select")).toHaveValue("MEDA");
  await expect(page.getByTestId("break-glass-banner")).toHaveCount(0);

  // 2. An ICU patient is closed to them; the server says break-glass would open it.
  await page.getByTestId("mrn-search").fill(icuMrn);
  await page.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.getByTestId("restricted")).toContainText(`MRN ${icuMrn} is outside your units`);
  await page.getByRole("button", { name: "Emergency access (break-glass)" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const open = dialog.getByRole("button", { name: "Open record for 4 hours" });
  await dialog.getByLabel("Reason for emergency access").fill("ICU call");
  await expect(open).toBeDisabled(); // fewer than 10 characters
  await expect(page.getByTestId("break-glass-count")).toContainText("at least 10 required");
  await dialog.getByLabel("Reason for emergency access").fill("Rapid response call on the ICU, covering for the attending");
  await shot(page, "bg-01-reason");
  await open.click();

  // 3. The record opens and the red banner stays while the grant lasts.
  await expect(page.getByTestId("chart-header")).toContainText(`MRN ${icuMrn}`);
  await expect(page.getByText(/Break-glass access until/)).toBeVisible();
  const banner = page.getByTestId("break-glass-banner");
  await expect(banner).toContainText("Emergency access (break-glass) active");
  await expect(banner).toContainText(`MRN ${icuMrn}`);
  await page.getByRole("link", { name: "Home" }).click();
  await expect(banner).toBeVisible(); // on every page
  await shot(page, "bg-02-banner");

  // 4. The administrator reviews the access; the decision goes to the audit log.
  await page.getByRole("button", { name: "Switch role" }).click();
  await page.getByTestId("login-admin").click();
  await page.getByRole("link", { name: "Break-glass review" }).click();
  const row = page.locator("li", { hasText: "Rapid response call on the ICU" });
  await expect(row).toContainText("access active");
  await expect(row).toContainText(/review by/);
  await shot(page, "bg-03-review");
  await row.getByPlaceholder("Review note (optional)").fill("Confirmed with the ICU charge nurse");
  await row.getByRole("button", { name: "Justified", exact: true }).click();
  await expect(page.getByText("Nothing to review")).toBeVisible();

  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByRole("tab", { name: "Break-glass" }).click();
  await expect(page.getByTestId("audit-break_glass")).toHaveCount(2); // the grant and its review
  await expect(page.getByTestId("audit-break_glass").first()).toContainText("justified");
  await expect(page.getByText(/Chain intact/)).toBeVisible();
  await shot(page, "bg-04-audit");
});
