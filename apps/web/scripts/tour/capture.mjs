// Captures the self-guided tour from the running app (demo/tour.md): plays the storyline's four highlights in a
// headless browser as the people who play them, has the demo's automation do the steps in between, then opens the
// engineer track's screens, and writes per hotspot a WebP screenshot, the target's box and the caption into
// apps/web/public/tour/<track>.json (images in public/tour/img/).
//
//   node scripts/tour/capture.mjs --api http://127.0.0.1:9441            # starts its own Vite against that API
//   node scripts/tour/capture.mjs --api http://127.0.0.1:9441 --web http://127.0.0.1:5197   # a running Vite
//   node scripts/tour/capture.mjs --api ... --from summary                # resume at a stage (no reset), keep the
//                                                                         # earlier shots (STAGES below; kept in STAGE)
//   node scripts/tour/capture.mjs --text-only                             # rewrite the captions, keep the shots
//
// The API must be a local one with the mock model and its own database: the capture resets the storyline (the English
// story) and walks it to the end. For the engineer track's workflow timeline the API runs with Temporal on (its own
// namespace); without Temporal that hotspot is left out and the capture says so. Setup: demo/tour.md.

import { chromium } from "@playwright/test";
import { execFileSync, spawn } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { checkScript, TRACKS } from "./script.mjs";

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = join(WEB_ROOT, "public", "tour");
// The shots stay here until the browser work is done: the Vite dev server reloads every open page when a file it
// serves from public/ changes (a rewritten image or manifest), which loses a page's state mid-step (casebook 10.11).
// node_modules is outside its watch. `--from` resumes from the shots kept here.
const STAGE = join(WEB_ROOT, "node_modules", ".cache", "tour-capture");
const VIEWPORT = { width: 1440, height: 900 };
const STAY = "stay-j0001";
const MRN = "40720001";
const FOLLOWUP = `FU-${STAY}`;
const CAST = { admin: "U-ADMIN", clerk: "U-CLER-01", ops: "U-OPS", surgeon: "U-SURG-02", nurse: "U-NURS-13" };

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};
const API = (opt("api", process.env.TOUR_API) ?? "http://127.0.0.1:9441").replace(/\/$/, "");
const TEXT_ONLY = args.includes("--text-only");
// The capture's stages in order. `--from <stage>` resumes there: no reset, the story as it stands (it must be at that
// stage), the earlier stages' shots kept from the last run's staging folder.
const STAGES = ["call", "bed", "summary", "followup", "trail", "engineer", "coding", "journey"];
const FROM = opt("from", "call");
if (!STAGES.includes(FROM)) throw new Error(`--from: one of ${STAGES.join(", ")}`);
const runs = (stage) => STAGES.indexOf(stage) >= STAGES.indexOf(FROM);

const t0 = Date.now();
const log = (m) => console.log(`[${String(Math.round((Date.now() - t0) / 1000)).padStart(4)} s] ${m}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- the API, as the people who play the parts ----------

const tokens = {};
async function token(user) {
  if (!tokens[user]) {
    const r = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: user }) });
    if (!r.ok) throw new Error(`login ${user}: ${r.status} ${await r.text()}`);
    tokens[user] = (await r.json()).token;
  }
  return tokens[user];
}

async function api(path, { method = "GET", user = CAST.admin, body, ok = true } = {}) {
  const r = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${await token(user)}`,
    ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined });
  if (ok && !r.ok) throw new Error(`${method} ${path}: ${r.status} ${await r.text()}`);
  return r.ok ? r.json() : null;
}

const story = () => api("/api/storyline");
const stepOf = async (n) => (await story()).steps[n - 1];

/** Poll `probe` until it returns a truthy value. */
async function until(what, probe, { timeout = 180_000, every = 2000 } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const v = await probe();
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(every);
  }
}

