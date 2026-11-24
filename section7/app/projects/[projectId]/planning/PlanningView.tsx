"use client";
// Phase 7 planning surfaces. Admin: brief -> proposal versions -> approve / reject / reassign.
// Member: only their own approved tasks. Approving a plan records a decision; it never starts
// work (D-05). Unknowns from the server are shown as unknown, never filled in here.
import * as React from "react";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Pill } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { ApiError } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import {
  type MyTasks, type PlanVersion, type ReviewDecision,
  createProposal, lines, listVersions, myTasks, parseTasks, reviewProblem, reviewVersion,
} from "./planningApi";

const muted = { color: "var(--muted)" } as const;
const TASKS_EXAMPLE = '[{"key": "threat", "title": "Threat-model the OTP flow", "requiredRole": "security", "preferredAssignee": "user-id"}]';

export function AssignmentTable({ v }: { v: PlanVersion }) {
  const titles = Object.fromEntries(v.brief.tasks.map((t) => [t.key, t.title]));
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <caption className="sr-only">Assignments in version {v.version}</caption>
      <thead><tr><th scope="col">Task</th><th scope="col">Assignee</th><th scope="col">Why</th><th scope="col">Missing expertise</th><th scope="col">Cost</th></tr></thead>
      <tbody>
        {v.assignments.map((a) => (
          <tr key={a.task} data-testid={`assignment-${v.version}-${a.task}`}>
            <td>{titles[a.task] ?? a.task}{a.dependsOn.length > 0 && <span style={muted}> (after {a.dependsOn.join(", ")})</span>}</td>
            <td>{a.assignee ?? <em>Unassigned: availability unknown</em>}</td>
            <td>{a.rationale}</td>
            <td>{a.missingExpertise.length ? a.missingExpertise.join(", ") : "none"}</td>
            <td>{a.costEstimate === "unknown" ? "unknown" : a.costEstimate}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export interface AdminViewProps {
  versions: PlanVersion[];
  busy: boolean;
  problem: string | null;
  onPropose: (goals: string, constraints: string, criteria: string, tasks: string) => void;
  onReview: (version: number, body: ReviewDecision) => void;
}

export function AdminPlanningView(p: AdminViewProps) {
  const [goals, setGoals] = useState("");
  const [constraints, setConstraints] = useState("");
  const [criteria, setCriteria] = useState("");
  const [tasks, setTasks] = useState("");
  const [note, setNote] = useState("");
  const [reTask, setReTask] = useState("");
  const [reWho, setReWho] = useState("");
  const latest = p.versions.at(-1);
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card>
        <h2 style={{ fontSize: 15 }}>New brief</h2>
        <p style={muted}>The proposal names only current team members the brief asks for. Everyone else stays unassigned with availability unknown. Nothing starts until people pick up their tasks.</p>
        <label>Goals (one per line)<textarea value={goals} onChange={(e) => setGoals(e.target.value)} rows={2} style={{ width: "100%" }} /></label>
        <label>Constraints (one per line)<textarea value={constraints} onChange={(e) => setConstraints(e.target.value)} rows={2} style={{ width: "100%" }} /></label>
        <label>Acceptance criteria (one per line)<textarea value={criteria} onChange={(e) => setCriteria(e.target.value)} rows={2} style={{ width: "100%" }} /></label>
        <label>Tasks (JSON)<textarea value={tasks} placeholder={TASKS_EXAMPLE} onChange={(e) => setTasks(e.target.value)} rows={4} style={{ width: "100%", fontFamily: "ui-monospace, Menlo, monospace" }} /></label>
        <Button variant="primary" disabled={p.busy} onClick={() => p.onPropose(goals, constraints, criteria, tasks)}>Propose assignments</Button>
      </Card>
      {p.problem && <Notice tone="warning" role="alert" testId="planning-problem">{p.problem}</Notice>}
      {p.versions.length === 0 && <p style={muted}>No plan yet.</p>}
      {[...p.versions].reverse().map((v) => (
        <Card key={v.version}>
          <section aria-label={`Version ${v.version}`} data-testid={`version-${v.version}`}>
            <h3 style={{ fontSize: 14 }}>Version {v.version} <Pill tone={v.state === "approved" ? "green" : v.state === "rejected" ? "red" : "yellow"}>{v.state}</Pill>
              {v !== latest && <span style={muted}> (older version, read-only)</span>}</h3>
            <p style={muted}>By {v.createdBy}{v.parentVersion ? `, from version ${v.parentVersion}` : ""}{v.note ? `: ${v.note}` : ""}</p>
            <AssignmentTable v={v} />
            {v === latest && (
              <div style={{ display: "grid", gap: 8, marginTop: 8 }} data-testid="review-actions">
                <label>Note (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
                {v.state === "proposed" && (
                  <div role="group" aria-label="Decide" style={{ display: "flex", gap: 8 }}>
                    <Button variant="primary" disabled={p.busy} onClick={() => p.onReview(v.version, { decision: "approve", note: note || undefined })}>Approve plan</Button>
                    <Button variant="danger" disabled={p.busy} onClick={() => p.onReview(v.version, { decision: "reject", note: note || undefined })}>Reject plan</Button>
                  </div>
                )}
                <div role="group" aria-label="Reassign" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <label>Task<select value={reTask} onChange={(e) => setReTask(e.target.value)}>
                    <option value="">Choose…</option>
                    {v.assignments.map((a) => <option key={a.task} value={a.task}>{a.task}</option>)}
                  </select></label>
                  <label>Assignee user id (empty = unassigned)<input value={reWho} onChange={(e) => setReWho(e.target.value)} /></label>
                  <Button disabled={p.busy || !reTask} onClick={() => p.onReview(v.version, { decision: "edit", note: note || undefined, reassign: [{ task: reTask, assignee: reWho.trim() || null }] })}>Reassign (needs a new review)</Button>
                </div>
              </div>
            )}
          </section>
        </Card>
      ))}
    </div>
  );
}

export function MemberTasksView({ mine }: { mine: MyTasks | null }) {
  if (!mine) return <p style={muted}>Loading your tasks…</p>;
  if (mine.version === null) return <p style={muted}>No approved plan yet.</p>;
  return (
    <Card>
      <h2 style={{ fontSize: 15 }}>Your tasks (approved plan version {mine.version})</h2>
      {mine.tasks.length === 0 ? <p style={muted}>Nothing is assigned to you in this plan.</p> : (
        <ul>{mine.tasks.map((t) => (
          <li key={t.task} data-testid={`my-task-${t.task}`}>{t.title}{t.dependsOn.length > 0 && <span style={muted}> (waits on {t.dependsOn.join(", ")})</span>}</li>
        ))}</ul>
      )}
      {(mine.acceptanceCriteria ?? []).length > 0 && <p style={muted}>Done when: {mine.acceptanceCriteria!.join("; ")}</p>}
    </Card>
  );
}

export function PlanningPanel({ projectId }: { projectId: string }) {
  // Read the session after mount: localStorage does not exist on the server render (hydration).
  const [session, setSession] = useState<ReturnType<typeof loadSession> | undefined>(undefined);
  useEffect(() => setSession(loadSession()), []);
  const token = session?.token ?? null;
  const isAdmin = session?.principal.role === "admin";
  const [versions, setVersions] = useState<PlanVersion[]>([]);
  const [mine, setMine] = useState<MyTasks | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    try {
      if (isAdmin) setVersions((await listVersions(projectId, token)).versions);
      else setMine(await myTasks(projectId, token));
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : "Could not load the plan.");
    }
  }, [projectId, token, isAdmin]);
  useEffect(() => { void reload(); }, [reload]);

  if (session === undefined) return <p style={muted}>Loading…</p>;
  if (!token) return <p>Sign in to see planning.</p>;
  if (!isAdmin) return <MemberTasksView mine={mine} />;
  return (
    <AdminPlanningView versions={versions} busy={busy} problem={problem}
      onPropose={(g, c, a, t) => {
        const tasks = parseTasks(t);
        if (typeof tasks === "string") return setProblem(tasks);
        setBusy(true);
        createProposal(projectId, token, { goals: lines(g), constraints: lines(c), acceptanceCriteria: lines(a), tasks })
          .then(() => { setProblem(null); return reload(); })
          .catch((e) => setProblem(e instanceof ApiError ? e.message : "The proposal failed."))
          .finally(() => setBusy(false));
      }}
      onReview={(version, body) => {
        setBusy(true);
        reviewVersion(projectId, token, version, body)
          .then(() => { setProblem(null); return reload(); })
          .catch((e) => setProblem(reviewProblem(e instanceof ApiError ? e.status : undefined, e instanceof ApiError ? e.code : undefined)))
          .finally(() => setBusy(false));
      }} />
  );
}
