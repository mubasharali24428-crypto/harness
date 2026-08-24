// M14-F2a — invite lifecycle admin UI tests.
// Mock-fetch protocol per lane instructions: resend/revoke wire contracts,
// per-status InvitesTable rendering (color+text badges, actions per status),
// and the honest empty state. UNVERIFIED against the live server until VT-3
// probes land (ATLAS diffs at gate).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError, IDENTITY_BASE } from "@/lib/api";
import { listInvites, resendInvite, revokeInvite, type TeamInviteDto } from "./teamApi";
import { InvitesTable, type InviteActions } from "./tables";

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOKEN = "tok-123";

function stubFetch(responses: Array<{ status: number; body?: unknown }>) {
  const queue = [...responses];
  return vi.fn(async () => {
    const next = queue.shift() ?? { status: 500 };
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      text: async () => (next.body === undefined ? "" : JSON.stringify(next.body)),
    };
  });
}

const noopActions: InviteActions = {
  onResend: () => {},
  onRevoke: () => {},
};

function invite(partial: {
  id?: string;
  email?: string;
  role?: string;
  status?: string;
}) {
  const status = partial.status ?? "sent";
  return {
    id: partial.id ?? "inv-1",
    teamId: "team-1",
    email: partial.email ?? "newbie@x.com",
    role: partial.role ?? "engineer",
    expiresAt: "2026-08-30T00:00:00Z",
    redeemedAt: status === "redeemed" ? "2026-08-23T10:00:00Z" : null,
    revokedAt: status === "revoked" ? "2026-08-23T09:00:00Z" : null,
    createdAt: "2026-08-23T08:00:00Z",
    status,
  } satisfies TeamInviteDto;
}

describe("M14-F2a client: listInvites", () => {
  it("GETs the full lifecycle list with bearer", async () => {
    const f = stubFetch([{ status: 200, body: { items: [] } }]);
    vi.stubGlobal("fetch", f);
    await expect(listInvites("team-1", TOKEN)).resolves.toEqual({ items: [] });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    // plain URL: no implicit status filter — every lifecycle row comes back
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/teams/team-1/invites`);
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });
});

describe("M14-F2a client: resendInvite", () => {
  it("POSTs .../invites/{id}/resend and returns the fresh one-time token", async () => {
    const minted = invite({ id: "inv-9" });
    const f = stubFetch([{ status: 201, body: { ...minted, token: "fresh-token-value-0001" } }]);
    vi.stubGlobal("fetch", f);
    const out = await resendInvite("team-1", "inv-9", TOKEN);
    expect(out.token).toBe("fresh-token-value-0001");
    expect(out.id).toBe("inv-9");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/teams/team-1/invites/inv-9/resend`);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("maps a revoked-invite 409 error_body to an honest ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch([
        { status: 409, body: { detail: { error: { code: "INVITE_REVOKED", message: "invite was revoked" } } } },
      ]),
    );
    try {
      await resendInvite("team-1", "inv-9", TOKEN);
      expect.fail("expected ApiError");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).code).toBe("INVITE_REVOKED");
      expect((e as ApiError).status).toBe(409);
    }
  });
});