/** The demo's automation does the steps before `key` (jump) or the current step (autoplay), as their people. */
async function automate(kind, key) {
  const view = await story();
  const target = view.steps.find((x) => x.key === key);
  if (kind === "jump" && view.current === target.n) return; // already there (a resumed capture)
  const job = await api(`/api/storyline/steps/${key}/${kind}`, { method: "POST" });
  log(`automation (${kind} ${key}): steps ${job.steps.join(", ")}`);
  await until(`the automation to finish (${kind} ${key})`, async () => {
    const a = (await story()).autoplay;
    if (a?.id !== job.id) return false;
    if (a.status === "failed") throw new Error(`the automation stopped at ${a.current}: ${a.error}`);
    return a.status === "done";
  }, { timeout: 20 * 60_000, every: 3000 });
}

/** Fast-forward the hospital clock until step `n` is no longer waiting for its moment (`ready` for more). */
async function advanceUntil(n, ready = async (s) => s.status !== "waiting") {
  await until(`step ${n}'s moment`, async () => {
    const view = await story();
    if (await ready(view.steps[n - 1], view)) return true;
    if (view.job?.status !== "running") await api("/api/storyline/advance", { method: "POST", ok: false });
    return false;
  }, { timeout: 15 * 60_000, every: 3000 });
}

// ---------- the browser ----------

const HIDE = `
  [data-testid=demo-spotlight] { display: none !important; }   /* the way into demo mode: the tour draws its own */
  * { caret-color: transparent !important; }
`;

let browser;
let page;
let converter;
const shots = {};

async function signIn(user, path) {
  await page.evaluate(([t]) => { localStorage.setItem("ioc.token", t); localStorage.removeItem("ioc.demo"); },
    [await token(user)]);
  await page.goto(path);
  await page.addStyleTag({ content: HIDE });
}

/** The union of the locators' boxes (every element each one matches), in viewport coordinates. */
async function union(locators) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const l of [locators].flat()) {
    await l.first().waitFor({ state: "visible", timeout: 30_000 }); // a refetch may re-render it a moment
    const elements = await l.all();
    if (!elements.length) throw new Error(`nothing matches ${l}`);
    for (const e of elements) {
      const b = await e.boundingBox();
      if (!b) throw new Error(`no box for ${l}`);
      x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height);
    }
  }
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** The union, padded and clamped to the viewport, in whole pixels: the hotspot's box. */
async function boxOf(locators, pad = 6) {
  const u = await union(locators);
  const x0 = Math.max(0, u.x - pad), y0 = Math.max(0, u.y - pad);
  const x1 = Math.min(VIEWPORT.width, u.x + u.width + pad), y1 = Math.min(VIEWPORT.height, u.y + u.height + pad);
  if (x1 - x0 < 8 || y1 - y0 < 8) throw new Error(`target off screen: ${locators}`);
  return { x: Math.round(x0), y: Math.round(y0), width: Math.round(x1 - x0), height: Math.round(y1 - y0) };
}

/** Scroll the target's scrolling container (or the page) so the whole target is on screen, centred if it fits. */
async function bringIntoView(locators) {
  const first = [locators].flat()[0].first();
  await first.waitFor({ state: "visible", timeout: 60_000 });
  for (let i = 0; i < 3; i++) {
    const u = await union(locators);
    if (u.y >= 64 && u.y + u.height <= VIEWPORT.height - 16) return;
    const delta = Math.round(u.y - Math.max(72, (VIEWPORT.height - u.height) / 2));
    await first.evaluate((el, d) => {
      let p = el.parentElement;
      while (p && !(p.scrollHeight > p.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) {
        p = p.parentElement;
      }
      (p ?? document.scrollingElement).scrollBy(0, d);
    }, delta);
    await page.waitForTimeout(300);
  }
}

/** A viewport screenshot (WebP) with the named targets' boxes; the first target is brought on screen. */
async function shot(id, targets) {
  await bringIntoView(Object.values(targets)[0]);
  await page.waitForTimeout(600); // the page settles (fonts, a refetch's re-render)
  const boxes = {};
  for (const [name, l] of Object.entries(targets)) boxes[name] = await boxOf(l);
  const png = await page.screenshot({ animations: "disabled" });
  const file = `img/${id}.webp`;
  writeFileSync(join(STAGE, file), await webp(png));
  shots[id] = { image: file, boxes };
  writeFileSync(join(STAGE, "shots.json"), JSON.stringify(shots, null, 2)); // for a resumed run
  log(`shot ${id}: ${Object.entries(boxes).map(([k, b]) => `${k} ${b.width}x${b.height}@${b.x},${b.y}`).join(", ")}`);
}

/** PNG to WebP in the browser itself (a canvas), so the capture needs no image library. */
async function webp(png) {
  const url = await converter.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    c.getContext("2d").drawImage(img, 0, 0);
    return c.toDataURL("image/webp", 0.82);
  }, png.toString("base64"));
  if (!url.startsWith("data:image/webp")) throw new Error("this Chromium cannot encode WebP");
  return Buffer.from(url.split(",")[1], "base64");
}

