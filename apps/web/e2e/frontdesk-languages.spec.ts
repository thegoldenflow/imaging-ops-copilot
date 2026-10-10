import { expect, test } from "@playwright/test";

// The imaging phone line in mock mode answers with its scripted fallback, in the caller's language (System 4).
// The phone page reads each reply with a browser voice in that language; the test records what the page asks the
// browser to say instead of speaking it.

type Spoken = { lang: string; text: string }[];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const spoken: { lang: string; text: string }[] = [];
    Object.defineProperty(window, "__spoken", { value: spoken });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: { speak: (u: SpeechSynthesisUtterance) => spoken.push({ lang: u.lang, text: u.text }), cancel: () => {} },
    });
  });
});

test("phone line: French and Chinese callers are answered and spoken to in their language", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("login-front_desk").click();
  await page.getByRole("link", { name: "Front desk" }).click();
  const say = async (text: string, expectReply: RegExp) => {
    await page.getByLabel("Caller says").fill(text);
    await page.getByLabel("Caller says").press("Enter");
    await expect(page.getByTestId("transcript")).toContainText(expectReply);
  };

  await page.getByTestId("start-call").click();
  await expect(page.getByTestId("call-language")).toHaveText("English");
  await say("Bonjour, quelle est l'adresse de votre centre Westbrook ?", /Le Westbrook Imaging Centre se trouve au/);
  await expect(page.getByTestId("call-language")).toHaveText("Français");
  await say("Merci, au revoir.", /Merci d'avoir appelé Lakeshore Imaging\. Au revoir !/);

  await page.getByTestId("start-call").click();
  await say("请问Northgate诊所的地址是什么？", /Northgate Imaging Centre的地址是/);
  await expect(page.getByTestId("call-language")).toHaveText("中文");

  const spoken = await page.evaluate(() => (window as unknown as { __spoken: Spoken }).__spoken);
  const voice = (pattern: RegExp) => spoken.find((s) => pattern.test(s.text))?.lang;
  expect(voice(/^Thank you for calling Lakeshore Imaging/)).toBe("en-CA"); // the greeting
  expect(voice(/se trouve au/)).toBe("fr-CA");
  expect(voice(/Au revoir !/)).toBe("fr-CA");
  expect(voice(/的地址是/)).toBe("zh-CN");
});
