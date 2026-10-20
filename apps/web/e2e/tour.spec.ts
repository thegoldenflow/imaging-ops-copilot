import { expect, test, type Page } from "@playwright/test";

// The self-guided front door (demo/tour.md): a signed-out visitor lands on the landing page, takes the hospital
// manager's tour to its end screen, goes on to the engineer track, and finishes it, on a phone (375 px) and a desktop
// (1440 px). The tour is static (public/tour/, captured from the running app by scripts/tour/capture.mjs): not one
// /api request on the way. "Try it live" then opens today's sign-in page.

interface Step { id: string; chapter: string; caption: string; screen: string; note?: string }
interface Manifest { title: string; chapters: { id: string; title: string; role: { name: string } }[]; steps: Step[];
  end: { title: string; otherLabel: string }; other: string }

async function manifest(page: Page, track: string): Promise<Manifest> {
  const r = await page.request.get(`/tour/${track}.json`); // a static file, not /api
  expect(r.ok()).toBeTruthy();
  return r.json();
}

/** Every frame of a track: each chapter's role card, each hotspot (clicked, Next, or the arrow key), then the end. */
async function walk(page: Page, track: string, wide: boolean) {
  const m = await manifest(page, track);
  await expect(page).toHaveURL(new RegExp(`/tour/${track}$`));
  await expect(page.getByTestId("tour")).toHaveAttribute("data-track", track);
  await expect(page.getByTestId("tour-footer")).toContainText("Captured from the running app (version");
  await expect(page.getByTestId("tour-footer")).toContainText("all data synthetic");
  const viewport = page.viewportSize()!;
  let n = 0;
  for (const [c, chapter] of m.chapters.entries()) {
    const card = page.getByTestId("tour-role-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText(chapter.title);
    await expect(card).toContainText(`You are ${chapter.role.name}`);
    await expect(page.getByTestId("tour-progress")).toHaveText(`Chapter ${c + 1} of ${m.chapters.length}`);
    await page.getByTestId("tour-start-chapter").click();
    for (const step of m.steps.filter((s) => s.chapter === chapter.id)) {
      n += 1;
      await expect(page.getByTestId("tour-progress")).toHaveText(`Chapter ${c + 1} of ${m.chapters.length} · ${n}/${m.steps.length}`);
      await expect(page.getByTestId("tour-caption")).toHaveText(step.caption);
      await expect(page.getByTestId(wide ? "tour-callout" : "tour-sheet")).toBeVisible();
      if (step.note) await expect(page.getByTestId("tour-note")).toHaveText(step.note);
      const image = page.getByTestId("tour-image");
      await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
      // the hotspot is on screen, inside the stage
      const hotspot = page.getByTestId("tour-hotspot");
      await expect(hotspot).toBeVisible();
      const box = (await hotspot.boundingBox())!;
      const stage = (await page.getByTestId("tour-stage").boundingBox())!;
      expect(box.x + box.width / 2).toBeGreaterThan(0);
      expect(box.x + box.width / 2).toBeLessThan(viewport.width);
      expect(box.y + box.height / 2).toBeGreaterThan(stage.y);
      expect(box.y + box.height / 2).toBeLessThan(stage.y + stage.height);
      if (n === 2) { // Back returns to the previous hotspot, then forward again
        await page.getByTestId("tour-back").click();
        await expect(page.getByTestId("tour-progress")).toHaveText(`Chapter ${c + 1} of ${m.chapters.length} · 1/${m.steps.length}`);
        await page.keyboard.press("ArrowRight");
        await expect(page.getByTestId("tour-caption")).toHaveText(step.caption);
      }
      // three ways on: the hotspot itself, the Next button, the arrow key
      if (n % 3 === 0) await page.getByTestId("tour-next").click();
      else if (n % 3 === 1) await hotspot.click();
      else await page.keyboard.press("ArrowRight");
    }
  }
  const end = page.getByTestId("tour-end");
  await expect(end).toContainText(m.end.title);
  await expect(page.getByTestId("tour-progress")).toHaveText(`Done · ${m.steps.length}/${m.steps.length}`);
  await expect(page.getByTestId("tour-try-live")).toHaveAttribute("href", "/login");
  await expect(page.getByTestId("tour-other-track")).toHaveAttribute("href", `/tour/${m.other}`);
  await expect(page.getByTestId("tour-other-track")).toHaveText(m.end.otherLabel);
  await expect(page.getByTestId("tour-next")).toBeDisabled();
  return m;
}

for (const size of [{ width: 375, height: 812 }, { width: 1440, height: 900 }]) {
  test.describe(`${size.width} px`, () => {
    test.use({ viewport: size });

    test("landing page, both tracks to the end, with no /api request; then the sign-in page", async ({ page }) => {
      const api: string[] = [];
      page.on("request", (r) => { if (new URL(r.url()).pathname.startsWith("/api")) api.push(r.url()); });
      const wide = size.width >= 768;

      await page.goto("/");
      const landing = page.getByTestId("landing");
      await expect(landing).toContainText("All data is synthetic");
      const manager = await manifest(page, "manager");
      const engineer = await manifest(page, "engineer");
      expect(manager.steps.length).toBeLessThanOrEqual(12);
      expect(engineer.steps.length).toBeLessThanOrEqual(12);
      await expect(page.getByTestId("landing-manager")).toContainText(`${manager.steps.length} steps`);
      await expect(page.getByTestId("landing-engineer")).toContainText(`${engineer.steps.length} steps`);
      await expect(page.getByTestId("landing-live")).toHaveAttribute("href", "/login");

      await page.getByTestId("landing-manager").click();
      await walk(page, "manager", wide);
      await page.getByTestId("tour-other-track").click();
      await walk(page, "engineer", wide);
      expect(api, "the landing page and the tour are static").toEqual([]);

      await page.getByTestId("tour-try-live").click();
      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByTestId("login-admin")).toBeVisible(); // the live app's sign-in, with its Casey Brooks card
      await expect(page.getByText("AI tools for a hospital and its imaging centre")).toBeVisible();
    });
  });
}

test("Escape leaves the tour for the landing page", async ({ page }) => {
  await page.goto("/tour/engineer");
  await expect(page.getByTestId("tour-role-card")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("landing")).toBeVisible();
});
