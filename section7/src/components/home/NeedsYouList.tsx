"use client";

// Operability slice 1: Home "Needs you". One list of what is waiting on a person (plan approvals,
// blocked runs, open reviews), oldest first, each row with its age, a one-line reason and one
// primary action, Open. Loading, empty and error are distinct states; a failed load names the
// call and offers Retry, and is never shown as "nothing waiting".
import * as React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { loadErrorMessage } from "@/lib/humanize";
import { MAIN_CONTENT_ID } from "@/lib/focus";
import { listRuns } from "../../../app/runs/runsApi";
import {
  NEEDS_YOU_CALL,
  listKeyTarget,
  needsYouItems,
  openLabel,
  shouldFocusList,
  type NeedsYouItem,
} from "./needsYou";

export const NEEDS_YOU_LIST_ID = "needs-you-list";
const HEADING_ID = "needs-you-heading";

export type NeedsYouState =
  | { status: "loading" }
  | { status: "error"; call: string; message: string }
  | { status: "ready"; items: NeedsYouItem[] };

export interface NeedsYouViewProps {
  state: NeedsYouState;
  onRetry: () => void;
  /** Opens a row's href (Enter on the list opens the first row). */
  onOpen: (href: string) => void;
  listRef?: React.Ref<HTMLUListElement>;
}

/** Hook-free view, so tests drive it directly with fixtures. */
export function NeedsYouView({ state, onRetry, onOpen, listRef }: NeedsYouViewProps) {
  return (
    <section className="ny" aria-labelledby={HEADING_ID}>
      <h2 id={HEADING_ID} className="ny-heading">
        Needs you
        {state.status === "ready" && state.items.length > 0 ? (
          <span className="ws-group-count">
            <span className="sr-only">, </span>
            {state.items.length}
            <span className="sr-only"> waiting</span>
          </span>
        ) : null}
      </h2>
      {state.status === "loading" ? (
        <p className="ny-note" role="status">Loading what needs you…</p>
      ) : state.status === "error" ? (
        <div className="ny-note ny-error" role="alert">
          <p>
            Couldn’t load {state.call}. {state.message}
          </p>
          <button type="button" className="btn" onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : state.items.length === 0 ? (
        <p className="ny-note">
          Nothing waiting. <Link href="/runs">See all runs</Link>
        </p>
      ) : (
        <ul
          id={NEEDS_YOU_LIST_ID}
          ref={listRef}
          className="ny-rows"
          tabIndex={-1}
          aria-labelledby={HEADING_ID}
          aria-describedby="needs-you-hint"
          onKeyDown={(e) => {
            const href = listKeyTarget(e, state.items);
            if (href) {
              e.preventDefault();
              onOpen(href);
            }
          }}
        >
          {state.items.map((item) => (
            <li key={item.id} className="ny-row" data-kind={item.kind}>
              <div className="ny-main">
                <span className="ny-title">{item.title}</span>
                <span className="ny-meta">
                  <span className="ny-kind">{item.kindLabel}:</span> {item.reason}
                  <span aria-hidden="true"> · </span>
                  <span className="sr-only">, waiting since </span>
                  {item.since ? (
                    <time dateTime={item.since} title={item.sinceTitle}>
                      {item.age}
                    </time>
                  ) : (
                    <span>time unknown</span>
                  )}
                </span>
              </div>
              <Link href={item.href} className="btn primary ny-open" aria-label={openLabel(item)}>
                Open
              </Link>
            </li>
          ))}
        </ul>
      )}
      {state.status === "ready" && state.items.length > 0 ? (
        <p id="needs-you-hint" className="sr-only">
          Press Enter to open the first item, or Tab to a row.
        </p>
      ) : null}
    </section>
  );
}

/** One load of the list as a state: a failure names the call and keeps the server's reason. */
export async function loadNeedsYou(token: string, now: number = Date.now()): Promise<NeedsYouState> {
  try {
    const page = await listRuns({ limit: 100 }, token);
    return { status: "ready", items: needsYouItems(page.items, now) };
  } catch (e) {
    return { status: "error", call: NEEDS_YOU_CALL, message: loadErrorMessage(e) };
  }
}

/** Loads the runs list, derives the rows and moves focus to the list when the page was just reached. */
export function NeedsYouList({ token }: { token: string }) {
  const router = useRouter();
  const [state, setState] = useState<NeedsYouState>({ status: "loading" });
  const [reload, setReload] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    let alive = true;
    setState({ status: "loading" });
    void loadNeedsYou(token).then((next) => alive && setState(next));
    return () => {
      alive = false;
    };
  }, [token, reload]);

  useEffect(() => {
    if (state.status !== "ready" || state.items.length === 0) return;
    if (typeof document !== "undefined" && shouldFocusList(document.activeElement, MAIN_CONTENT_ID)) {
      listRef.current?.focus({ preventScroll: true });
    }
  }, [state]);

  const onRetry = useCallback(() => setReload((k) => k + 1), []);
  const onOpen = useCallback((href: string) => router.push(href), [router]);
  return <NeedsYouView state={state} onRetry={onRetry} onOpen={onOpen} listRef={listRef} />;
}
