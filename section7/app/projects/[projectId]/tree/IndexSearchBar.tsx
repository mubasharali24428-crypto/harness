"use client";

// M21-X3 — index-backed search bar for the repo tree browser.
//
// Upgrades the M12-G8 file browser from walk-only to searchable: the query
// runs against Section 11's indexed project (the SAME frozen W21-C1 API the
// meeting composer's @ref autocomplete uses) instead of scraping directories.
// A query ending in a plausible file suffix searches FILES (distinct paths);
// anything else searches SYMBOLS (scored path:line chunk hits).
//
// Honest-degradation law: an unreachable/empty index renders an explicit
// status line — never a fabricated result list, and never a broken tree
// (the lazy S5-backed listing above is untouched).

import { useEffect, useRef, useState } from "react";
import * as React from "react";
import { loadSession } from "@/lib/auth";
import {
  indexSearchMode,
  searchIndex,
  type IndexHitDto,
  type IndexSearchMode,
} from "./treeApi";

export const INDEX_SEARCH_DEBOUNCE_MS = 250;

/** Headless debounce driver for the search bar (injectable fetcher → tests
 *  drive it without network stubs; same pattern as RefAutocomplete's). */
export function createIndexSearch(
  run: (query: string) => Promise<IndexHitDto[]>,
) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return {
    schedule(query: string, onResult: (hits: IndexHitDto[]) => void): void {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        void run(query)
          .then(onResult)
          .catch(() => onResult([]));
      }, INDEX_SEARCH_DEBOUNCE_MS);
    },
    cancel(): void {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}

export type IndexSearchStatus = "idle" | "loading" | "ready" | "error";

export interface IndexSearchBarProps {
  projectId: string;
}

/** Search box + hit list. Renders above the tree inside the Files card. */
export function IndexSearchBar({ projectId }: IndexSearchBarProps) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<IndexHitDto[]>([]);
  const [status, setStatus] = useState<IndexSearchStatus>("idle");
  const [mode, setMode] = useState<IndexSearchMode>("symbols");
  const acRef = useRef<ReturnType<typeof createIndexSearch> | null>(null);

  useEffect(() => {
    const ac = createIndexSearch(async (q) => {
      const res = await searchIndex(projectId, q, loadToken(), { topK: 12 });
      setMode(res.mode);
      return res.hits;
    });
    acRef.current = ac;
    return () => ac.cancel();
  }, [projectId]);

  useEffect(() => {
    const ac = acRef.current;
    if (!ac) return;
    const trimmed = query.trim();
    if (!trimmed) {
      ac.cancel();
      setHits([]);
      setStatus("idle");
      return;
    }
    setStatus("loading");
    setMode(indexSearchMode(trimmed));
    ac.schedule(trimmed, (items) => {
      setHits(items);
      setStatus("ready");
    });
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps -- query IS the trigger

  return (
    <div style={{ marginBottom: 10 }} data-testid="index-search">
      {/* aria-label over placeholder-only naming (M15-S3 accessibility law). */}
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search the index… (file.ext or symbol)"
        aria-label="Search project index"
        style={{
          width: "100%",
          background: "var(--panel-2)",
          border: "1px solid var(--border)",
          color: "var(--text)",
          borderRadius: 6,
          padding: 8,
          font: "inherit",
          fontSize: 13,
        }}
      />
      {status === "loading" && (
        <div style={{ opacity: 0.7, fontSize: 12 }} role="status">
          searching index…
        </div>
      )}
      {status === "ready" && hits.length === 0 && (
        <div style={{ opacity: 0.7, fontSize: 12 }}>
          No indexed matches for “{query.trim()}”.
        </div>
      )}
      {status === "error" && (
        <div style={{ color: "var(--danger, #c33)", fontSize: 12 }} role="alert">
          index search failed — is the context service up?
        </div>
      )}
      <ul
        role="listbox"
        aria-label={`Indexed ${mode} matches`}
        style={{ listStyle: "none", margin: "6px 0 0", padding: 0 }}
      >
        {hits.map((hit) => (
          <li key={`${hit.path}:${hit.startLine}`} role="option" aria-selected={false}>
            <code style={{ fontSize: 12 }}>{hit.path}</code>
            {mode === "symbols" && (
              <span style={{ opacity: 0.75, fontSize: 12 }}> :{hit.startLine}–{hit.endLine}</span>
            )}
            <span className="pill blue" style={{ marginLeft: 8 }}>
              {Number.isFinite(hit.score) ? hit.score.toFixed(3) : "?"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Session token at call time (the tree view reads it lazily too). */
function loadToken(): string {
  return loadSession()?.token ?? "";
}