/** Type a caller's line and send it; the call panel may re-render under the typing (a refetch), so fill again until
 * Send takes it, then wait for the agent's answer. */
async function say(box, text) {
  const input = page.getByRole("textbox", { name: box });
  const send = page.getByRole("button", { name: "Send" });
  const answering = page.getByText("The agent is answering…");
  await answering.waitFor({ state: "hidden", timeout: 60_000 });
  for (let attempt = 0; ; attempt++) {
    await input.fill(text, { timeout: 30_000 });
    try {
      await send.click({ timeout: 5000 });
      break;
    } catch (e) {
      if (attempt >= 5) throw e;
    }
  }
  await answering.waitFor({ state: "visible", timeout: 5000 }).catch(() => undefined); // it may already be done
  await answering.waitFor({ state: "hidden", timeout: 60_000 });
}

// ---------- the manager track: the four highlights, played in the browser ----------

async function stepCall(view) {
  log("step 1: the daughter's call (clerk)");
  await signIn(CAST.clerk, "/call-centre");
  await page.getByTestId("start-call").click();
  const [line1, line2] = view.patient.call_lines;
  await say("Type what the caller says…", line1);
  await say("Type what the caller says…", line2);
  const details = page.getByTestId("call-details");
  const verified = details.getByText("Verified", { exact: false }).first();
  await verified.waitFor({ timeout: 60_000 });
  // the identity check and who is calling (a `has:` locator is queried inside each candidate, so it is page-rooted)
  await shot("call-verified", { verified: details.locator(":scope > div").filter({ hasText: /^(Identity|Caller)/ }) });
  const card = page.getByTestId("call-requests").locator("li").filter({ hasText: MRN })
    .filter({ has: page.getByTestId("registration-request") });
  await shot("call-registration", { card });
  await card.getByRole("button", { name: "Confirm registration" }).click();
  await card.waitFor({ state: "detached", timeout: 30_000 });
  await page.getByRole("button", { name: "End call" }).click();
  await until("step 1 done", async () => (await stepOf(1)).done);
}

