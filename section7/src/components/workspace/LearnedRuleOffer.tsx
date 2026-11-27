"use client";

// Sprint 5 Task 37 (I-08, CD-02): when a run is stopped for repeated sandbox denials, the
// stop offers its lesson as a project rule a person can copy. The offer is READ-ONLY: this
// component makes one GET and never writes. Saving the rule stays the person's own edit on
// /settings/project (PUT /projects/{id}/rules); nothing is applied automatically.

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui";
import { call, root, type SimilarCase } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import type { RunStepDto } from "../../../app/runs/runsApi";

export interface LearnedRuleOfferDto {
  failureClass: string;
  paths: string[];
  suggestedRule: string | null;
  recordedAt: string | null;
  status: "proposed";
}

export interface LearnedRuleDto {
  offer: LearnedRuleOfferDto | null;
  similarCases: SimilarCase[];
}

/** The W27 guard's stop reason for repeated denials (loop_guard.DENIALS_REPEATED). */
export const DENIAL_STOP = "denials_repeated";

/** Pure: did this run stop on repeated denials? Only then is the lesson worth a request. */
export function stoppedOnDenials(steps: RunStepDto[] | null | undefined): boolean {
  return (steps ?? []).some((s) => s.type === "engine_failed" && (s.reason ?? "").startsWith(DENIAL_STOP));
}

export const getLearnedRule = (runId: string, token: string) =>
  call<LearnedRuleDto>(`${root(runId)}/learned-rule`, token);

type CopyState = "idle" | "copied" | "failed";

/** Presentational, SSR-safe: exported for static-render tests. */
export function LearnedRuleView({
  data,
  onCopy,
  copy = "idle",
}: {
  data: LearnedRuleDto;
  onCopy?: () => void;
  copy?: CopyState;
}) {
  const { offer, similarCases } = data;
  const rule = offer?.suggestedRule ?? null;
  if (!offer || (!rule && similarCases.length === 0)) return null;
  return (
    <section className="ws-next ws-learned" aria-label="Suggested project rule" data-testid="learned-rule">
      <p className="ws-next-question">This run stopped after repeated sandbox denials.</p>
      {rule ? (
        <>
          <p className="ws-next-recap">
            A project rule could stop the next plan from hitting the same wall. It is a suggestion only:
            it is <strong>not saved</strong>, and nothing changes unless a person adds it to the project rules.
          </p>
          <pre className="ws-approval-diff ws-learned-rule" tabIndex={0} aria-label="Suggested rule text"
            data-testid="learned-rule-text">{rule}</pre>
          {offer.paths.length > 0 && (
            <ul className="ws-approval-paths" aria-label="Paths the rule names">
              {offer.paths.map((p) => (
                <li key={p}><code>{p}</code></li>
              ))}
            </ul>
          )}
          <div className="ws-planpin-actions">
            {onCopy && (
              <Button size="sm" onClick={onCopy} testId="learned-rule-copy">Copy rule text</Button>
            )}
            <Link href="/settings/project" className="ws-next-link" data-testid="learned-rule-settings">
              Open project rules to add it yourself →
            </Link>
          </div>
          {copy !== "idle" && (
            <p role="status" className="ws-next-recap" data-testid="learned-rule-copy-status">
              {copy === "copied" ? "Copied. Paste it into the project rules and save there."
                : "Could not copy. Select the text above instead."}
            </p>
          )}
        </>
      ) : (
        <p className="ws-next-recap">The stop did not name a path, so there is no rule to suggest.</p>
      )}
      {similarCases.length > 0 && (
        <div data-testid="learned-rule-similar">
          <p className="ws-next-recap">Similar past denials in this team</p>
          <ul className="ws-approval-paths">
            {similarCases.slice(0, 3).map((c) => (
              <li key={c.caseId}>
                Earlier run <code>{c.runId}</code> ({c.outcome})
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/** Fetches the lesson only for a run that stopped on repeated denials; failures stay quiet
 *  (the step list already shows the stop reason). */
export function LearnedRuleOffer({ runId, token, steps }: { runId: string; token: string; steps: RunStepDto[] }) {
  const show = stoppedOnDenials(steps);
  const [data, setData] = React.useState<LearnedRuleDto | null>(null);
  const [copy, setCopy] = React.useState<CopyState>("idle");

  React.useEffect(() => {
    if (!show) return;
    let cancelled = false;
    void getLearnedRule(runId, token)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [runId, token, show]);

  if (!show || !data) return null;
  const rule = data.offer?.suggestedRule;
  const onCopy = rule
    ? () => {
      const done = (ok: boolean) => setCopy(ok ? "copied" : "failed");
      try {
        void navigator.clipboard.writeText(rule).then(() => done(true), () => done(false));
      } catch {
        done(false);
      }
    }
    : undefined;
  return <LearnedRuleView data={data} onCopy={onCopy} copy={copy} />;
}
