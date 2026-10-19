// M14-F1b (raw-03 P2) — keyboard support + focus management helpers.
//
// Two primitives, both dependency-free so they work in node-env vitest
// (renderToStaticMarkup / real DOM in jsdom-less tests):
//
//   • trapFocus(container)  → cleanup fn. Keeps Tab/Shift+Tab inside a
//     container (modals, inline confirms). Pure DOM — no React.
//   • useSkipTarget(id)     → { skipHref, onSkipActivate }. Gives the
//     AppShell skip-link a real href AND an onClick that works even when the
//     app is served from a file:// or hash-router context where fragment
//     navigation alone wouldn't move focus. Returns stable strings/handlers.
//
// Convention shared with globals.css: the main content region carries
// id="main-content" (tabindex="-1" so it can receive focus programmatically),
// and the skip link is the FIRST element in the shell markup.

/** Selector matching every natively focusable control we actually render. */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

/** True when the element can actually take focus right now (not display:none,
 *  not hidden via aria). Cheap heuristic — good enough for trap loops. In
 *  non-DOM environments (node-env vitest with hand-built fakes) we trust the
 *  selector pass and treat every match as focusable. */
function isActuallyFocusable(el: Element): boolean {
  if (typeof window === "undefined" || typeof window.getComputedStyle !== "function") {
    return true;
  }
  const style = window.getComputedStyle(el);
  if (style.display === "none" || style.visibility === "hidden") return false;
  return el.getAttribute?.("aria-hidden") !== "true";
}

/**
 * Keep keyboard focus inside `container` while it is open.
 *
 * Returns a cleanup function that removes the listener; callers own the
 * lifecycle (useEffect return, unmount handler, …). Focus is moved into the
 * container first if it's currently outside — a trapped region you can't
 * reach into would be a black hole for keyboard users.
 */
export function trapFocus(container: HTMLElement): () => void {
  const focusablesIn = (): HTMLElement[] =>
    Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
      isActuallyFocusable,
    );

  // Move focus into the container if it's currently outside.
  const initialFocusables = focusablesIn();
  const active = typeof document !== "undefined" ? document.activeElement : null;
  if (!active || !container.contains(active)) {
    (initialFocusables[0] ?? container).focus();
  }

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key !== "Tab") return;
    const items = focusablesIn();
    if (items.length === 0) {
      // Nothing tabbable: hold focus on the container instead of letting Tab
      // escape to the page behind the overlay.
      event.preventDefault();
      container.focus();
      return;
    }
    const first = items[0]!;
    const last = items[items.length - 1]!;
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !container.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !container.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  };

  container.addEventListener("keydown", onKeyDown);
  return () => container.removeEventListener("keydown", onKeyDown);
}

/** DOM id of the shell's main content landmark — single source of truth. */
export const MAIN_CONTENT_ID = "main-content";

/** Fragment href the skip link points at. */
export const SKIP_HREF = `#${MAIN_CONTENT_ID}`;

/**
 * Behavior bundle for the skip link ("Skip to content").
 *
 * The href alone moves focus in browsers that honor fragment navigation onto
 * tabindex="-1" targets; the click handler covers the rest by moving focus
 * explicitly (and scrolling the target into view) without breaking middle-
 * click/copy-link semantics — it only preventDefaults when the target exists.
 */
export function useSkipTarget(targetId: string = MAIN_CONTENT_ID): {
  skipHref: string;
  onSkipActivate: (event: { preventDefault(): void }) => void;
} {
  const skipHref = `#${targetId}`;
  const onSkipActivate = (event: { preventDefault(): void }): void => {
    if (typeof document === "undefined") return;
    const target = document.getElementById(targetId);
    if (!target) return; // let native fragment navigation try its best
    event.preventDefault();
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "start" });
  };
  return { skipHref, onSkipActivate };
}

/**
 * Part 56: call this in the onClick of a button whose action removes or disables it (accept, reject,
 * take control, offer handoff ...). Focus moves to the nearest ancestor marked `data-focus-park`
 * (give it tabIndex={-1}) BEFORE the button disappears, so a keyboard user is never dropped to <body>.
 */
export function parkFocus(e: { currentTarget: unknown }): void {
  const from = e.currentTarget as { closest?: (sel: string) => { focus?: (o?: object) => void } | null } | null;
  from?.closest?.("[data-focus-park]")?.focus?.({ preventScroll: true });
}
