"use client";
// Task 19: external agents registered on this run (section4 adapters.py, protocol v1). Every
// lease is observer-only with no authority; the scope shown is what the agent ASKED for, never
// what it holds. No key material reaches the UI (the server stores only a fingerprint).
import * as React from "react";
import { useEffect, useState } from "react";
import { formatDateTime } from "@/lib/intl";
import { call, root } from "./collabApi";

export interface AdapterLease {
  leaseId: string;
  agentId: string;
  protocolVersion: string;
  capabilities: string[];
  requestedScope: string[];
  mode: "observer";
  authority: [];
  expiresAt: string;
  expired: boolean;
  registeredBy: string;
}

export const listAdapters = (runId: string, t: string) =>
  call<{ adapters: AdapterLease[] }>(`${root(runId)}/adapters`, t);

const muted = { color: "var(--muted)" } as const;

export function ObserversView({ adapters }: { adapters: AdapterLease[] }) {
  return (
    <section aria-label="External observers">
      <h3 style={{ fontSize: 13 }}>External agents ({adapters.length})</h3>
      {adapters.length === 0 ? <p style={muted}>No external agents are observing this run.</p> : (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {adapters.map((a) => (
            <li key={a.leaseId} data-testid={`adapter-${a.agentId}`}>
              <strong>{a.agentId}</strong> observes only (no authority). Can: {a.capabilities.join(", ")}.{" "}
              {a.requestedScope.length > 0 && <>Asked for (not granted): {a.requestedScope.join(", ")}. </>}
              {a.expired ? <span>Lease expired {formatDateTime(a.expiresAt)}.</span>
                : <span>Lease until {formatDateTime(a.expiresAt)}.</span>}
              <span style={muted}> Registered by {a.registeredBy}.</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ObserversSection({ runId, token, tick }: { runId: string; token: string | null; tick: number }) {
  const [adapters, setAdapters] = useState<AdapterLease[] | null>(null);
  useEffect(() => {
    if (!token) return;
    listAdapters(runId, token).then((r) => setAdapters(r.adapters)).catch(() => setAdapters(null));
  }, [runId, token, tick]);
  return adapters ? <ObserversView adapters={adapters} /> : null;
}
