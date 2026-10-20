// Parts 31/58: the "erase my account" card. Presentational (the page owns state and the request),
// so it renders in the node test env like the other settings cells.
import * as React from "react";
import { Button, Card } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";

export function ErasureCard(p: {
  typed: string;
  onType: (v: string) => void;
  onSubmit: () => void;
  busy: boolean;
  problem: string | null;
  done: boolean;
}) {
  if (p.done) {
    return (
      <Card title="Account erased" testId="erasure-done">
        <p>Your account has been erased. You have been signed out and can no longer sign in with it.</p>
        <a href="/login">Back to sign in</a>
      </Card>
    );
  }
  return (
    <Card title="Erase my account" testId="erasure-card">
      <p>This cannot be undone. Erasing your account:</p>
      <ul>
        <li>removes your name and email, so you can no longer sign in, and ends every session;</li>
        <li>deletes your personal settings, notification inbox, GitHub connection and your seats in teams, organizations and workspaces;</li>
        <li>deletes invitations and mail addressed to your email.</li>
      </ul>
      <p>
        What stays: audit and collaboration records keep an opaque id with no name or email, and comments or
        plans you wrote stay until removed through a content deletion request.
      </p>
      <p>
        It is refused, and nothing changes, while you are the only admin or owner of a team or organization,
        or took part in a run under a retention hold.
      </p>
      <form onSubmit={(e) => { e.preventDefault(); p.onSubmit(); }}>
        <label htmlFor="erase-email">Type your email address to confirm</label>
        <br />
        <input
          id="erase-email"
          className="text-input"
          type="email"
          autoComplete="off"
          value={p.typed}
          onChange={(e) => p.onType(e.target.value)}
          aria-describedby={p.problem ? "erase-problem" : undefined}
        />
        {p.problem && <Notice id="erase-problem" tone="danger" role="alert">{p.problem}</Notice>}
        <div style={{ marginTop: 12 }}>
          <Button type="submit" variant="danger" disabled={p.busy || p.typed.trim() === ""} testId="erase-submit">
            Erase my account
          </Button>
        </div>
      </form>
    </Card>
  );
}
