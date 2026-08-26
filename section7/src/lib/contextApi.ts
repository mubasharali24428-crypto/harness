// M21-X3 — context pillar API client (W21-C1 consumer side).
//
// Talks to Section 11's frozen search contract:
//   POST /api/v1/context/search {query, projectId, topK}
//     → {projectId, query, results:[{path, lang, startLine, endLine, content, score}]}
// (camelCase wire per contracts/api-conventions.md; the result-item shape is
// FROZEN — X2's resolver and X4's planner build on it — so this client only
// shape-guards defensively and never renames or drops fields.)
//
// Also hosts the CLIENT MIRROR of W21-C2's @ref syntax. The grammar is owned
// by Section 4's resolver (harness_orchestrator/context_resolver.py) — this
// file deliberately re-implements its observable rules (parse + retrieval
// query) so the UI suggests/labels refs EXACTLY as the backend will resolve
// them; the two must move together if the grammar ever evolves.
//
// Conventions match runsApi.ts / treeApi.ts: ApiError + parseApiError over a
// local fetch wrapper, pure helpers exported for node-env vitest.

import { ApiError, parseApiError } from "@/lib/api";

/** Section 11 base (same NEXT_PUBLIC convention as every other service). */
export const CONTEXT_BASE =
  process.env.NEXT_PUBLIC_CONTEXT_BASE_URL ?? "http://localhost:8011";

// ---------------------------------------------------------------------------
// Frozen W21-C1 wire shapes
// ---------------------------------------------------------------------------

/** One scored chunk with its exact source location — FROZEN, additive-only. */
export interface ScoredChunkDto {
  path: string;
  lang: string;
  startLine: number;
  endLine: number;
  content: string;
  score: number;
}

export interface ContextSearchResponseDto {
  projectId: string;
  query: string;
  results: ScoredChunkDto[];
}

/** POST /api/v1/context/search body. topK mirrors the server cap [1,50]. */
export interface ContextSearchQuery {
  query: string;
  projectId: string;
  topK?: number;
}

export const CONTEXT_SEARCH_MAX_TOP_K = 50;

/** Shared cap: how many distinct suggestions/results list-shaped consumers
 *  show (composer menu, tree search's file mode). One knob, two surfaces. */
export const REF_SUGGESTION_LIMIT = 5;

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${CONTEXT_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  return body as T;
}

/** Pure: clamp topK into the server-validated [1, 50] window (default 8). */
export function clampTopK(topK: number | undefined): number {
  const n = typeof topK === "number" && Number.isFinite(topK) ? Math.floor(topK) : 8;
  return Math.min(CONTEXT_SEARCH_MAX_TOP_K, Math.max(1, n));
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Pure: accept only rows that carry the FULL frozen field set with sane types. */
function guardChunk(raw: unknown): ScoredChunkDto | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  return isFiniteNumber(r.startLine) &&
    isFiniteNumber(r.endLine) &&
    isFiniteNumber(r.score) &&
    typeof r.path === "string" &&
    typeof r.lang === "string" &&
    typeof r.content === "string"
    ? { path: r.path, lang: r.lang, startLine: r.startLine, endLine: r.endLine, content: r.content, score: r.score }
    : null;
}

/**
 * POST /api/v1/context/search — W21-C1. Team scoping happens server-side via
 * the bearer token. A drifted body degrades to an honest empty result list,
 * never a crash on results.map.
 */
export async function searchContext(
  query: ContextSearchQuery,
  token: string,
): Promise<ContextSearchResponseDto> {
  const trimmed = query.query.trim();
  if (!trimmed) {
    // Server 422s on empty queries; short-circuit honestly instead.
    return { projectId: query.projectId, query: query.query, results: [] };
  }
  const data = await request<Partial<ContextSearchResponseDto>>(
    "/api/v1/context/search",
    token,
    {
      method: "POST",
      body: JSON.stringify({
        query: trimmed,
        projectId: query.projectId,
        topK: clampTopK(query.topK),
      }),
    },
  );
  const results: ScoredChunkDto[] = [];
  if (Array.isArray(data.results)) {
    for (const raw of data.results) {
      const chunk = guardChunk(raw);
      if (chunk) results.push(chunk);
    }
  }
  return {
    projectId: typeof data.projectId === "string" ? data.projectId : query.projectId,
    query: typeof data.query === "string" ? data.query : query.query,
    results,
  };
}

