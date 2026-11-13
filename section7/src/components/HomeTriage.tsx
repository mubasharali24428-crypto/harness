"use client";

// U1 home (docs/plans/ui-overhaul-plan-2026-11-13.md §4): "Needs you" on the left, oldest wait first;
// "Working now" on the right. Also drives the tab title and icon, so a background tab says when a
// person is needed. Data: GET /api/v1/orchestrator/runs (team-scoped server-side), refreshed on a timer.
import * as React from "react";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getToken } from "@/lib/auth";
import { formatRelativeTime, formatTimestamp } from "@/lib/format";
import { needsYouHref, tabTitle, tabTone, triageRuns, type Triage } from "@/lib/triage";
import { listRuns, runDetailHref, shortId, stateLabel, type RunDto } from "../../app/runs/runsApi";

const REFRESH_MS = 15_000;
const TONE_FILL = { attention: "#e9b949", working: "#6b8cff", idle: "#8d96a8" } as const;

function tabIcon(tone: keyof typeof TONE_FILL): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" fill="${TONE_FILL[tone]}"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** Keeps document.title and the favicon in step with the triage while the home page is open. */
function useTabStatus(t: Triage | null) {
  useEffect(() => {
    if (!t) return;
    const baseTitle = "AI Engineering Harness";
    document.title = tabTitle(baseTitle, t);
    let link = document.querySelector<HTMLLinkElement>("link[rel='icon'][data-harness]");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      link.dataset.harness = "1";
      document.head.appendChild(link);
    }
    link.href = tabIcon(tabTone(t));
    return () => {
      document.title = baseTitle;
    };
  }, [t]);
}

function RunRow({ run, action, projectName }: { run: RunDto; action?: string; projectName: string }) {
  const when = run.startedAt ?? run.createdAt;
  return (
    <li>
      <Link
        href={action ? needsYouHref(run) : runDetailHref(run)}
        className="group flex items-start justify-between gap-3 rounded-lg border border-border-soft bg-panel-2 px-3 py-2.5 no-underline hover:border-border focus-visible:outline-2 focus-visible:outline-accent"
      >
        <span className="min-w-0">
          <span className="block text-[14px] text-text">{action ?? stateLabel(run.status)}</span>
          <span className="block truncate font-mono text-[12px] text-muted">
            {projectName} · run {shortId(run.id)}
          </span>
        </span>
        <time className="shrink-0 font-mono text-[12px] text-muted" title={formatTimestamp(when)} dateTime={when}>
          {formatRelativeTime(when)}
        </time>
      </Link>
    </li>
  );
}

function Column({
  title,
  count,
  empty,
  children,
  testId,
}: {
  title: string;
  count: number | null;
  empty: string;
  children: React.ReactNode;
  testId: string;
}) {
  const headingId = `${testId}-h`;
  return (
    <section aria-labelledby={headingId} data-testid={testId} className="min-w-0 rounded-xl border border-border bg-panel p-4">
      <h2 id={headingId} className="m-0 mb-3 flex items-center gap-2 text-[15px] font-semibold text-text">
        {title}
        {count !== null ? (
          <span className="rounded-full bg-panel-3 px-2 py-0.5 font-mono text-[12px] text-text-2">{count}</span>
        ) : null}
      </h2>
      {count === 0 ? <p className="m-0 text-[13px] text-muted">{empty}</p> : children}
    </section>
  );
}

export default function HomeTriage({ projectNames }: { projectNames: Record<string, string> }) {
  const [runs, setRuns] = useState<RunDto[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    let alive = true;
    const load = () =>
      listRuns({ limit: 100 }, token)
        .then((page) => {
          if (!alive) return;
          setRuns(page.items);
          setError(null);
        })
        .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const triage = useMemo(() => (runs ? triageRuns(runs) : null), [runs]);
  useTabStatus(triage);
  const name = (r: RunDto) => projectNames[r.projectId] ?? shortId(r.projectId);

  return (
    <div className="mb-6">
      {error ? (
        <p role="alert" className="m-0 mb-3 text-[13px] text-red">
          Could not load runs: {error}
        </p>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2">
        <Column
          title="Needs you"
          count={triage ? triage.needsYou.length : null}
          empty="Nothing is waiting on a person."
          testId="home-needs-you"
        >
          {triage ? (
            <ol className="m-0 grid list-none gap-2 p-0">
              {triage.needsYou.slice(0, 8).map((r) => (
                <RunRow key={r.id} run={r} action={r.action} projectName={name(r)} />
              ))}
            </ol>
          ) : (
            <p className="m-0 text-[13px] text-muted">Loading…</p>
          )}
        </Column>
        <Column
          title="Working now"
          count={triage ? triage.working.length : null}
          empty="No agent is working right now."
          testId="home-working"
        >
          {triage ? (
            <ol className="m-0 grid list-none gap-2 p-0">
              {triage.working.slice(0, 8).map((r) => (
                <RunRow key={r.id} run={r} projectName={name(r)} />
              ))}
            </ol>
          ) : (
            <p className="m-0 text-[13px] text-muted">Loading…</p>
          )}
        </Column>
      </div>
      {triage && triage.needsYou.length + triage.working.length > 16 ? (
        <p className="m-0 mt-2 text-[13px]">
          <Link href="/runs" className="text-accent">
            All runs →
          </Link>
        </p>
      ) : null}
    </div>
  );
}