async function stepBed() {
  await automate("jump", "bed"); // steps 2-4
  log("step 5: Orthopedics full (bed manager)");
  await advanceUntil(5, async (s) => s.status !== "waiting" && (s.proposal?.ref ?? "").startsWith("FlowException/"));
  const exceptionId = (await stepOf(5)).proposal.ref.split("/")[1];
  await until("the Orthopedics exception to be high", async () => { // the Control Tower's sync follows her record
    const list = await api("/api/control-tower/exceptions", { user: CAST.ops });
    return list.exceptions.find((e) => e.id === exceptionId || e.key === "unit_occupancy:ORTH")?.severity === "high";
  });
  await signIn(CAST.ops, "/control-tower");
  const exception = page.locator(`[data-exception="${exceptionId}"]`);
  await exception.waitFor({ timeout: 60_000 });
  await shot("bed-exception", { exception });
  await exception.click();
  const drawer = page.getByTestId("action-drawer");
  await drawer.getByText("AI-generated").first().waitFor({ timeout: 120_000 });
  const narrative = drawer.getByTestId("narrative");
  await shot("bed-drawer", { narrative: [narrative, drawer.getByText("AI-generated").first()] });
  const rounds = drawer.getByTestId("action-overflow_discharge_rounds");
  if (!(await rounds.isVisible())) await drawer.getByText(/More actions from the engine/).click();
  if (!(await rounds.isChecked())) await rounds.check();
  const action = drawer.locator("li").filter({ has: page.getByTestId("action-overflow_discharge_rounds") }).first();
  await shot("bed-action", { action });
  await drawer.getByTestId("approve").click();
  await drawer.getByTestId("decision").getByText(/Approved by/).waitFor({ timeout: 30_000 });
  await page.keyboard.press("Escape");
  await advanceUntil(5, async (s) => s.done); // her bed: the first one the early discharges free
  const bed = (await stepOf(5)).detail.replace(/^bed /, "");
  log(`her bed: ${bed}`);
  await signIn(CAST.ops, "/control-tower");
  await page.getByTestId(`unit-${bed.split("-")[0]}`).first().click();
  await page.getByTestId(`bed-${bed}`).click();
  await shot("bed-hidden", { hidden: page.getByTestId("card-hidden") });
}

async function stepSummary() {
  await automate("jump", "summary"); // steps 6-8
  log("step 9: the discharge summary (surgeon)");
  await advanceUntil(9);
  await until("the discharge medication table to be final", async () => {
    const list = await api("/api/documentation/worklist", { user: CAST.surgeon });
    return list.items.find((i) => i.encounter_id === STAY)?.discharge_table === "final";
  }, { timeout: 5 * 60_000 });
  await signIn(CAST.surgeon, "/documentation");
  await page.getByTestId(`work-${STAY}`).click();
  const draft = page.getByTestId("draft-summary");
  const summary = page.getByTestId("summary-review");
  await draft.or(summary).first().waitFor({ timeout: 60_000 });
  if (await draft.isVisible()) await draft.click();
  await summary.waitFor({ timeout: 120_000 });
  // a traceable medicine line, selected: the source panel highlights what it cites
  const item = summary.locator('[data-testid="summary-item"][data-status="ok"]').filter({ hasText: /warfarin/i }).first();
  await item.click();
  const source = page.locator('[data-testid="source"][data-highlighted="true"]').first();
  await source.waitFor({ timeout: 30_000 });
  await shot("summary-sources", { item, source });
  await item.click(); // unselect
  const red = summary.locator('[data-testid="summary-item"][data-status="red"]').filter({ hasText: /lisinopril/i }).first();
  await shot("summary-blocked", { red });
  // signing is blocked, and why (under the draft, not on screen with the medicines)
  await shot("summary-why", { why: [page.getByTestId("sign-summary"), page.getByTestId("why-not")] });
  const path = await red.getAttribute("data-path"); // in the editor its name is an input's value, not text
  await page.getByTestId("edit-summary").click();
  await summary.locator(`[data-testid="summary-item"][data-path="${path}"]`).getByTestId("remove-item").click();
  await page.getByTestId("save-summary").click();
  const bar = page.getByTestId("validation-bar");
  await bar.getByText("Every item traces to its sources").waitFor({ timeout: 60_000 });
  await page.getByTestId("sign-summary").click();
  const signed = page.getByTestId("signed");
  await signed.waitFor({ timeout: 60_000 });
  await shot("summary-signed", { signed: [signed, page.getByText(/^Edited \d+ time/)] });
  await until("step 9 done", async () => (await stepOf(9)).done);
}

