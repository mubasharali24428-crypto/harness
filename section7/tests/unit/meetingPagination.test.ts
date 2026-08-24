// M16-X2 — meeting message pagination (client half).
//
// Pins the section7 wiring against S6's paginated GET /messages:
// - `getMessagePage` issues a real `?limit=&before=` cursor request and
//   returns the {items, hasMore} envelope untouched.
// - `getMessages` stays a convenience read: it unwraps `items` so every
//   pre-existing caller (sendWithAi, restore, tests) keeps its shape.
// - The page-level load-more contract: PREPEND older pages (history order),
//   never touch newer messages/composer state, and stop when hasMore=false.
//
// The load-more reducer is exercised through the same pure logic the page
// uses (prepend + hasMore handoff) because vitest here runs in a node
// environment — see meetingOverride.test.tsx for the source-pin technique.

import { afterEach, describe, expect, it, vi } from "vitest";
import { getMessagePage, getMessages, MEETING_BASE } from "@/lib/api";
import type { MeetingMsg } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOKEN = "tok-123";

function stubOk(responseBody: unknown) {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(responseBody),
  }));
}

function msg(id: string, content = `c-${id}`): MeetingMsg {
  return { id, userId: "u1", content, isAiGenerated: false };
}

describe("getMessagePage (M16-X2)", () => {
  it("sends ?limit=&before= as query params and keeps bearer auth", async () => {
    const f = stubOk({ items: [], hasMore: false });
    vi.stubGlobal("fetch", f);
    await getMessagePage("mtg-9", TOKEN, { limit: 50, before: "msg-abc" });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      `${MEETING_BASE}/api/v1/meetings/mtg-9/messages?limit=50&before=msg-abc`,
    );
    expect((init.headers as Record<string, string>).authorization).toBe(
      `Bearer ${TOKEN}`,
    );
  });

  it("omits the query string entirely when no paging opts are given", async () => {
    const f = stubOk({ items: [], hasMore: false });
    vi.stubGlobal("fetch", f);
    await getMessagePage("mtg-9", TOKEN);
    const [url] = f.mock.calls[0] as unknown as [string];
    // No-param GET = documented legacy window (server default), not limit=0.
    expect(url).toBe(`${MEETING_BASE}/api/v1/meetings/mtg-9/messages`);
  });

  it("returns the {items, hasMore} envelope untouched", async () => {
    const envelope = { items: [msg("m-2"), msg("m-3")], hasMore: true };
    vi.stubGlobal("fetch", stubOk(envelope));
    const page = await getMessagePage("mtg-9", TOKEN);
    expect(page).toEqual(envelope);
    expect(page.hasMore).toBe(true);
  });

  it("getMessages unwraps items for legacy callers (compat shim)", async () => {
    vi.stubGlobal(
      "fetch",
      stubOk({ items: [msg("m-1"), msg("m-2")], hasMore: false }),
    );
    const msgs = await getMessages("mtg-9", TOKEN);
    expect(Array.isArray(msgs)).toBe(true);
    expect(msgs.map((m) => m.id)).toEqual(["m-1", "m-2"]);
  });
});

describe("load-more append semantics (M16-X2)", () => {
  /** Exact reduction the page's load-more performs on a fetched page. */
  const prependOlder = (prev: MeetingMsg[], page: { items: MeetingMsg[] }) => [
    ...page.items,
    ...prev,
  ];

  it("older pages land BEFORE the loaded window, preserving history order", () => {
    const loaded = [msg("m-8"), msg("m-9"), msg("m-10")];
    const olderPage = { items: [msg("m-5"), msg("m-6"), msg("m-7")] };
    const next = prependOlder(loaded, olderPage);
    expect(next.map((m) => m.id)).toEqual(["m-5", "m-6", "m-7", "m-8", "m-9", "m-10"]);
  });

  it("repeated loads keep appending strictly older history without duplicates", () => {
    let state = [msg("m-4"), msg("m-5")];
    state = prependOlder(state, { items: [msg("m-2"), msg("m-3")] });
    state = prependOlder(state, { items: [msg("m-0"), msg("m-1")] });
    expect(state.map((m) => m.id)).toEqual(["m-0", "m-1", "m-2", "m-3", "m-4", "m-5"]);
    const ids = new Set(state.map((m) => m.id));
    expect(ids.size).toBe(state.length); // cursor excludes the anchor — no repeats
  });

  it("an empty older page leaves state intact (honest empty cursor)", () => {
    const loaded = [msg("m-3")];
    expect(prependOlder(loaded, { items: [] })).toEqual(loaded);
  });
});

describe("meeting page load-more wiring (source pins)", () => {
  // Same technique as meetingOverride.test.tsx: the node vitest environment
  // can't render Next page components, so the page contract is pinned at
  // source level.
  it("renders a hasMore-gated load-more control and leaves the composer alone", async () => {
    const fs = await import("node:fs");
    const src = await fs.promises.readFile(
      new URL("../../app/meeting/page.tsx", import.meta.url),
      "utf8",
    );
    // Load-more exists, is gated on server truthfulness, and prepends via loadMore.
    expect(src).toContain('data-testid="load-older"');
    expect(src).toContain("{hasMore && (");
    expect(src).toContain("onClick={() => void loadMore()}");
    // Cursor request prefers the stable messageSeq; legacy id is the fallback.
    expect(src).toContain("before: oldest.messageSeq ?? oldest.id");
    // Composer unaffected: still exactly one chat input with its accessible name.
    expect(src.match(/aria-label="Chat message"/g)?.length).toBe(1);
    // Restore + reload read through the paginated endpoint (newest window only).
    expect(src).toContain("(await getMessagePage(id, tok)).items");
  });
});
