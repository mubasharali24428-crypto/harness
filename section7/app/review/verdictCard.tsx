"use client";

// U3 review verdict card — the Week 8 comment's facts, on screen before the
// diff. Pure text comes from src/lib/verdictFacts.ts; this component only
// presents recorded facts and marks every unknown in words. No approve
// shortcut and no auto-approval live here.
import * as React from "react";
import { Card } from "@/components/ui";
import { cssVar, fontSize, space } from "@/components/tokens";
import type { VerdictFact } from "@/lib/verdictFacts";

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