async function stepFollowup(view) {
  await automate("jump", "followup"); // step 10
  log("step 11: the follow-up call (ward nurse)");
  await advanceUntil(11);
  await until("her follow-up call to be scheduled", async () => {
    const call = await api(`/api/voice/followups/${FOLLOWUP}`, { user: CAST.nurse, ok: false });
    return call?.status === "scheduled";
  }, { timeout: 15 * 60_000 }); // with Temporal, her journey's timer schedules it: minutes behind on a loaded worker
  await signIn(CAST.nurse, `/follow-up?call=${FOLLOWUP}`);
  await page.getByTestId("place-call").click();
  const transcript = page.getByTestId("followup-call-transcript");
  await transcript.locator("div").first().waitFor({ timeout: 60_000 });
  const box = "Type what the patient says…";
  const [birth, wound] = view.patient.followup_lines;
  await say(box, birth);
  await say(box, wound);
  const escalated = transcript.locator(":scope > div").filter({ hasText: /call you back within 30 minutes/ }).last();
  await escalated.waitFor({ timeout: 60_000 });
  const words = transcript.locator(":scope > div").filter({ hasText: /soaked through/ }).last();
  await shot("followup-call", { escalation: [words, escalated] });
  // the remaining questions (medicines, follow-ups, red flags): "nothing else" until the call ends
  const ended = page.getByTestId("followup-ended");
  for (let i = 0; i < 8; i++) {
    await page.getByRole("textbox", { name: box }).or(ended).first().waitFor({ timeout: 30_000 });
    if (await ended.isVisible()) break;
    await say(box, view.patient.followup_closing ?? "No, nothing else.");
  }
  await ended.waitFor({ timeout: 30_000 });
  await page.getByRole("tab", { name: "Queue" }).click();
  const top = page.getByTestId("followup-list").locator("li").first();
  await top.getByText(/Heavy wound drainage/).waitFor({ timeout: 60_000 });
  await top.click();
  await page.getByTestId("patient-words").waitFor({ timeout: 30_000 });
  await page.getByRole("textbox", { name: "Nurse note" }).fill("Called back: wound reviewed tomorrow at the fracture clinic");
  await page.getByRole("button", { name: "Save call-back note" }).click();
  await page.getByTestId("nurse-actions").getByText("Call-back note").waitFor({ timeout: 30_000 });
  await shot("followup-queue", { top });
  await until("step 11 done", async () => (await stepOf(11)).done);
}

// ---------- the engineer track: the screens behind the same story ----------

/** Before step 12 (so the audit log ends with the story's follow-up): step 1's trail and its audit event. */
async function trailScreens() {
  log("the engineer track's screens (administrator)");
  await signIn(CAST.admin, "/storyline");
  const step1 = page.getByTestId("story-step-1");
  await step1.waitFor({ timeout: 60_000 });
  if (!(await step1.getByTestId("say-1").isVisible())) await step1.locator("button").first().click();
  await shot("story-trail", { trail: [page.getByTestId("story-proposal-1"), page.getByTestId("story-signed-1"),
    page.getByTestId("story-audit-1")] });

  // her events only (the MRN filter: matched by its keyed hash), the approvals; the clerk's confirmation of step 1
  await signIn(CAST.admin, "/audit");
  await page.getByTestId("audit-mrn").fill(MRN);
  await page.getByRole("tab", { name: "Approvals" }).click();
  const row = page.locator('[data-testid="audit-approve"]').filter({ hasText: "Maya Nakamura" })
    .filter({ hasText: "call_center" }).first();
  await shot("audit-approvals", { row });
}

