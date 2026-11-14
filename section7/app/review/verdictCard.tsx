"use client";

// U3 review verdict card — the Week 8 comment's facts, on screen before the
// diff. Pure text comes from src/lib/verdictFacts.ts; this component only
// presents recorded facts and marks every unknown in words. No approve
// shortcut and no auto-approval live here.
import * as React from "react";
import { Card } from "@/components/ui";
import { cssVar, fontSize, space } from "@/components/tokens";
import { verdictFacts, type VerdictFact } from "@/lib/verdictFacts";
import type { ReviewDto } from "@/lib/api";
import type { RunAggregateDto } from "../runs/runsApi";

/** The card's facts from what the review service and the run aggregate recorded.
 *  Shared by the review page and the run page so both say the same thing. */
export function reviewVerdictFacts(
  liveReview: ReviewDto | null,
  runAgg: RunAggregateDto | null,
): VerdictFact[] {
  const flags = liveReview ? (liveReview.risk_flags ?? liveReview.riskFlags) : undefined;
  const testResults = liveReview?.testResults;
  return verdictFacts({
    plan: runAgg
      ? { approved: runAgg.planApproved ? true : null, approvedBy: null, approvedAt: null }
      : null,
    // No browser-callable sandbox fact exists yet (verdict_post.collect_facts
    // leaves it unrecorded); the card says so instead of guessing.
    sandbox: null,
    tests: liveReview
      ? Array.isArray(testResults)
        ? testResults.map((t) => ({ command: t.name, result: t.status }))
        : null
      : null,
    cost: runAgg
      ? { actualUsd: runAgg.costUsd, estimatedUsd: runAgg.estimatedCostUsd }
      : null,
    risk: liveReview
      ? {
          recorded: Array.isArray(flags),
          flags: (flags ?? []).map((f) => ({
            path: f.path,
            severity: f.severity,
            reason: f.reason,
          })),
        }
      : null,
    // The adversarial digest has no endpoint the browser can call (I-10 is
    // server-side only). Recorded concerns stay "not recorded" here.
    preReview: null,
  });
}

const muted = { color: cssVar("muted") } as const;

export function VerdictCard({ facts }: { facts: VerdictFact[] }) {
  return (
    <Card
      as="div"
      title="Verdict — recorded facts"
      testId="verdict-card"
      style={{ gridColumn: "1 / -1" }}
    >
      <p style={{ ...muted, margin: `0 0 ${space.md}px`, fontSize: fontSize.sm }}>
        Every line is a recorded fact. “Not recorded” is not a pass.
      </p>
      <dl
        style={{
          margin: 0,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: space.md,
        }}
      >
        {facts.map((fact) => (
          <div key={fact.key} style={{ minWidth: 0 }} data-testid={`verdict-${fact.key}`}>
            <dt
              style={{
                margin: 0,
                color: cssVar("muted"),
                fontSize: fontSize.xs,
                letterSpacing: 1,
                textTransform: "uppercase",
              }}
            >
              {fact.label}
            </dt>
            <dd
              style={{
                margin: `${space.xs}px 0 0`,
                fontSize: fontSize.md,
                color: fact.unknown ? cssVar("muted") : cssVar("text"),
                overflowWrap: "anywhere",
              }}
            >
              {fact.value}
              {fact.details.length > 0 && (
                <ul
                  style={{
                    margin: `${space.xs}px 0 0`,
                    paddingLeft: space.block,
                    color: cssVar("text2"),
                    fontSize: fontSize.sm,
                  }}
                >
                  {fact.details.map((detail) => (
                    <li key={detail} style={{ overflowWrap: "anywhere" }}>
                      {detail}
                    </li>
                  ))}
                </ul>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <p style={{ ...muted, margin: `${space.md}px 0 0`, fontSize: fontSize.xs }}>
        Advisory summary of recorded facts. Not an approval to merge. A human merges.
      </p>
    </Card>
  );
}
