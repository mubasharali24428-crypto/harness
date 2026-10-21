"use client";

// Part 40 UI: who owns the session this run belongs to, and a hand-over to another active member.
// The server refuses a stale owner (STALE_CLIENT_STATE); the panel re-reads and shows the current one.
import * as React from "react";
import { useCallback, useContext, useEffect, useState } from "react";
import { AnnounceContext } from "@/components/LiveAnnouncer";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { type SessionMember, type SessionView, getRunSession, getSessionMembers, transferOwner } from "./controlApi";
import { useAction, usePolled } from "./usePolled";

export function OwnerPanel(p: {
  session: SessionView; members: SessionMember[]; me: string; canTransfer: boolean; busy: boolean;
  onTransfer: (toUserId: string) => void;
}) {
  const id = React.useId();
  const [to, setTo] = useState("");
  const others = p.members.filter((m) => m.kind === "user" && m.userId && m.userId !== p.session.ownerUserId);
  const mine = p.session.ownerUserId === p.me;
  return (
    <section aria-label="Session owner" data-testid="owner-panel">
      <h3 style={{ fontSize: 13 }}>Session owner</h3>
      <p style={{ margin: "4px 0" }}>
        <strong>{p.session.ownerUserId}</strong>{mine ? " (you)" : ""} owns <em>{p.session.title}</em> ({p.session.state}).
      </p>
      {p.canTransfer && others.length > 0 && (
        <form onSubmit={(e) => { e.preventDefault(); if (to) p.onTransfer(to); }} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <label htmlFor={`${id}-to`}>Hand over to</label>
          <select id={`${id}-to`} value={to} onChange={(e) => setTo(e.target.value)} disabled={p.busy}>
            <option value="">Choose a member…</option>
            {others.map((m) => <option key={m.userId!} value={m.userId!}>{m.displayName ?? m.userId}</option>)}
          </select>
          <Button size="sm" variant="danger" type="submit" disabled={p.busy || !to}>Transfer ownership</Button>
        </form>
      )}
    </section>
  );
}

export function RunOwnerSection({ runId, token, tick, me }: { runId: string; token: string; tick: number; me: string }) {
  const [session, reloadSession] = usePolled(useCallback(() => getRunSession(runId, token), [runId, token]), tick, 15000);
  const sid = session?.id;
  const [members, reloadMembers] = usePolled(useCallback(() => (sid ? getSessionMembers(sid, token) : Promise.resolve(null)), [sid, token]), tick, 30000);
  const after = useCallback(async () => { await Promise.all([reloadSession(), reloadMembers()]); }, [reloadSession, reloadMembers]);
  const { busy, error, said, run } = useAction(after);
  const announce = useContext(AnnounceContext);
  useEffect(() => { if (announce && said) announce(said); }, [announce, said]);
  if (!session) return null; // a run outside any session has no owner to show
  // The server decides who may hand over (manage rights); a refusal is shown as a plain message.
  const canTransfer = session.state !== "deleted" && (session.yourRole === "owner" || session.ownerUserId === me);
  return (
    <div data-testid="run-owner">
      {error && <Notice tone="danger" role="alert">{error}</Notice>}
      <OwnerPanel session={session} members={members?.items ?? []} me={me} canTransfer={canTransfer} busy={busy}
        onTransfer={(to) => void run(() => transferOwner(session.id, token, to, session.ownerUserId), () => "Ownership transferred")} />
    </div>
  );
}
