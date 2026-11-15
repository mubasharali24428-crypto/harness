/**
 * Keyboard + accessibility-tree journey on the RC stack: sign in, open the demo
 * run, propose a plan, approve it. At each step it records what the browser
 * exposes to assistive technology: the focused element's role and accessible
 * name after every key press, and the page's live-region text.
 *
 * This is NOT a screen-reader recording. It shows what a screen reader would be
 * given, not how VoiceOver or NVDA speak it. Run: node scripts/sr-journey.mjs
 */
import { chromium } from "@playwright/test";
import { writeFileSync } from "node:fs";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3011";
const out = [];
const findings = [];
const say = (line) => { out.push(line); console.log(line); };

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();

async function focused() {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const label = el.getAttribute("aria-label")
      || (el.getAttribute("aria-labelledby") && document.getElementById(el.getAttribute("aria-labelledby"))?.textContent)
      || (el.id && document.querySelector(`label[for="${el.id}"]`)?.textContent)
      || el.closest("label")?.textContent
      || el.textContent
      || el.getAttribute("placeholder")
      || el.getAttribute("title") || "";
    const role = el.getAttribute("role") || { A: "link", BUTTON: "button", TEXTAREA: "textbox", INPUT: el.type === "checkbox" ? "checkbox" : "textbox", SELECT: "combobox", SUMMARY: "disclosure" }[el.tagName] || el.tagName.toLowerCase();
    return { role, name: label.trim().replace(/\s+/g, " ").slice(0, 90), disabled: el.disabled === true };
  });
}
async function key(k, why) {
  await page.keyboard.press(k);
  await page.waitForTimeout(150);
  const f = await focused();
  say(`| \`${k}\` | ${why ?? ""} | ${f ? `${f.role}: "${f.name}"${f.disabled ? " (disabled)" : ""}` : "nothing focused (body)"} |`);
  if (f && !f.name) findings.push(`A focused ${f.role} has no accessible name (after ${k}${why ? `, ${why}` : ""}).`);
  if (!f) findings.push(`Focus fell to the page body after ${k}${why ? ` (${why})` : ""}.`);
  return f;
}
async function tabTo(match, why, max = 40) {
  for (let i = 0; i < max; i++) {
    const f = await key("Tab", i === 0 ? why : "");
    if (f && match(f)) return i + 1;
  }
  findings.push(`Could not reach "${why}" within ${max} Tab presses.`);
  return -1;
}
async function live() {
  const t = await page.evaluate(() => [...document.querySelectorAll('[role=status],[role=alert],[aria-live]')].map((e) => e.textContent.trim()).filter(Boolean).join(" / "));
  say(`\nLive regions now read: ${t ? `"${t.slice(0, 300)}"` : "(nothing)"}\n`);
}
const section = (title) => { say(`\n### ${title}\n`); say("| Key | Step | Focus lands on (role: name) |"); say("|---|---|---|"); };

section("1. Sign in");
await page.goto(`${BASE}/login`);
await page.waitForLoadState("networkidle");
say(`Page title: "${await page.title()}". Headings: ${JSON.stringify(await page.getByRole("heading").allInnerTexts())}`);
say("\n| Key | Step | Focus lands on (role: name) |\n|---|---|---|");
await tabTo((f) => f.role === "textbox", "reach the email field");
await page.keyboard.type(`sr-journey-${Date.now().toString(36)}@harness.dev`);
await key("Enter", "submit the form");
await page.waitForLoadState("networkidle");
await live();

section("2. Seed the demo project (a fresh team has none)");
await page.goto(`${BASE}/demo`);
await page.waitForLoadState("networkidle");
await tabTo((f) => /demo/i.test(f.name) && f.role === "button", "reach the demo button");
await key("Enter", "start the demo");
await page.waitForURL(/\/runs\//, { timeout: 60000 });
await page.waitForLoadState("networkidle");
say(`\nLanded on: ${new URL(page.url()).pathname}${new URL(page.url()).search}. Headings: ${JSON.stringify((await page.getByRole("heading").allInnerTexts()).slice(0, 8))}`);
await live();

section("3. New run: type a goal, propose a plan");
await page.goto(`${BASE}/`);
await page.waitForLoadState("networkidle");
const tabsToComposer = await tabTo((f) => f.role === "textbox" && /goal/i.test(f.name), "reach the goal input");
say(`\nTab presses from the top of the page to the goal input: ${tabsToComposer}`);
if (tabsToComposer > 12) findings.push(`The goal input is ${tabsToComposer} Tab presses from the top of the page; the skip link shortens this only if used.`);
await page.keyboard.type("Add a docstring to word_count");
say("\n| Key | Step | Focus lands on (role: name) |\n|---|---|---|");
await key("Enter", "propose the plan");
await page.waitForURL(/\/runs\/run-/, { timeout: 60000 });
await page.waitForLoadState("networkidle");
say(`\nLanded on the run. Headings: ${JSON.stringify((await page.getByRole("heading").allInnerTexts()).slice(0, 8))}`);
const f0 = await focused();
say(`Focus after the page change: ${f0 ? `${f0.role}: "${f0.name}"` : "nothing focused (body)"}`);
if (!f0) findings.push("After proposing a plan the new run page opens with focus on the page body; a screen-reader user is not told the page changed or where the plan is.");
await live();

section("4. Approve the plan");
const n = await tabTo((f) => f.role === "button" && /approve plan/i.test(f.name), "reach Approve plan", 60);
say(`\nTab presses to reach Approve plan: ${n}`);
if (n > 20) findings.push(`Approve plan is ${n} Tab presses from the top of the run page.`);
say("\n| Key | Step | Focus lands on (role: name) |\n|---|---|---|");
await key("Enter", "approve");
await page.getByTestId("plan-start").waitFor({ timeout: 30000 });
const f1 = await focused();
say(`\nAfter approval, focus is on: ${f1 ? `${f1.role}: "${f1.name}"` : "nothing focused (body)"}`);
if (!f1) findings.push("After Approve plan the button is replaced by Start run and focus falls to the page body; the user must find Start run again.");
await live();
const liveText = await page.evaluate(() => [...document.querySelectorAll('[role=status],[aria-live]')].map((e) => e.textContent.trim()).join(" "));
if (!/approv/i.test(liveText)) findings.push("Nothing in a live region says the plan was approved; the change is visual only (the button becomes Start run and the pin reads 'approved, not started').");

const md = `# Accessibility-tree journey: sign in to plan approval — 2026-11-15

Produced by \`section7/scripts/sr-journey.mjs\` against ${BASE} (Chromium, keyboard only).

**This is not a screen-reader recording.** It records what the browser exposes to assistive
technology (focus order, roles, accessible names, live-region text). How VoiceOver or NVDA speak
it still needs a person. The plan's "recorded manual screen-reader journey" is therefore **not met**.

## Findings

${findings.length ? findings.map((f) => `- ${f}`).join("\n") : "- None from this automated pass."}

## Journey
${out.join("\n")}
`;
writeFileSync("../docs/briefs/evidence/ui/sr-journey-2026-11-15.md", md);
console.log(`\nfindings: ${findings.length}`);
await browser.close();
