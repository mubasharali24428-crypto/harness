"use client";

// M12-E4b — Review page as the REAL prId consumer (H-001 UI half + W2-C2).
// Acquisition precedence: ?pr= deep link → pr_opened bus event for the
// current run → session-remembered value written by THIS page. When none has
// produced a prId, PrEmptyState renders the honest pending copy — the old
// dead-end ("set harness.prId first" / hand-edit localStorage) is gone.
import { useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { approvePr, getReviewData, requestChanges, type ReviewDto } from "@/lib/api";
import { getUserId, getPrId, setPrId, getToken, getProjectId } from "@/lib/auth";
import { useEventStream } from "@/lib/useEventStream";
import {
  adoptEventPrId,
  prIdFromSearch,
  resolvePrId,
} from "./prSource";
import { PrEmptyState } from "./prEmptyState";

const RISKY_FILES = ["auth/", "payment/", "migrations", ".env", "secrets"];

export default function Review() {
  const [decision, setDecision] = useState<null | "approved" | "changes_requested">(null);
  const [liveReview, setLiveReview] = useState<ReviewDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // --- prId acquisition (W2-C2 consumer side) -----------------------------
  // Path 1: deep link /review?pr=<prId>, resolved once on mount. Also the
  // signal that the URL itself names the PR under review.
  const [deepLinkPrId, setDeepLinkPrId] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    const fromUrl = prIdFromSearch(window.location.search);
    if (fromUrl) {
      setDeepLinkPrId(fromUrl);
      // Persist ONLY what this page legitimately learned — no hand editing.
      setPrId(fromUrl);
    }
    setHydrated(true);
  }, []);

  // Path 3: value remembered in the session store (written by this page on an
  // earlier visit / deep link). Read after hydration so SSR markup matches.
  const [rememberedPrId, setRememberedPrId] = useState<string | null>(null);
  useEffect(() => {
    if (!deepLinkPrId && !hydrated) return;
    setRememberedPrId(getPrId());
  }, [deepLinkPrId, hydrated]);

  const projectId = getProjectId();
  const token = getToken();
  const { events } = useEventStream(projectId, token);

  // Path 2: subscribe to the bus; adopt prId from a pr_opened event matching
  // the current run. Producer is lane E4a (parallel) — payload shape per
  // W2-C2: {runId, prId, url}. Producer side UNVERIFIED pending ATLAS merge.
  const currentRunId = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const rid = events[i].runId;
      if (rid) return rid;
    }
    return null;
  }, [events]);
  const [eventPrId, setEventPrId] = useState<string | null>(null);
  useEffect(() => {
    const adopted = adoptEventPrId(deepLinkPrId ?? rememberedPrId, events, currentRunId);
    if (adopted && adopted !== eventPrId) {
      setEventPrId(adopted);
      setPrId(adopted); // remember it so refresh keeps the review loaded
    }
  }, [deepLinkPrId, rememberedPrId, events, currentRunId]); // eslint-disable-line react-hooks/exhaustive-deps -- eventPrId intentionally excluded: adoption guard lives inside

  const prId = deepLinkPrId ?? eventPrId ?? rememberedPrId;

  // Load review data whenever a prId becomes available.
  useEffect(() => {
    const t = getToken();
    if (!prId || !t) return;
    getReviewData(prId, t)
      .then((r) => {
        setLiveReview(r);
        setError(null);
      })
      .catch(() => {
        /* stay on honest empty state until the PR exists */
      });
  }, [prId]);

  const touchesRisky =
    liveReview?.changed_files?.some((f) =>
      RISKY_FILES.some((r) => f.path.toLowerCase().includes(r)),
    ) ?? false;

  async function decide(kind: "approved" | "changes_requested") {
    if (!prId) {
      setError("No pull request yet — it appears here the moment the agent opens one.");
      return;
    }
    const t = getToken();
    if (!t) {
      setError("Not signed in — go to /login first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const reviewer = getUserId() ?? "ui-user";
      if (kind === "approved") await approvePr(prId, reviewer, t);
      else await requestChanges(prId, [], reviewer, t);
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
          {liveReview ? `PR ${liveReview.prId}` : prId ? `PR ${prId} loading…` : "no PR yet"}
        </span>
      </h1>
      {!prId && <PrEmptyState />}
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
      {!liveReview && prId && !decision && (
        <p className="status-line">
          <span>PR {prId}</span>
          <span>loading diff…</span>
        </p>
      )}
      {error && <p style={{ color: "var(--red)" }}>{error}</p>}
    </AppShell>
  );
}