describe("M14-F2a client: revokeInvite", () => {
  it("DELETEs .../invites/{id} with bearer and resolves {ok:true}", async () => {
    const f = stubFetch([{ status: 200, body: { ok: true } }]);
    vi.stubGlobal("fetch", f);
    await expect(revokeInvite("team-1", "inv-2", TOKEN)).resolves.toEqual({ ok: true });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/teams/team-1/invites/inv-2`);
    expect(init.method).toBe("DELETE");
  });
});

describe("InvitesTable status rendering (a11y law: color + text)", () => {
  it("renders a text badge for EVERY lifecycle status", () => {
    const rows = [
      invite({ id: "d1", status: "draft", email: "d@x.com" }),
      invite({ id: "s1", status: "sent", email: "s@x.com" }),
      invite({ id: "r1", status: "redeemed", email: "r@x.com" }),
      invite({ id: "e1", status: "expired", email: "e@x.com" }),
      invite({ id: "v1", status: "revoked", email: "v@x.com" }),
    ];
    const html = renderToStaticMarkup(
      <InvitesTable invites={rows} busyInviteId={null} actions={noopActions} />,
    );
    // The LAW: every badge carries its text label (never color alone).
    for (const s of ["draft", "sent", "redeemed", "expired", "revoked"]) {
      expect(html).toContain(`>${s}</span>`);
    }
    // Color rides along WITH each label (tokens.statusTones → Pill tone):
    // redeemed=green hex, revoked=red hex, sent=accent blue. The badge is the
    // span whose text is exactly the status.
    const badgeFor = (id: string) => {
      const row = html.split(`data-testid="invite-row-${id}"`)[1] ?? "";
      return row.slice(0, row.indexOf("</tr>"));
    };
    const spanFor = (rowHtml: string, label: string) => {
      const idx = rowHtml.indexOf(`>${label}</span>`);
      const start = rowHtml.lastIndexOf("<span", idx);
      return rowHtml.slice(start, idx + label.length + 7);
    };
    expect(spanFor(badgeFor("r1"), "redeemed")).toContain("var(--green)"); // green (token, not literal hex)
    expect(spanFor(badgeFor("v1"), "revoked")).toContain("var(--red)"); // red
    expect(spanFor(badgeFor("s1"), "sent")).toContain("var(--accent)"); // accent/blue
    expect(spanFor(badgeFor("d1"), "draft")).toContain("var(--yellow)"); // yellow
    expect(spanFor(badgeFor("e1"), "expired")).toContain("var(--yellow)"); // yellow
  });

  it("shows Resend + Revoke only on actionable statuses (draft/sent)", () => {
    const html = renderToStaticMarkup(
      <InvitesTable
        invites={[
          invite({ id: "live", status: "sent" }),
          invite({ id: "done", status: "redeemed" }),
          invite({ id: "old", status: "expired" }),
          invite({ id: "dead", status: "revoked" }),
        ]}
        busyInviteId={null}
        actions={noopActions}
      />,
    );
    expect(html).toContain('data-testid="resend-invite-live"');
    expect(html).toContain('data-testid="revoke-invite-live"');
    // terminal statuses: no actions, just an em-dash
    for (const id of ["done", "old", "dead"]) {
      expect(html).not.toContain(`data-testid="resend-invite-${id}"`);
      expect(html).not.toContain(`data-testid="revoke-invite-${id}"`);
    }
    expect((html.match(/>—</g) ?? []).length).toBe(3);
  });

  it("wires onResend/onRevoke to the right row buttons", () => {
    const calls: Array<[string, string]> = [];
    const actions: InviteActions = {
      onResend: (id) => calls.push(["resend", id]),
      onRevoke: (id) => calls.push(["revoke", id]),
    };
    const html = renderToStaticMarkup(
      <InvitesTable
        invites={[invite({ id: "live", email: "ada@x.com" })]}
        busyInviteId={null}
        actions={actions}
      />,
    );
    expect(html).toContain("Resend invite for ada@x.com");
    expect(html).toContain("Revoke invite for ada@x.com");
    // simulate clicks against the rendered handlers
    actions.onResend("live");
    actions.onRevoke("live");
    expect(calls).toEqual([
      ["resend", "live"],
      ["revoke", "live"],
    ]);
  });

  it("disables the busy row's controls and shows progress text", () => {
    const html = renderToStaticMarkup(
      <InvitesTable
        invites={[invite({ id: "live" }), invite({ id: "other" })]}
        busyInviteId="live"
        actions={noopActions}
      />,
    );
    expect(html).toContain("Resending…");
    // disabled renders as `disabled=""` before the data-testid attribute
    const rowHtml = html.split('data-testid="invite-row-live"')[1]?.split("data-testid=\"invite-row-other\"")[0] ?? "";
    expect(rowHtml).toContain("disabled");
    expect(rowHtml).toContain("Resending…");
    // the OTHER row stays enabled
    const otherRow = html.split('data-testid="invite-row-other"')[1] ?? "";
    expect(otherRow).toContain("Resend</button>");
    expect(otherRow).not.toContain("disabled");
  });

  it("renders an honest empty state (no fake rows)", () => {
    const html = renderToStaticMarkup(
      <InvitesTable invites={[]} busyInviteId={null} actions={noopActions} />,
    );
    expect(html).toContain("No invites yet — create one above.");
    expect(html).not.toContain("<button");
  });
});
