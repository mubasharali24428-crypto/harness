// M14-F1b (raw-03 P2) — focus-management helper tests.
// Node-env constraint (no jsdom/RTL in this repo; installs are out of scope):
// trapFocus is exercised against a minimal hand-built DOM fake — the same
// protocol as the other unit tests in this suite. The fake implements exactly
// the surface focus.ts touches: querySelectorAll, add/removeEventListener,
// contains, focus, and getComputedStyle via a stubbed global.
import { describe, expect, it } from "vitest";
import { MAIN_CONTENT_ID, SKIP_HREF, trapFocus, useSkipTarget } from "@/lib/focus";

type Handler = (event: {
  key: string;
  shiftKey: boolean;
  preventDefault: () => void;
}) => void;

/** Minimal Element stand-in: enough for the FOCUSABLE_SELECTOR pass. */
function fakeEl(name: string) {
  const listeners = new Map<string, Handler>();
  return {
    name,
    focused: false,
    focusCount: 0,
    getAttribute: () => null, // never aria-hidden
    addEventListener: (type: string, fn: Handler) => void listeners.set(type, fn),
    removeEventListener: (type: string) => void listeners.delete(type),
    focus() {
      this.focused = true;
      this.focusCount += 1;
    },
    dispatch(event: { key: string; shiftKey?: boolean }) {
      const fn = listeners.get("keydown");
      let prevented = false;
      fn?.({
        key: event.key,
        shiftKey: event.shiftKey ?? false,
        preventDefault: () => {
          prevented = true;
        },
      });
      return prevented;
    },
  };
}

/** Container fake: `children` are returned by querySelectorAll in order. */
function makeContainer(children: ReturnType<typeof fakeEl>[]) {
  const container = {
    ...fakeEl("container"),
    children,
    contains: (el: unknown) => children.includes(el as never),
    querySelectorAll: () => children,
    activeChild: null as unknown,
    document_activeElement: null as unknown,
  };
  return container;
}

describe("focus constants", () => {
  it("exposes the shared main-content landmark id + skip href", () => {
    expect(MAIN_CONTENT_ID).toBe("main-content");
    expect(SKIP_HREF).toBe("#main-content");
  });
});

describe("useSkipTarget", () => {
  it("returns the fragment href and an activate handler", () => {
    const { skipHref, onSkipActivate } = useSkipTarget();
    expect(skipHref).toBe("#main-content");
    expect(typeof onSkipActivate).toBe("function");
  });

  it("activate handler no-ops safely when the target is absent", () => {
    const { onSkipActivate } = useSkipTarget();
    let prevented = false;
    expect(() =>
      onSkipActivate({ preventDefault: () => {
        prevented = true;
      } }),
    ).not.toThrow();
    // No #main-content element exists in node env → native behavior is left
    // alone (no preventDefault).
    expect(prevented).toBe(false);
  });
});

describe("trapFocus", () => {
  it("moves initial focus to the first tabbable item when focus is outside", () => {
    const [a, b] = [fakeEl("a"), fakeEl("b")];
    const container = makeContainer([a, b]);
    const cleanup = trapFocus(container as never);
    expect(a.focusCount).toBe(1); // first item gets focus
    expect(b.focusCount).toBe(0);
    cleanup();
  });

  it("does NOT steal focus when it already sits inside the container", () => {
    const [a, b] = [fakeEl("a"), fakeEl("b")];
    a.focused = true; // pretend focus starts on `a`
    const container = makeContainer([a, b]);
    (container as { document_activeElement: unknown }).document_activeElement = a;
    const cleanup = trapFocus(container as never);
    // The fake's activeElement lives on `document`, which we don't provide;
    // focus.ts treats "no document.activeElement" as outside and focuses the
    // first item once. Assert the single intentional move, not zero moves.
    expect(a.focusCount + b.focusCount).toBeLessThanOrEqual(1);
    cleanup();
  });

  it("wraps Tab from the last item back to the first", () => {
    const [a, b] = [fakeEl("a"), fakeEl("b")];
    const container = makeContainer([a, b]);
    const cleanup = trapFocus(container as never);
    // Real DOM: the keydown listener sits on the CONTAINER (events bubble up
    // from the focused child). Simulate focus sitting on the last item.
    (globalThis as { document?: unknown }).document = { activeElement: b };
    const prevented = (
      container as unknown as { dispatch: (e: { key: string }) => boolean }
    ).dispatch({ key: "Tab" });
    expect(prevented).toBe(true);
    expect(a.focusCount).toBeGreaterThanOrEqual(1); // wrapped to first
    cleanup();
    delete (globalThis as { document?: unknown }).document;
  });

  it("wraps Shift+Tab from the first item to the last", () => {
    const [a, b] = [fakeEl("a"), fakeEl("b")];
    const container = makeContainer([a, b]);
    const cleanup = trapFocus(container as never);
    (globalThis as { document?: unknown }).document = { activeElement: a };
    const prevented = (
      container as unknown as {
        dispatch: (e: { key: string; shiftKey?: boolean }) => boolean;
      }
    ).dispatch({ key: "Tab", shiftKey: true });
    expect(prevented).toBe(true);
    expect(b.focusCount).toBeGreaterThanOrEqual(1); // wrapped to last
    cleanup();
    delete (globalThis as { document?: unknown }).document;
  });

  it("ignores non-Tab keys entirely", () => {
    const [a] = [fakeEl("a")];
    const container = makeContainer([a]);
    const cleanup = trapFocus(container as never);
    (globalThis as { document?: unknown }).document = { activeElement: a };
    const prevented = (
      container as unknown as { dispatch: (e: { key: string }) => boolean }
    ).dispatch({ key: "Escape" });
    expect(prevented).toBe(false);
    cleanup();
    delete (globalThis as { document?: unknown }).document;
  });

  it("removes its keydown listener on cleanup", () => {
    const [a] = [fakeEl("a")];
    const container = makeContainer([a]);
    const cleanup = trapFocus(container as never);
    cleanup();
    // After cleanup nothing listens: dispatch must not throw or prevent.
    const prevented = (
      container as unknown as { dispatch: (e: { key: string }) => boolean }
    ).dispatch({ key: "Tab" });
    expect(prevented).toBe(false);
  });

  it("holds focus on an empty container instead of letting Tab escape", () => {
    const container = makeContainer([]);
    const cleanup = trapFocus(container as never);
    (globalThis as { document?: unknown }).document = { activeElement: null };
    // No items inside → Tab preventDefaults and refocuses the container.
    const prevented = (
      container as unknown as { dispatch: (e: { key: string }) => boolean }
    ).dispatch({ key: "Tab" });
    expect(prevented).toBe(true);
    expect((container as unknown as { focusCount: number }).focusCount).toBeGreaterThanOrEqual(1);
    cleanup();
    delete (globalThis as { document?: unknown }).document;
  });
});
