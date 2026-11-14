"use client";

// SignedOutState — a real condition (no session in this browser), not a fake
// state switch. Names the next action and links to the login flow the demo
// already uses; ?next returns here after sign-in.

import * as React from "react";
import { EmptyState } from "@/components/States";

export function SignedOutState() {
  return (
    <EmptyState
      title="Sign in to see your runs"
      body="This screen reads your team's real runs, so it needs a session. Sign in and you'll land back here."
      action={
        <a
          className="btn primary"
          href={typeof window !== "undefined" ? `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}` : "/login"}
        >
          Sign in
        </a>
      }
    />
  );
}
