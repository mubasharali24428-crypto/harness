"use client";

// M12-E4b — Review page as the REAL prId consumer (H-001 UI half + W2-C2).
// Acquisition precedence: ?pr= deep link → pr_opened bus event for the
// current run → session-remembered value written by THIS page. When none has
// produced a prId, PrEmptyState renders the honest pending copy — the old
// dead-end ("set harness.prId first" / hand-edit localStorage) is gone.
import { useEffect, useMemo, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too — same as meeting/page.
import * as React from "react";
import AppShell from "@/components/AppShell";
import { ApiError, approvePr, getReviewData, requestChanges, type ReviewDto } from "@/lib/api";
import { getUserId, getPrId, setPrId, getToken, getProjectId } from "@/lib/auth";
import { useEventStream } from "@/lib/useEventStream";
import {
  adoptEventPrId,
  prIdFromSearch,
  resolvePrId,
  runIdForPr,
} from "./prSource";
import { PrEmptyState } from "./prEmptyState";
// M16-X4 (raw-17): markup migrated onto the shared DS primitives — Card /
// Button / Pill replace the class-only shells and the tinted error line moved
// onto the proposed Notice primitive. Visual parity intent; the .pill.green /
// .pill.yellow / .pill.red class hooks the e2e journey keys on are preserved
// verbatim.
import { Button, Card, Pill } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { getRunAggregate, type RunAggregateDto } from "../runs/runsApi";
import { VerdictCard, reviewVerdictFacts } from "./verdictCard";

const RISKY_FILES = ["auth/", "payment/", "migrations", ".env", "secrets"];

// Hoisted so the M16-X4 zero-inline-style audit stays meaningful (M-A9).
const fieldLabelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  color: "var(--muted)",
  marginBottom: 4,
};

const changeRequestStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--input)",
  border: "1px solid var(--border-soft)",
  borderRadius: 8,
  color: "inherit",
  font: "inherit",
  fontSize: 13,
  padding: 8,
  resize: "vertical",
};

