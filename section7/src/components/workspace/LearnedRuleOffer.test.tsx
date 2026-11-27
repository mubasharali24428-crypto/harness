// Sprint 5 Task 37 (I-08, CD-02): the denial stop's rule offer is read-only.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LearnedRuleView, getLearnedRule, stoppedOnDenials, type LearnedRuleDto } from "./LearnedRuleOffer";

afterEach(() => vi.unstubAllGlobals());

const RULE = "LEARNED FROM DENIALS (run r1): include `deploy/.env` in the plan's scopePaths — these paths were repeatedly denied by the sandbox boundary, so future plans must request them up front.";
const data = (over: Partial<LearnedRuleDto> = {}): LearnedRuleDto => ({
  offer: { failureClass: "denials_repeated", paths: ["deploy/.env"], suggestedRule: RULE,
    recordedAt: "2026-11-25T03:00:00+00:00", status: "proposed" },
  similarCases: [{ caseId: "c1", runId: "r-old", outcome: "denied", score: 0.8, suggestedRule: RULE }],
  ...over,
});

describe("stoppedOnDenials", () => {
  it("is true only for an engine_failed step whose reason is the repeated-denial stop", () => {
    expect(stoppedOnDenials([{ seq: 1, type: "engine_failed", ts: null,
      reason: "denials_repeated after 9 tool calls. Suggested project rule — LEARNED…" }])).toBe(true);
    expect(stoppedOnDenials([{ seq: 1, type: "engine_failed", ts: null, reason: "deadline" }])).toBe(false);
    expect(stoppedOnDenials([{ seq: 1, type: "paused", ts: null, reason: "denials_repeated" }])).toBe(false);
    expect(stoppedOnDenials([])).toBe(false);
    expect(stoppedOnDenials(undefined)).toBe(false);
  });
});

describe("LearnedRuleView", () => {
  it("shows the rule read-only, says it is not saved, and links to the rules page", () => {
    const h = renderToStaticMarkup(<LearnedRuleView data={data()} onCopy={() => {}} />);
    expect(h).toContain('aria-label="Suggested project rule"');
    expect(h).toContain("not saved");
    expect(h).toContain("nothing changes unless a person adds it to the project rules");
    expect(h).toMatch(/<pre[^>]*aria-label="Suggested rule text"[^>]*>LEARNED FROM DENIALS \(run r1\)/);
    expect(h).toContain("<code>deploy/.env</code>");
    expect(h).toContain('href="/settings/project"');
    expect(h).toContain("Copy rule text");
    expect(h).toMatch(/learned-rule-similar.*Earlier run <code>r-old<\/code> \(denied\)/);
    // read-only: no editable control and no save/apply action anywhere in the offer
    expect(h).not.toMatch(/<textarea|<input|contenteditable/i);
    expect(h).not.toMatch(/>\s*(Save|Apply|Add rule)\b/);
  });

  it("renders nothing without an offer, and an honest note when the stop named no path", () => {
    expect(renderToStaticMarkup(<LearnedRuleView data={{ offer: null, similarCases: [] }} />)).toBe("");
    expect(renderToStaticMarkup(<LearnedRuleView data={data({ similarCases: [],
      offer: { ...data().offer!, suggestedRule: null, paths: [] } })} />)).toBe("");
    const h = renderToStaticMarkup(<LearnedRuleView data={data({
      offer: { ...data().offer!, suggestedRule: null, paths: [] } })} />);
    expect(h).toContain("there is no rule to suggest");
    expect(h).not.toContain("Copy rule text");
  });

  it("tells the person what happened after copy", () => {
    expect(renderToStaticMarkup(<LearnedRuleView data={data()} copy="copied" />))
      .toContain("Copied. Paste it into the project rules and save there.");
    expect(renderToStaticMarkup(<LearnedRuleView data={data()} copy="failed" />))
      .toContain("Could not copy. Select the text above instead.");
  });
});

describe("getLearnedRule", () => {
  it("makes exactly one GET to the run's learned-rule route and never writes", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify(data()), { status: 200 }));
    vi.stubGlobal("fetch", fn);
    const got = await getLearnedRule("r 1", "tok");
    expect(got.offer?.status).toBe("proposed");
    expect(fn).toHaveBeenCalledTimes(1);
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/orchestrator\/runs\/r%201\/learned-rule$/);
    expect(init.method ?? "GET").toBe("GET");
    expect(init.body).toBeUndefined();
  });
});
