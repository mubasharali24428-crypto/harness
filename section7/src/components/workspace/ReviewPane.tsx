"use client";

// ReviewPane — the Diff pane: verdict facts, the decision (R4), then the diff.
// A reviewer approves or requests changes here; a comment on a file is recorded
// on the review and, while the run is live, sent to the agent as direction.
// Every action is an existing review-service or redirect call.

import * as React from "react";
import type { RunView } from "./useRun";
import { DiffPane } from "./DiffPane";
import { reviewVerdictFacts } from "../../../app/review/verdictCard";
import { addReviewComment, approvePr, listReviewComments, requestChanges, type ReviewComment } from "@/lib/api";
import { getUserId } from "@/lib/auth";
import { loadErrorMessage } from "@/lib/humanize";
import { Button } from "@/components/ui";
import { joinRun, sendRedirect } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import { takesEffectText } from "../../../app/projects/[projectId]/runs/[runId]/collabModel";
import { composerMode } from "./runFlow";
import { approveBlock, commentAsDirection, reviewRisks, type DiffComment } from "./reviewFlow";

export function ReviewPane({ view, token, role }: { view: RunView; token: string; role?: string | null }) {
  const { agg, review, reviewState, runId } = view;
  const prId = agg?.prId ?? null;
  const [comments, setComments] = React.useState<ReviewComment[]>([]);
  const [acknowledged, setAcknowledged] = React.useState(false);
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [decision, setDecision] = React.useState<string | null>(null);
  const [status, setStatus] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // X-07 KPI: did the diff ever come on screen before Approve? Measured, not assumed.
  const diffRef = React.useRef<HTMLDivElement | null>(null);
  const [diffSeen, setDiffSeen] = React.useState(false);
  React.useEffect(() => {
    const el = diffRef.current;
    if (!el || diffSeen || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) setDiffSeen(true); },
      { threshold: 0.25 });
    io.observe(el);
    return () => io.disconnect();
  }, [diffSeen]);

  React.useEffect(() => {
    if (!prId) return;
    let alive = true;
    listReviewComments(prId, token)
      .then((c) => alive && setComments(c))
      .catch(() => {}); // the list is a convenience; the diff and decision still work
    return () => {
      alive = false;
    };
  }, [prId, token]);

  if (!agg) return null;
  const ready = reviewState === "ready" && review !== null && prId !== null;
  const facts = reviewVerdictFacts(reviewState === "ready" ? review : null, agg);
  const risks = reviewRisks(ready ? review : null);
  const blocked = approveBlock({ role, risks, acknowledged });
  const canDecide = approveBlock({ role, risks: [], acknowledged: true }) === null;

  async function decide(kind: "approve" | "changes") {
    if (!prId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const reviewer = getUserId() ?? "ui-user";
      if (kind === "approve") await approvePr(prId, reviewer, token, diffSeen);
      else await requestChanges(prId, [{ body: note.trim() }], reviewer, token);
      setDecision(kind === "approve"
        ? "Approved. Merging still happens on GitHub, by a person."
        : "Changes requested. Your note is recorded on the review.");
      setNote("");
    } catch (e) {
      setError(loadErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function comment(c: DiffComment) {
    if (!prId) return;
    setError(null);
    setStatus(null);
    try {
      const saved = await addReviewComment(prId, { file_path: c.path, line: c.line, body: c.body }, token);
      setComments((prev) => [...prev, saved]);
      if (runId && composerMode(agg!.state) === "direction") {
        await joinRun(runId, token).catch(() => {}); // steering needs a seat; already seated is fine
        const r = await sendRedirect(runId, token, { kind: "guidance", content: commentAsDirection(c), requestId: crypto.randomUUID() });
        setStatus(r.queued
          ? "Comment recorded. Queued: the person in control must accept it before the agent sees it."
          : `Comment recorded and sent to the agent. ${takesEffectText(r.takesEffect) ?? ""}`.trim());
      } else {
        setStatus("Comment recorded on the review. The run is not active, so it was not sent to the agent.");
      }
    } catch (e) {
      setError(loadErrorMessage(e));
      throw e; // keep the text in the form
    }
  }

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

      {ready && (
        <section className="ws-decide" aria-label="Your decision" data-testid="review-decide">
          <h3>Your decision</h3>
          {risks.length > 0 && (
            <>
              <ul data-testid="review-risks">
                {risks.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              {canDecide && (
                <label>
                  <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)}
                    data-testid="review-ack" />
                  I have read the risk flags above
                </label>
              )}
            </>
          )}
          {canDecide && (
            <label className="stack">
              What should change? (required to request changes)
              <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
          )}
          <div className="ws-decide-actions">
            <Button variant="primary" size="sm" disabled={busy || blocked !== null} onClick={() => void decide("approve")}
              testId="review-approve">
              Approve
            </Button>
            <Button variant="danger" size="sm" disabled={busy || !canDecide || note.trim() === ""}
              onClick={() => void decide("changes")} testId="review-request-changes">
              Request changes
            </Button>
          </div>
          {blocked && <p data-testid="review-blocked">{blocked}</p>}
          {decision && <p role="status">{decision}</p>}
          {status && <p role="status">{status}</p>}
          {error && <p role="alert">{error}</p>}
          {comments.length > 0 && (
            <>
              <h3>Comments ({comments.length})</h3>
              <ul data-testid="review-comments">
                {comments.map((c) => (
                  <li key={c.id}>
                    {c.file_path ? <code>{c.file_path}{c.line ? `:${c.line}` : ""}</code> : null} {c.body}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <div ref={diffRef}>
        <DiffPane view={view} compact onComment={ready && canDecide ? comment : undefined} />
      </div>
    </div>
  );
}