export default function Review() {
  const [decision, setDecision] = useState<null | "approved" | "changes_requested">(null);
  const [liveReview, setLiveReview] = useState<ReviewDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // M-A9: a failed review load used to be swallowed, leaving "loading diff…"
  // on screen forever. Load failures are now their own state, with a retry.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [prMissing, setPrMissing] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [changeRequest, setChangeRequest] = useState("");

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

  // U3: run status/cost for the verdict card. The review API does not carry a
  // run id, so the run comes from a pr_opened event that names THIS prId; the
  // newest run on the stream may be another run. No link → "not recorded".
  const verdictRunId = useMemo(() => runIdForPr(prId, events), [prId, events]);
  const [runAgg, setRunAgg] = useState<RunAggregateDto | null>(null);
  useEffect(() => {
    if (!verdictRunId) {
      setRunAgg(null);
      return;
    }
    const t = getToken();
    if (!t) return;
    let cancelled = false;
    getRunAggregate(verdictRunId, t)
      .then((a) => {
        if (!cancelled) setRunAgg(a);
      })
      .catch(() => {
        if (!cancelled) setRunAgg(null);
      });
    return () => {
      cancelled = true;
    };
  }, [verdictRunId]);

  const verdict = useMemo(() => reviewVerdictFacts(liveReview, runAgg), [liveReview, runAgg]);



  // Section 8's wire shape is camelCase; the older snake_case fields stay as a
  // fallback so the page and the verdict card read the same risk data.
  const changedFiles = liveReview?.changed_files ?? liveReview?.changedFiles ?? [];
  const riskFlags = liveReview?.risk_flags ?? liveReview?.riskFlags ?? [];

  // Load review data whenever a prId becomes available.
  useEffect(() => {
    const t = getToken();
    if (!prId || !t) return;
    let cancelled = false;
    setLoadError(null);
    setPrMissing(false);
    getReviewData(prId, t)
      .then((r) => {
        if (cancelled) return;
        setLiveReview(r);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // A PR that does not exist yet is the honest empty state; anything
        // else (auth, offline, 5xx) is a failure the reviewer can retry.
        if (err instanceof ApiError && err.status === 404) {
          setPrMissing(true);
        } else {
          setLoadError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [prId, reloadKey]);

  const touchesRisky = changedFiles.some((f) =>
    RISKY_FILES.some((r) => f.path.toLowerCase().includes(r)),
  );

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
    // M-A9: "Request changes" used to POST an empty comment array and still
    // report a recorded decision, so the agent received a rejection with no
    // reason. The note is now required and travels as the review comment.
    const note = changeRequest.trim();
    if (kind === "changes_requested" && !note) {
      setError("Say what needs to change — the agent only gets what you write here.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const reviewer = getUserId() ?? "ui-user";
      if (kind === "approved") await approvePr(prId, reviewer, t);
      else await requestChanges(prId, [{ body: note }], reviewer, t);
      setDecision(kind);
      setChangeRequest("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell active="/review">
      {/* M16-X4: Pill keeps className="pill …" so the e2e journey's .pill hooks
          stay intact — parity face over the DS tone map. */}
      <h1>
        Review Area{" "}
        <Pill
          tone={liveReview ? "green" : loadError ? "red" : "yellow"}
          className={`pill ${liveReview ? "green" : loadError ? "red" : "yellow"}`}
        >
          {liveReview
            ? `PR ${liveReview.prId}`
            : !prId || prMissing
              ? "no PR yet"
              : loadError
                ? `PR ${prId} not loaded`
                : `PR ${prId} loading…`}
        </Pill>
      </h1>
      {!prId && <PrEmptyState />}
      {liveReview && (
        <div className="grid">
          {/* U3: the verdict card comes before the diff and the summary. */}
          <VerdictCard facts={verdict} />

          {/* M16-X4: Card(as="div") keeps the .grid child semantics of the old
              div.card shells; title renders the legacy .card h2 face. */}
          <Card as="div" style={{ gridColumn: "1 / -1" }} title="AI-Written PR Summary">
            {liveReview.summary?.sections?.length ? (
              liveReview.summary.sections.map((s) => (
                <p key={s.label}>
                  <strong>{s.label}</strong> — {s.text}
                </p>
              ))
            ) : (
              <p>No AI summary stored for this PR yet.</p>
            )}
          </Card>

          {changedFiles.length > 0 && (
            <Card as="div" title="Changed Files">
              {changedFiles.map((f) => (
                <div className="status-line" key={f.path}>
                  <span>{f.path}</span>
                  <span>
                    +{f.additions} −{f.deletions}
                  </span>
                </div>
              ))}
            </Card>
          )}

          <Card as="div" title="Risk Flags">
            {riskFlags.length ? (
              <ul className="risk-list">
                {riskFlags.map((f) => (
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
          </Card>

          <Card as="div" title="Human Gate">
            <p>Merge happens on GitHub, by a human, manually. This app never merges.</p>
            <label htmlFor="change-request" style={fieldLabelStyle}>
              What should change? (required to request changes; the agent sees only this)
            </label>
            <textarea
              id="change-request"
              value={changeRequest}
              onChange={(e) => setChangeRequest(e.target.value)}
              rows={3}
              aria-label="Changes you are requesting"
              style={changeRequestStyle}
            />
            {/* M16-X4: the flex row is the one-off layout exception (above). */}
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <Button
                type="button"
                variant="primary"
                disabled={busy}
                onClick={() => void decide("approved")}
              >
                Approve
              </Button>
              <Button
                type="button"
                variant="danger"
                disabled={busy || changeRequest.trim() === ""}
                onClick={() => void decide("changes_requested")}
              >
                Request changes
              </Button>
              <a className="btn" href={liveReview.pr_url} target="_blank" rel="noreferrer">
                Open on GitHub ↗
              </a>
            </div>
            {decision && (
              <p>
                Decision recorded:{" "}
                <Pill tone={decision === "approved" ? "green" : "red"} className={`pill ${decision === "approved" ? "green" : "red"}`}>
                  {decision}
                </Pill>{" "}
                — written to audit log via Section 1.
              </p>
            )}
          </Card>
        </div>
      )}
      {!liveReview && prId && !decision && !prMissing && (
        <p className="status-line">
          <span>PR {prId}</span>
          <span>{loadError ? "not loaded" : "loading diff…"}</span>
        </p>
      )}
      {loadError && (
        <Notice tone="danger">
          Review data for PR {prId} could not be loaded: {loadError}{" "}
          <Button type="button" size="sm" variant="secondary" onClick={() => setReloadKey((k) => k + 1)}>
            Try again
          </Button>
        </Notice>
      )}
      {prMissing && !liveReview && (
        <PrEmptyState />
      )}
      {error && <Notice tone="danger">{error}</Notice>}
    </AppShell>
  );
}
