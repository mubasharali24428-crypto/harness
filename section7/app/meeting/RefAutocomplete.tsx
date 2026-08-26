"use client";

// M21-X3 — @ref autocomplete for the meeting composer.
//
// UX contract (commander's intent: a stranger watches @refs resolve into
// visible retrieved chunks):
// - typing "@" in the draft opens a suggestion list fed by Section 11's index
//   (W21-C1 search over the partial ref text, debounced);
// - picking a suggestion rewrites the trailing "@partial" into a complete
//   ref — the RESOLVED REF IS THE TEXT; the message body stays plain text,
//   so S6/S4's resolver parses the exact same refs server-side;
// - the composer surfaces the refs the outgoing text carries as chips
//   (kind-labeled), derived purely from the draft;
// - every failure degrades to an honest empty state — autocomplete is
//   additive sugar over a working composer, never a gate on sending.
//
// Node-env test protocol: pure helpers are exported headless and driven
// directly; presentational exports render via react-dom/server.

import { useEffect, useRef, useState } from "react";
import * as React from "react";
import { Pill } from "@/components/ui";
import {
  activeRefToken,
  applyRefSelection,
  parseRefs,
  REF_SUGGESTION_LIMIT,
  searchContext,
  type ParsedRef,
} from "@/lib/contextApi";

// Single source of truth for the suggestion cap lives in the API client; the
// component surface keeps the same name so callers/tests stay stable.
export { REF_SUGGESTION_LIMIT };

export const REF_DEBOUNCE_MS = 250;

/** Pure: distinct paths from chunk hits → file suggestions for the menu. */
export function pathsFromChunks(
  results: { path: string }[],
  cap: number = REF_SUGGESTION_LIMIT,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of results) {
    if (!r?.path || seen.has(r.path)) continue;
    seen.add(r.path);
    out.push(r.path);
    if (out.length >= cap) break;
  }
  return out;
}

export type RefSuggestState = "idle" | "loading" | "ready" | "error";

export interface RefSuggestion {
  value: string;
  kind: "file";
}

/** Headless autocomplete state machine — owns fetch lifecycle + debounce.
 *  `queryRef` is injectable so tests drive it without network/fetch stubs. */
export function createRefAutocomplete(queryRef: (partial: string) => Promise<string[]>) {
  let timer: ReturnType<typeof setTimeout> | null = null;

  return {
    /** Schedule a debounced lookup for the given partial ref text. */
    schedule(partial: string, onResult: (s: RefSuggestion[]) => void): void {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        void queryRef(partial)
          .then((paths) =>
            onResult(
              paths.slice(0, REF_SUGGESTION_LIMIT).map((value) => ({ value, kind: "file" as const })),
            ),
          )
          .catch(() => onResult([]));
      }, REF_DEBOUNCE_MS);
    },
    cancel(): void {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}

/** The kind chip face for one parsed @ref (blue=file, yellow=symbol). */
export function refChipTone(ref: ParsedRef): "blue" | "yellow" {
  return ref.kind === "file" ? "blue" : "yellow";
}

export interface RefChipsProps {
  draft: string;
}

/** Presentational chip strip: what the outgoing text references right now. */
export function RefChips({ draft }: RefChipsProps) {
  const refs = parseRefs(draft ?? "");
  if (refs.length === 0) return null;
  return (
    <div
      className="chat-input"
      style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}
      data-testid="ref-chips"
      aria-label="Referenced files"
    >
      {refs.map((ref) => (
        <Pill
          key={`${ref.span[0]}`}
          tone={refChipTone(ref)}
          className={`pill ${ref.kind === "file" ? "blue" : "yellow"}`}
        >
          @{ref.name}
        </Pill>
      ))}
    </div>
  );
}

export interface RefAutocompleteProps {
  /** Full current draft text (the input's value). */
  draft: string;
  projectId: string;
  token: string;
  /** Called with the rewritten draft after a suggestion is chosen. */
  onSelect: (nextDraft: string) => void;
  /** Draft setter so the widget can mark loading state around its own input? No —
   *  read-only widget: it never mutates the draft outside selection. */
}

/**
 * Connected autocomplete menu for the composer. Renders NOTHING while no ref
 * is being typed (the common case) — an honest zero-footprint addition.
 */
export function RefAutocomplete({ draft, projectId, token, onSelect }: RefAutocompleteProps) {
  const [suggestions, setSuggestions] = useState<RefSuggestion[]>([]);
  const [state, setState] = useState<RefSuggestState>("idle");
  const acRef = useRef<ReturnType<typeof createRefAutocomplete> | null>(null);

  // One debouncer per mount; canceled on unmount so late replies can't land.
  useEffect(() => {
    const ac = createRefAutocomplete(async (partial) => {
      const res = await searchContext({ query: partial, projectId, topK: 10 }, token);
      return pathsFromChunks(res.results);
    });
    acRef.current = ac;
    return () => ac.cancel();
  }, [projectId, token]);

  const partial = activeRefToken(draft ?? "");
  const open = Boolean(partial);

  useEffect(() => {
    const ac = acRef.current;
    if (!ac) return;
    if (!open) {
      ac.cancel();
      setSuggestions([]);
      setState("idle");
      return;
    }
    setState("loading");
    ac.schedule(partial!, (items) => {
      setSuggestions(items);
      setState("ready");
    });
  }, [partial]); // eslint-disable-line react-hooks/exhaustive-deps -- `partial` fully derives from draft

  if (!open) return null;

  const choose = (value: string) => onSelect(applyRefSelection(draft ?? "", value));

  return (
    <div data-testid="ref-autocomplete" role="listbox" aria-label="File ref suggestions">
      {state === "loading" && suggestions.length === 0 && (
        <div style={{ opacity: 0.7, fontSize: 12 }} role="status">
          searching index…
        </div>
      )}
      {state === "ready" && suggestions.length === 0 && (
        <div style={{ opacity: 0.7, fontSize: 12 }}>No indexed matches.</div>
      )}
      {suggestions.map((s) => (
        <button
          key={s.value}
          type="button"
          role="option"
          aria-selected={false}
          data-ref={s.value}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text)",
            cursor: "pointer",
            display: "block",
            font: "inherit",
            fontSize: 13,
            padding: "3px 6px",
            textAlign: "left",
            width: "100%",
          }}
          onMouseDown={(e) => {
            // mousedown (not click) so the input keeps focus — same trick
            // editor completions use; blur would close the menu first.
            e.preventDefault();
            choose(s.value);
          }}
        >
          @{s.value}
        </button>
      ))}
    </div>
  );
}
