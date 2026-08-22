"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import DiffView from "@/components/DiffView";
import { api, ApiError, getToken } from "@/lib/api";
import { mockDiff } from "@/lib/mockApi";

const RISKY_FILES = ["auth/", "payment/", "migrations", ".env", "secrets"];

interface ReviewDto {
  prId: string;
  pr_url: string;
  review_status: string;
  changed_files: { path: string; additions: number; deletions: number }[];
  risk_flags?: { path: string; severity: string; rule: string; reason: string }[];
  summary?: { sections: { label: string; text: string }[] };
}

export default function Review() {
  const [decision, setDecision] = useState<null | "approved" | "changes_requested">(null);
  const [source, setSource] = useState<"live" | "mock">("mock");
  const [liveReview, setLiveReview] = useState<ReviewDto | null>(null);
  const diff = mockDiff();

  useEffect(() => {
    const prId = localStorage.getItem("harness.prId");
    if (!prId) return;
    api
      .getReviewData<ReviewDto>(prId)
      .then((data) => {
        setLiveReview(data);
        setSource("live");
      })
      .catch(() => setSource("mock"));
  }, []);

  const touchesRisky = RISKY_FILES.some((r) =>
    (diff.original + diff.modified).toLowerCase().includes(r.replace("/", ""))
  );

  const decide = async (kind: "approved" | "changes_requested") => {
    setDecision(kind);
    if (source !== "live" || !liveReview) return;
    try {
      const who = getToken() ?? "reviewer";
      if (kind === "approved") await api.approvePr(liveReview.prId, who);
      else await api.requestChanges(liveReview.prId, [], who);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401)
        console.error("login required for live review actions");
    }
  };

  return (
    <AppShell active="/review">
      <h1>
        Review Area — draft PR #7: agent/demo → main{" "}
        <span className={`pill ${source === "live" ? "green" : "yellow"}`}>{source}</span>
      </h1>
      <div className="grid">
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>AI-Written PR Summary</h2>
          {liveReview?.summary?.sections?.length ? (
            liveReview.summary.sections.map((s) => (
              <p key={s.label}>
                <strong>{s.label}</strong> — {s.text}
              </p>
            ))
          ) : (
            <p>
              Adds a <code>/health</code> endpoint per brief brf-001. No other routes touched.
              Risky areas: none — but middleware was NOT changed and deserves an eyeball. Tests
              added and green.
            </p>
          )}
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Diff</h2>
          <DiffView original={diff.original} modified={diff.modified} />
        </div>

        {liveReview?.changed_files?.length ? (
          <div className="card">
            <h2>Changed Files (live)</h2>
            {liveReview.changed_files.map((f) => (
              <div className="status-line" key={f.path}>
                <span>{f.path}</span>
                <span>
                  +{f.additions} −{f.deletions}
                </span>
              </div>
            ))}
          </div>
        ) : null}

        <div className="card">
          <h2>Risk Flags</h2>
          {liveReview?.risk_flags?.length ? (
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
              No high-risk zones touched (<code>auth/</code>, payments, migrations, secrets).
            </p>
          )}
        </div>

        <div className="card">
          <h2>Human Gate</h2>
          <p>Merge happens on GitHub, by a human, manually. This app never merges.</p>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn primary" onClick={() => decide("approved")}>
              Approve
            </button>
            <button className="btn danger" onClick={() => decide("changes_requested")}>
              Request changes
            </button>
            <a className="btn" href={liveReview?.pr_url ?? "https://github.com/org/repo/pull/7"} target="_blank">
              Open on GitHub ↗
            </a>
          </div>
          {decision && (
            <p>
              Decision recorded:{" "}
              <span className={`pill ${decision === "approved" ? "green" : "red"}`}>
                {decision}
              </span>{" "}
              — written to audit log{source === "live" ? " via Section 1" : ""}.
            </p>
          )}
        </div>

        <div className="card chat">
          <h2>Inline Comments</h2>
          <div className="msg human">
            <div className="who">sam · src/app.py:1</div>
            Prefer explicit 200 status constant?
          </div>
          <div className="chat-input">
            <input placeholder="Add a comment…" />
            <button className="btn">Comment</button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
