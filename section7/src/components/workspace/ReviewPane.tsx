"use client";

// ReviewPane — the Diff pane with the verdict card above it. Same data and
// same verdict facts as the live review page; the reviewer reads recorded
// facts before the diff, and the risk flags / tests sit in the card.

import * as React from "react";
import type { RunView } from "./useRun";
import { DiffPane } from "./DiffPane";
import { reviewVerdictFacts } from "../../../app/review/verdictCard";

export function ReviewPane({ view }: { view: RunView }) {
  const { agg, review, reviewState } = view;
  if (!agg) return null;
  const facts = reviewVerdictFacts(reviewState === "ready" ? review : null, agg);
  return (
    <div className="ws-review" data-testid="preview-review">
      <section className="ws-verdict" aria-label="Verdict — recorded facts">
        <dl className="ws-verdict-grid">
          {facts.map((fact) => (
            <div key={fact.key} className={`ws-verdict-fact${fact.unknown ? " unknown" : ""}`} data-testid={`preview-verdict-${fact.key}`}>
              <dt>{fact.label}</dt>
              <dd>
                {fact.value}
                {fact.details.length > 0 ? (
                  <ul>
                    {fact.details.map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
        <p className="ws-verdict-note">Advisory summary of recorded facts. Not an approval to merge. A human merges.</p>
      </section>
      <DiffPane view={view} compact />
    </div>
  );
}