/** An agent's trace, the registries, the evals. */
async function engineerScreens() {
  // the discharge summary's run (step 9): the surgeon's draft (not a workflow's system read of the same agent), found
  // through the API because "Recent runs" lists only the newest 40; AI Ops opens a trace from the URL
  const { runs: drafts } = await api("/api/agents/runs?agent_id=discharge_summary&limit=200");
  const draft = drafts.find((r) => r.actor_role === "physician" && r.kind !== "call");
  if (!draft) throw new Error("no discharge summary run by the surgeon");
  await signIn(CAST.admin, `/ai-ops?trace=${draft.run_id}`);
  const trace = page.getByTestId("aiops-trace");
  await trace.getByText(/^sign by /).waitFor({ timeout: 30_000 }); // the human decision that followed
  await shot("agent-trace", { trace });
  const tools = page.getByTestId("aiops-trace-tool-call").first().locator("xpath=ancestor::table[1]");
  await shot("agent-tools", { tools: [tools.locator("thead th:nth-child(-n+3)"),  // tool, level, status
    tools.getByTestId("aiops-trace-tool-call").locator("td:nth-child(-n+3)")] });

  await signIn(CAST.admin, "/safety");
  const tiers = page.getByTestId("tier-ops").locator("xpath=ancestor::*[contains(@class,'rounded-xl')][1]");
  await shot("safety-tiers", { tiers });
  await shot("safety-reach", { reach: [page.getByTestId("safety-levels"), page.getByTestId("safety-reach")] });

  await signIn(CAST.admin, "/ai-ops?tab=evals");
  await shot("evals-agents", { agents: page.getByText("One runner for every agent", { exact: false }).first() });
  await signIn(CAST.admin, "/ai-ops?tab=registry");
  await shot("evals-prompts", { gate: [page.getByText("The registry's prompt_version is the pointer", { exact: false }),
    page.getByText(/^(prompt gate passes|\d+ failing the gate)$/).first()] });
}

/** After step 12: her journey's workflow timeline (only with Temporal on). */
async function journeyScreen(view) {
  if (!(view.temporal?.status?.online && view.journey)) {
    log("Temporal is off: the journey's timeline (engineer hotspot 'journey') is left out of this capture");
    return;
  }
  await until("her journey to complete", async () => (await story()).journey?.status === "completed",
    { timeout: 5 * 60_000 });
  await signIn(CAST.admin, `/workflows/${encodeURIComponent(view.journey.id)}`);
  // its first sign-off steps (each waited for a person's decision: a signal)
  await shot("journey", { timeline: page.getByTestId("timeline").locator(":scope > li:nth-child(-n+3)") });
}

// ---------- the manifests ----------

function version() {
  try {
    const sha = execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: WEB_ROOT }).toString().trim();
    const dirty = execFileSync("git", ["status", "--porcelain", "--", "src"], { cwd: WEB_ROOT }).toString().trim();
    return dirty ? `${sha}+` : sha;
  } catch {
    return "unknown";
  }
}

function manifest(key, meta, previous) {
  const t = TRACKS[key];
  const steps = [];
  for (const s of t.steps) {
    const shotOf = shots[s.shot] ?? previous?.steps.find((p) => p.id === s.id);
    const image = shotOf?.image;
    const box = shotOf?.boxes ? shotOf.boxes[s.target] : shotOf?.box;
    if (!image || !box) continue; // not taken (yet): left out
    steps.push({ id: s.id, chapter: s.chapter, screen: s.screen, image, box, caption: s.caption,
      ...(s.note ? { note: s.note } : {}) });
  }
  const chapters = t.chapters.filter((c) => steps.some((s) => s.chapter === c.id));
  return { track: key, title: t.title, audience: t.audience, intro: t.intro, other: t.other, end: t.end,
    captured: meta, viewport: VIEWPORT, chapters, steps };
}

let meta = null; // this run's: set once the browser is up

/** Publish: the shots' images into public/tour/img (stale ones removed) and the two manifests. */
function publish() {
  mkdirSync(join(OUT, "img"), { recursive: true });
  const used = new Set(Object.values(shots).map((x) => x.image));
  for (const image of used) copyFileSync(join(STAGE, image), join(OUT, image));
  for (const f of readdirSync(join(OUT, "img"))) if (!used.has(`img/${f}`)) rmSync(join(OUT, "img", f));
  for (const key of Object.keys(TRACKS)) {
    const m = manifest(key, meta, null);
    writeFileSync(join(OUT, `${key}.json`), `${JSON.stringify(m, null, 2)}\n`);
    log(`wrote public/tour/${key}.json: ${m.chapters.length} chapters, ${m.steps.length} hotspots`);
    for (const t of TRACKS[key].steps.filter((x) => !m.steps.some((y) => y.id === x.id))) log(`  left out: ${t.id}`);
  }
}

