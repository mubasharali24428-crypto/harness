"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { approvePr, getReviewData, requestChanges, type ReviewDto } from "@/lib/api";
import { getUserId, getPrId, getToken } from "@/lib/auth";

const RISKY_FILES = ["auth/", "payment/", "migrations", ".env", "secrets"];

export default function Review() {
  const [decision, setDecision] = useState<null | "approved" | "changes_requested">(null);
  const [liveReview, setLiveReview] = useState<ReviewDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const prId = getPrId();

  useEffect(() => {
    const token = getToken();
    if (!prId || !token) return;
    getReviewData(prId, token)
      .then((r) => {
        setLiveReview(r);
      })
      .catch(() => {
        /* stay on empty state until a PR exists */
      });
  }, [prId]);

  const touchesRisky =
    liveReview?.changed_files?.some((f) =>
      RISKY_FILES.some((r) => f.path.toLowerCase().includes(r)),
    ) ?? false;

  async function decide(kind: "approved" | "changes_requested") {
    if (!prId) {
      setError("No PR selected — set harness.prId first.");
      return;
    }
    const token = getToken();
    if (!token) {
      setError("Not signed in — go to /login first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const reviewer = getUserId() ?? "ui-user";
      if (kind === "approved") await approvePr(prId, reviewer, token);
      else await requestChanges(prId, [], reviewer, token);
      setDecision(kind);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell active="/review">
      <h1>
        Review Area{" "}
        <span className={`pill ${liveReview ? "green" : "yellow"}`}>
          {liveReview ? `PR ${liveReview.prId}` : "no PR loaded"}
        </span>
      </h1>
      {!prId && (
        <div className="card">
          <h2>No pull request selected</h2>
          <p>
            Open a PR from an approved run, then this area renders its diff, risk flags,
            and human gate.
          </p>
        </div>
      )}
      {liveReview && (
        <div className="grid">
          <div className="card" style={{ gridColumn: "1 / -1" }}>
            <h2>AI-Written PR Summary</h2>
            {liveReview.summary?.sections?.length ? (
              liveReview.summary.sections.map((s) => (
                <p key={s.label}>
                  <strong>{s.label}</strong> — {s.text}
                </p>
              ))
            ) : (
              <p>No AI summary stored for this PR yet.</p>
            )}
          </div>

          {liveReview.changed_files?.length > 0 && (
            <div className="card">
              <h2>Changed Files</h2>
              {liveReview.changed_files.map((f) => (
                <div className="status-line" key={f.path}>
                  <span>{f.path}</span>
                  <span>
                    +{f.additions} −{f.deletions}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="card">
            <h2>Risk Flags</h2>
            {liveReview.risk_flags?.length ? (
              <ul className="risk-list">
                {liveReview.risk_flags.map((f) => (
                  <li key={f.rule}>
                    {f.path} — {f.severity}: {f.reason}
                  </li>
                ))}
              </ul>
            ) : touchesRisky ? (
              <ul className="risk-list">
                <li>Possible sensitive-area touch detected</li>
              </ul>
            ) : (
              <p>
                No high-risk zones touched (<code>auth/</code>, payments, migrations,
                secrets).
              </p>
            )}
          </div>

          <div className="card">
            <h2>Human Gate</h2>
            <p>Merge happens on GitHub, by a human, manually. This app never merges.</p>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn primary" disabled={busy} onClick={() => void decide("approved")}>
                Approve
              </button>
              <button
                className="btn danger"
                disabled={busy}
                onClick={() => void decide("changes_requested")}
              >
                Request changes
              </button>
              <a className="btn" href={liveReview.pr_url} target="_blank" rel="noreferrer">
                Open on GitHub ↗
              </a>
            </div>
            {decision && (
              <p>
                Decision recorded:{" "}
                <span className={`pill ${decision === "approved" ? "green" : "red"}`}>
                  {decision}
                </span>{" "}
                — written to audit log via Section 1.
              </p>
            )}
          </div>
        </div>
      )}
      {error && <p style={{ color: "var(--red)" }}>{error}</p>}
    </AppShell>
  );
}
