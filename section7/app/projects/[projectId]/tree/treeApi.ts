// M12-G8 — tree API client for the read-only repo browser (part3 gap #7
// slice 1). Consumes the S5 route `GET /api/v1/sandbox/{projectId}/tree?path=`
// (section5/src/harness_sandbox/tree_api.py) with the same bearer/error
// conventions as runsApi.ts: ApiError + parseApiError, light shape guards so a
// drifted/empty body degrades to an honest empty listing instead of a crash.
// Read-only slice — no write verbs exist here by design; content viewing lands
// in W3.

import { ApiError, SANDBOX_BASE, parseApiError } from "@/lib/api";
import {
  clampTopK,
  CONTEXT_BASE,
  REF_SUGGESTION_LIMIT,
  type ContextSearchResponseDto,
} from "@/lib/contextApi";

/** One entry in a workspace directory listing. `size` is always 0 for dirs —
 *  the server refuses to report stat-bookkeeping numbers as payload sizes. */
export interface TreeEntryDto {
  name: string;
  type: "file" | "dir";
  size: number;
}

export interface TreeListingDto {
  path: string;
  entries: TreeEntryDto[];
}

async function request<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${SANDBOX_BASE}${path}`, {
    headers: { authorization: `Bearer ${token}` },
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

/** Pure: normalizes `raw` into a canonical relative path with no leading,
 *  trailing, or doubled slashes. Empty/null → "" (the workspace root). */
export function normalizeTreePath(raw: string | null | undefined): string {
  const trimmed = (raw ?? "").trim().replace(/\/{2,}/g, "/");
  return trimmed.replace(/^\/+/, "").replace(/\/+$/, "");
}

/** Pure: builds `?path=` query value; root stays query-less so URLs stay canonical. */
export function buildTreeQueryString(dir: string): string {
  const normalized = normalizeTreePath(dir);
  return normalized ? `?path=${encodeURIComponent(normalized)}` : "";
}

/**
 * GET /api/v1/sandbox/{projectId}/tree — M12-G8. Team scoping happens
 * server-side via the bearer token. A non-2xx (unknown project, missing dir,
 * traversal attempt rejected) throws ApiError carrying the contract code.
 */
export async function listTree(
  projectId: string,
  dir: string,
  token: string,
): Promise<TreeListingDto> {
  const normalized = normalizeTreePath(dir);
  const data = await request<Partial<TreeListingDto>>(
    `/api/v1/sandbox/${encodeURIComponent(projectId)}/tree${buildTreeQueryString(normalized)}`,
    token,
  );
  // Light shape guard: unknown/missing fields render as an honest empty
  // folder, never as a crash on data.entries.map.
  return {
    path: typeof data.path === "string" ? data.path : normalized,
    entries: Array.isArray(data.entries)
      ? data.entries.filter(
          (e): e is TreeEntryDto =>
            !!e &&
            typeof e.name === "string" &&
            (e.type === "file" || e.type === "dir") &&
            typeof e.size === "number",
        )
      : [],
  };
}

/** Pure: parent directory of a normalized path ("a/b" → "a", "a" → "", "" → ""). */
export function parentTreePath(dir: string): string {
  const normalized = normalizeTreePath(dir);
  const idx = normalized.lastIndexOf("/");
  return idx === -1 ? "" : normalized.slice(0, idx);
}

/** Pure: breadcrumb segments of a normalized path, each with its full path.
 *  Root always comes first as { name: projectName, path: "" }. */
export function treeBreadcrumbs(
  dir: string,
  projectName: string,
): { name: string; path: string }[] {
  const normalized = normalizeTreePath(dir);
  const crumbs = [{ name: projectName, path: "" }];
  let acc = "";
  for (const segment of normalized.split("/")) {
    if (!segment) continue;
    acc = acc ? `${acc}/${segment}` : segment;
    crumbs.push({ name: segment, path: acc });
  }
  return crumbs;
}

const SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Pure: human file size ("512 B", "1.5 KB"); negative/NaN renders honestly as "—". */
export function formatFileSize(size: number): string {
  if (!Number.isFinite(size) || size < 0) return "—";
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = unit === 0 ? String(value) : value.toFixed(1);
  return `${rounded} ${SIZE_UNITS[unit]}`;
}

// ---------------------------------------------------------------------------
// M21-X3 — searchable index slice. The file-tree browser gains a search box
// backed by the SAME Section 11 index as the meeting composer's @ref
// autocomplete (frozen W21-C1 POST /api/v1/context/search). A query that ends
// in a plausible file suffix searches FILES (paths); anything else searches
// SYMBOLS (chunk contents). Pure helpers exported headless for vitest.
// ---------------------------------------------------------------------------

export interface IndexHitDto {
  path: string;
  startLine: number;
  endLine: number;
  score: number;
}

export interface IndexSearchResultDto {
  query: string;
  mode: IndexSearchMode;
  hits: IndexHitDto[];
}

export type IndexSearchMode = "files" | "symbols";

const INDEX_FILE_SUFFIX = /\.[A-Za-z0-9]{1,8}$/;

/** Pure: search mode for a raw query — file-suffix queries browse files,
 *  everything else hunts symbols. Empty/whitespace still yields a mode; the
 *  caller decides whether an empty query is worth firing at all. */
export function indexSearchMode(rawQuery: string): IndexSearchMode {
  return INDEX_FILE_SUFFIX.test((rawQuery ?? "").trim()) ? "files" : "symbols";
}

/** Pure: distinct paths in hit order, capped (the file-mode result face). */
export function pathsFromHits(
  hits: IndexHitDto[],
  cap: number = REF_SUGGESTION_LIMIT,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const h of hits ?? []) {
    if (!h?.path || seen.has(h.path)) continue;
    seen.add(h.path);
    out.push(h.path);
    if (out.length >= cap) break;
  }
  return out;
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Pure: shape-guard W21-C1 rows into IndexHits (drifted rows dropped). */
export function guardIndexHits(results: unknown[]): IndexHitDto[] {
  const hits: IndexHitDto[] = [];
  for (const raw of results ?? []) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    if (
      typeof r.path === "string" &&
      isFiniteNumber(r.startLine) &&
      isFiniteNumber(r.endLine) &&
      isFiniteNumber(r.score)
    ) {
      hits.push({ path: r.path, startLine: r.startLine, endLine: r.endLine, score: r.score });
    }
  }
  return hits;
}

/**
 * POST /api/v1/context/search — the tree browser's index-backed search.
 * Same frozen contract, same bearer/error conventions as listTree above.
 * A drifted body degrades to an honest empty hit list.
 */
export async function searchIndex(
  projectId: string,
  query: string,
  token: string,
  opts?: { topK?: number },
): Promise<IndexSearchResultDto> {
  const trimmed = (query ?? "").trim();
  if (!trimmed) {
    // Server 422s on empty queries; short-circuit honestly instead.
    return { query: trimmed, mode: indexSearchMode(trimmed), hits: [] };
  }
  const res = await fetch(`${CONTEXT_BASE}/api/v1/context/search`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      query: trimmed,
      projectId,
      topK: clampTopK(opts?.topK),
    }),
  });
  const text = await res.text();
  let res_body: unknown = null;
  if (text) {
    try {
      res_body = JSON.parse(text);
    } catch {
      res_body = null;
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, res_body);
    throw new ApiError(message, code, res.status);
  }
  const data = (res_body ?? {}) as Partial<ContextSearchResponseDto>;
  const mode = indexSearchMode(trimmed);
  let hits: IndexHitDto[] = [];
  if (Array.isArray(data.results)) {
    hits = guardIndexHits(data.results);
    if (mode === "files") {
      // File mode presents one row per PATH (a browser, not a chunk reader).
      hits = pathsFromHits(hits).map((path) => ({
        path,
        startLine: 1,
        endLine: 1,
        score: Math.max(...hits.filter((h) => h.path === path).map((h) => h.score)),
      }));
    }
  }
  return { query: trimmed, mode, hits };
}
