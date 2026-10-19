"use client";

// Part 45 UI: compact subtask tree. Pause / resume / cancel only change the server's ledger (docs/agents.md);
// the buttons say what they do and are shown to people who can steer.
import * as React from "react";
import { useCallback } from "react";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { Pill } from "@/components/ui";
import { type AgentItem, agentAction, getAgents } from "./controlApi";
import { useAction, usePolled } from "./usePolled";

const muted = { color: "var(--muted)" } as const;

export interface AgentNode { item: AgentItem; children: AgentNode[] }

/** Items arrive oldest first; a child whose parent is missing is shown as a root rather than dropped. */
export function buildAgentTree(items: AgentItem[]): AgentNode[] {
  const nodes = new Map(items.map((i) => [i.id, { item: i, children: [] as AgentNode[] }]));
  const roots: AgentNode[] = [];
  for (const n of nodes.values()) {
    const parent = n.item.parentId ? nodes.get(n.item.parentId) : undefined;
    (parent ? parent.children : roots).push(n);
  }
  return roots;
}

const tone = (s: string) => (s === "active" || s === "completed" ? "green" as const : s === "paused" ? "yellow" as const : "red" as const);

function Row({ n, canSteer, busy, onAct }: {
  n: AgentNode; canSteer: boolean; busy: boolean; onAct: (id: string, a: "pause" | "resume" | "cancel") => void;
}) {
  const ids = React.useId();
  const a = n.item;
  const last = a.recentToolCalls?.[0];
  const live = a.state === "active" || a.state === "paused";
  return (
    <li data-testid={`agent-${a.id}`}>
      <span id={ids}><strong>{a.title}</strong> ({a.agentName} v{a.agentVersion})</span>{" "}
      <Pill tone={tone(a.state)}>{a.state === "paused" && a.pauseReason ? `paused: ${a.pauseReason.replace(/_/g, " ")}` : a.state}</Pill>
      <span style={muted}>
        {" "}${a.budget.remainingUsd} budget left
        {a.progress.percent !== null ? `, ${a.progress.percent}% done` : ""}
        {last ? `. Last tool: ${last.tool}, ${last.summary}` : ". No tool calls yet"}
      </span>
      {canSteer && live && (
        <span role="group" aria-label="Agent controls" style={{ marginLeft: 8 }}>
          {a.state === "active"
            ? <Button size="sm" disabled={busy} aria-describedby={ids} onClick={() => onAct(a.id, "pause")}>Pause</Button>
            : <Button size="sm" disabled={busy} aria-describedby={ids} onClick={() => onAct(a.id, "resume")}>Resume</Button>}{" "}
          <Button size="sm" variant="danger" disabled={busy} aria-describedby={ids} onClick={() => onAct(a.id, "cancel")}>Cancel</Button>
        </span>
      )}
      {n.children.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {n.children.map((c) => <Row key={c.item.id} n={c} canSteer={canSteer} busy={busy} onAct={onAct} />)}
        </ul>
      )}
    </li>
  );
}

export function AgentTree(p: {
  items: AgentItem[]; canSteer: boolean; busy: boolean; error: string | null; said: string;
  onAct: (id: string, a: "pause" | "resume" | "cancel") => void;
}) {
  if (p.items.length === 0) return null;
  return (
    <section aria-label="Agent subtasks" data-testid="agent-tree">
      <div role="status" aria-live="polite" style={{ position: "absolute", left: -9999 }}>{p.said}</div>
      {p.error && <Notice tone="danger" role="alert">{p.error}</Notice>}
      <h3 style={{ fontSize: 13 }}>Agent subtasks ({p.items.length})</h3>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {buildAgentTree(p.items).map((n) => <Row key={n.item.id} n={n} canSteer={p.canSteer} busy={p.busy} onAct={p.onAct} />)}
      </ul>
      {p.canSteer && <p style={muted}>Pause and cancel update the run's record. They do not stop a running process yet.</p>}
    </section>
  );
}

export function AgentTreeSection({ runId, token, tick, canSteer }: {
  runId: string; token: string; tick: number; canSteer: boolean;
}) {
  const [data, reload] = usePolled(useCallback(() => getAgents(runId, token), [runId, token]), tick, 10000);
  const { busy, error, said, run } = useAction(reload);
  return (
    <AgentTree
      items={data?.items ?? []} canSteer={canSteer} busy={busy} error={error} said={said}
      onAct={(id, a) => void run(() => agentAction(runId, token, id, a),
        () => ({ pause: "Agent paused", resume: "Agent resumed", cancel: "Agent cancelled" })[a])}
    />
  );
}