// ---------------------------------------------------------------------------
// Client mirror of the @ref syntax (W21-C2; grammar owned by S4's resolver)
// ---------------------------------------------------------------------------

export type RefKind = "file" | "symbol";

/** One parsed reference from meeting/brief text (span includes the "@"). */
export interface ParsedRef {
  kind: RefKind;
  name: string;
  span: [number, number];
}

const REF_PATTERN = /@([\w./+-]+)(?<!\.)(?=[.,;:!?)\]]*(\s|$))/g;
const FILE_SUFFIX = /\.[A-Za-z0-9]{1,8}$/;
const SYMBOL_SHAPE = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/;

/** Pure: extract @refs in order of appearance (duplicates kept, spans differ).
 *  Mirrors context_resolver.parse_refs exactly:
 *  - `@path/to/file.ext` → file (token ending in a plausible file suffix)
 *  - `@symbol-name` / `@Class.method` → symbol
 *  - sentence punctuation after the ref stays OUT of the match. */
export function parseRefs(text: string): ParsedRef[] {
  const refs: ParsedRef[] = [];
  for (const m of (text ?? "").matchAll(REF_PATTERN)) {
    const raw = m[1];
    const start = m.index ?? 0;
    let kind: RefKind | null = null;
    if (FILE_SUFFIX.test(raw)) kind = "file";
    else if (SYMBOL_SHAPE.test(raw)) kind = "symbol";
    if (!kind) continue;
    refs.push({ kind, name: raw, span: [start, start + raw.length + 1] });
  }
  return refs;
}

/** Pure: dedupe refs by name keeping first occurrence (for preview fetches). */
export function uniqueRefNames(refs: ParsedRef[]): string[] {
  const seen = new Set<string>();
  const names: string[] = [];
  for (const ref of refs) {
    if (seen.has(ref.name)) continue;
    seen.add(ref.name);
    names.push(ref.name);
  }
  return names;
}

/** Pure: the W21-C1 query a ref resolves through (S4's make_retrieval_query):
 *  file refs retrieve by basename — the index stores relative paths and the
 *  resolver confirms the full path client-side afterwards. */
export function makeRetrievalQuery(refName: string): string {
  return refName.includes("/") ? refName.slice(refName.lastIndexOf("/") + 1) : refName;
}

/** Pure: True when a chunk path lexically confirms a @file ref (basename or
 *  whole-segment path-suffix match; case-sensitive like real code paths). */
export function pathConfirms(chunkPath: string, refName: string): boolean {
  if (!refName || !chunkPath) return false;
  return chunkPath === refName || chunkPath.endsWith(`/${refName}`);
}

// Trailing partial @ref: start-of-text or whitespace/open-paren boundary,
// then "@token" running to the end. (Written without any "[" inside the
// classes — several strict parsers read a bare "[" in a class as nesting.)
const REF_PARTIAL_RE = new RegExp("(?:^|\\s|\\()@([\\w./+-]*)$");

/** Pure: does `draft` end mid-ref (an "@" token still being typed)? Returns
 *  the partial ref text for the suggestion query, else null. */
export function activeRefToken(draft: string): string | null {
  const m = REF_PARTIAL_RE.exec(draft ?? "");
  return m ? m[1] : null;
}

/** Pure: replace the trailing partial ref with the chosen suggestion,
 *  guaranteeing one separating space before what follows. */
export function applyRefSelection(draft: string, selection: string): string {
  const m = REF_PARTIAL_RE.exec(draft ?? "");
  if (!m || m.index === undefined) return draft;
  // The boundary char (space/"("/start) is part of the match; cut at the "@"
  // itself so the separator survives the rewrite.
  const atPos = m.index + m[0].indexOf("@");
  return `${draft.slice(0, atPos)}@${selection} `;
}
