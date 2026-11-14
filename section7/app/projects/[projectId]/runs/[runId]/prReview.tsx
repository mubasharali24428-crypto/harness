"use client";

// R0 (plan §8, F1/F2): the run's own pull request, on the run page. The verdict
// card and the diff come from the review service for the run's prId, so a run
// with no sandbox snapshots (the demo replay) still shows what changed.
// Approving stays on the review page; this block only reads.

import * as React from "react";
import { getReviewData, type ReviewDto } from "@/lib/api";
import { lineTone } from "@/lib/hunkReview";
import { ErrorState, LoadingState } from "@/components/States";
import { cssVar, fontSize, radius, space } from "@/components/tokens";
import type { RunAggregateDto } from "../../../../runs/runsApi";
import { VerdictCard, reviewVerdictFacts } from "../../../../review/verdictCard";

const TONE_COLOR = {
  add: cssVar("green"),
  del: cssVar("red"),
  meta: cssVar("muted"),
  context: cssVar("text"),
} as const;

export function PrDiff({ diff }: { diff: string }) {
  return (
    <pre
      data-testid="pr-diff"
      aria-label="Pull request diff"
      tabIndex={0}
      style={{
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: fontSize.sm,
        background: cssVar("panel2"),
        border: `1px solid ${cssVar("border")}`,
        borderRadius: radius.md,
        padding: space.md,
        overflowX: "auto",
        margin: `${space.md}px 0`,
      }}
    >
      {diff.split("\n").map((line, i) => (
        <span key={i} style={{ display: "block", color: TONE_COLOR[lineTone(line)] }}>
          {line || " "}
        </span>
      ))}
    </pre>
  );
}

export function PrReview({ prId, token, agg }: { prId: string; token: string; agg: RunAggregateDto }) {
  const [review, setReview] = React.useState<ReviewDto | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadKey, setReloadKey] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    setError(null);
    getReviewData(prId, token)
      .then((r) => !cancelled && setReview(r))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [prId, token, reloadKey]);

  return (
    <section aria-labelledby="pr-review-heading" data-testid="pr-review">
      <h2 id="pr-review-heading">Pull request</h2>
      {error && (
        <ErrorState
          title="The pull request review didn’t load"
          message={error}
          onRetry={() => setReloadKey((k) => k + 1)}
        />
      )}
      {!error && !review && <LoadingState label="Loading the pull request…" rows={4} />}
      {review && (
        <>
          <VerdictCard facts={reviewVerdictFacts(review, agg)} />
          {review.diff ? (
            <PrDiff diff={review.diff} />
          ) : (
            <p style={{ color: cssVar("muted") }}>The review service recorded no diff for this pull request.</p>
          )}
          <a className="btn" href={`/review?pr=${encodeURIComponent(prId)}`}>
            Open the review to approve or request changes
          </a>
        </>
      )}
    </section>
  );
}