async function main() {
  const problems = checkScript();
  if (problems.length) throw new Error(`the tour script breaks its limits:\n  ${problems.join("\n  ")}`);
  const previous = (key) => {
    const f = join(OUT, `${key}.json`);
    return existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : null;
  };
  if (TEXT_ONLY) {
    for (const key of Object.keys(TRACKS)) {
      const prev = previous(key);
      if (!prev) throw new Error(`--text-only needs an earlier capture (public/tour/${key}.json)`);
      writeFileSync(join(OUT, `${key}.json`), `${JSON.stringify(manifest(key, prev.captured, prev), null, 2)}\n`);
      log(`rewrote the text of public/tour/${key}.json`);
    }
    return;
  }
  if (FROM === "call") rmSync(STAGE, { recursive: true, force: true });
  else if (existsSync(join(STAGE, "shots.json"))) Object.assign(shots, JSON.parse(readFileSync(join(STAGE, "shots.json"), "utf8")));
  else throw new Error(`--from ${FROM}: no earlier shots to resume from (${STAGE})`);
  mkdirSync(join(STAGE, "img"), { recursive: true });

  let vite;
  let web = opt("web", process.env.TOUR_WEB);
  if (!web) {
    const port = opt("port", "5199");
    web = `http://127.0.0.1:${port}`;
    vite = spawn(process.execPath, [join(WEB_ROOT, "node_modules", "vite", "bin", "vite.js"), "--host", "127.0.0.1",
      "--port", port, "--strictPort"], { cwd: WEB_ROOT, env: { ...process.env, VITE_API_PROXY_TARGET: API }, stdio: "ignore" });
    await until("Vite", async () => fetch(web).then((r) => r.ok).catch(() => false), { timeout: 60_000, every: 1000 });
  }
  try {
    const health = await (await fetch(`${API}/api/health`)).json();
    if (health.llm_mode !== "mock") throw new Error(`the API runs model mode ${health.llm_mode}: capture against the mock`);
    if (FROM === "call") {
      log(`reset: the English story (about a minute) on ${API}`);
      await api("/api/storyline/reset", { method: "POST", body: { language: "en" } });
    } else {
      log(`resuming at ${FROM} on ${API} (no reset)`);
    }
    const view = await story();
    if (view.patient?.language !== "en") throw new Error("the story is not the English one (reset with language en)");
    const today = new Date(); // the capture's local date (toISOString would give UTC's)
    const at = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    meta = { at, version: version(), model: "mock",
      temporal: Boolean(view.temporal?.status?.online), story: "en" };

    browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM_PATH || undefined });
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, baseURL: web, locale: "en-CA",
      timezoneId: "America/Toronto", colorScheme: "light", reducedMotion: "reduce" });
    page = await context.newPage();
    converter = await context.newPage();
    await page.goto("/login");

    if (runs("call")) await stepCall(view);
    if (runs("bed")) await stepBed();
    if (runs("summary")) await stepSummary();
    if (runs("followup")) await stepFollowup(view);
    if (runs("trail")) await trailScreens();
    if (runs("engineer")) await engineerScreens();
    if (runs("coding") && (await story()).current === 12) await automate("autoplay", "coding"); // step 12
    const done = await story();
    if (done.current !== null) throw new Error(`the story is not finished: step ${done.current}`);
    await journeyScreen(done);

    publish();
    if (!meta.temporal) log(`note: Temporal was off, so the journey's timeline is not in the tour`);
  } finally {
    await browser?.close();
    vite?.kill();
  }
  log("done");
}

main().catch((e) => {
  console.error(e);
  browser?.close();
  process.exit(1);
});
