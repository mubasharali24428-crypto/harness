"use client";

// M12-G8 — read-only repo file tree (part3 gap #7 slice 1, the N9/K3 BLOCKER
// unblocker). Client of the S5 route `GET /api/v1/sandbox/{projectId}/tree`
// served by section5/src/harness_sandbox/tree_api.py.
//
// UX contract for this slice:
// - folders expand lazily: children are fetched per node the first time the
//   folder is opened, collapsed folders release their fetched rows;
// - clicking a file opens a metadata panel (name/path/type/size). There is
//   deliberately NO content viewer yet — it lands in W3 — so the panel says
//   exactly that instead of pretending;
// - breadcrumbs above the tree jump-expand ancestor folders;
// - every failure (not signed in, project without an active sandbox, rejected
//   path) renders an honest message — never a fabricated tree.

import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import { displayErrorMessage } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { IndexSearchBar } from "./IndexSearchBar";
import {
  TreeEntryDto,
  formatFileSize,
  listTree,
  normalizeTreePath,
  treeBreadcrumbs,
} from "./treeApi";

const cardStyle = { maxWidth: 1100, marginBottom: 20 } as const;

const rowButtonStyle = {
  background: "transparent",
  border: "none",
  color: "var(--text)",
  cursor: "pointer",
  font: "inherit",
  padding: "3px 6px",
  textAlign: "left",
  width: "100%",
} as const;

export type DirState = "loading" | "ready" | "error";

/** Pure per-directory tree state machine (M12-G8 lazy loading).
 *  `entriesByDir` caches one listing per opened dir path; `stateByDir` tracks
 *  loading/ready/error per dir. Collapsing a folder drops BOTH rows and cache
 *  so reopening refetches — children are always fetched per node, lazily. */
export interface TreeState {
  entriesByDir: Record<string, TreeEntryDto[]>;
  stateByDir: Record<string, DirState>;
}

export const initialTreeState: TreeState = { entriesByDir: {}, stateByDir: {} };

export type TreeAction =
  | { type: "loading"; dir: string }
  | { type: "ready"; dir: string; entries: TreeEntryDto[] }
  | { type: "error"; dir: string }
  | { type: "collapse"; dir: string };

function omitKey<T>(
  record: Record<string, T>,
  dir: string,
): Record<string, T> {
  const next = { ...record };
  delete next[dir];
  return next;
}

export function treeReducer(state: TreeState, action: TreeAction): TreeState {
  switch (action.type) {
    case "loading":
      return {
        ...state,
        stateByDir: { ...state.stateByDir, [action.dir]: "loading" },
      };
    case "ready":
      return {
        entriesByDir: { ...state.entriesByDir, [action.dir]: action.entries },
        stateByDir: { ...state.stateByDir, [action.dir]: "ready" },
      };
    case "error":
      return {
        ...state,
        stateByDir: { ...state.stateByDir, [action.dir]: "error" },
      };
    case "collapse":
      return {
        entriesByDir: omitKey(state.entriesByDir, action.dir),
        stateByDir: omitKey(state.stateByDir, action.dir),
      };
  }
}

/** Pure: whether toggling `dir` must hit the network (anything but an open
 *  ready folder — those collapse instead of refetching). */
export function shouldFetchOnToggle(
  stateByDir: Record<string, DirState>,
  dir: string,
): boolean {
  return stateByDir[dir] !== "ready";
}

export interface SelectedFile {
  path: string;
  name: string;
  size: number;
}

function joinChild(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name;
}

/** Presentational breadcrumb bar. Each crumb is a real button; the first one
 *  always represents the workspace root and shows the project id. */
export function TreeBreadcrumbs({
  dir,
  projectName,
  onNavigate,
}: {
  dir: string;
  projectName: string;
  onNavigate: (path: string) => void;
}) {
  const crumbs = useMemo(
    () => treeBreadcrumbs(dir, projectName),
    [dir, projectName],
  );
  return (
    <nav aria-label="Workspace path" style={{ marginBottom: 8, fontSize: 13 }}>
      {crumbs.map((crumb, i) => (
        <span key={crumb.path || "__root__"}>
          {i > 0 && <span style={{ opacity: 0.6 }}> / </span>}
          <a
            href={`#/tree/${crumb.path}`}
            onClick={(e) => {
              e.preventDefault();
              onNavigate(crumb.path);
            }}
            style={{ color: "var(--accent)", textDecoration: "underline" }}
          >
            {crumb.name}
          </a>
        </span>
      ))}
    </nav>
  );
}

/** One row of the tree. Dirs toggle expansion; files report selection. */
export function TreeNode({
  entry,
  path,
  depth,
  state,
  childrenEntries,
  onToggleDir,
  onOpenFile,
  selectedPath,
}: {
  entry: TreeEntryDto;
  path: string;
  depth: number;
  state?: DirState;
  childrenEntries?: TreeEntryDto[];
  onToggleDir: (dir: string) => void;
  onOpenFile: (file: SelectedFile) => void;
  selectedPath: string | null;
}) {
  const isDir = entry.type === "dir";
  const open = isDir && state === "ready";
  return (
    <li role="none">
      <button
        type="button"
        role="treeitem"
        aria-expanded={isDir ? open : undefined}
        aria-selected={!isDir && selectedPath === path ? true : undefined}
        data-path={path}
        style={{ ...rowButtonStyle, paddingLeft: 6 + depth * 16 }}
        onClick={() => {
          if (isDir) onToggleDir(path);
          else onOpenFile({ path, name: entry.name, size: entry.size });
        }}
      >
        {isDir ? (
          <>
            <span aria-hidden="true">{state === "error" ? "⚠" : open ? "▾" : "▸"}</span>{" "}
            {entry.name}/
          </>
        ) : (
          <>
            <span aria-hidden="true">·</span> {entry.name}
          </>
        )}
      </button>
      {isDir && state === "loading" && (
        <div style={{ paddingLeft: 22 + depth * 16, opacity: 0.7, fontSize: 12 }}>
          loading…
        </div>
      )}
      {isDir && state === "error" && (
        <div style={{ paddingLeft: 22 + depth * 16, color: "var(--danger, #c33)", fontSize: 12 }}>
          could not load this folder
        </div>
      )}
      {isDir && open && (
        <ul role="group" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {childrenEntries?.map((child) => (
            <TreeNode
              key={`${path}/${child.name}`}
              entry={child}
              path={joinChild(path, child.name)}
              depth={depth + 1}
              onToggleDir={onToggleDir}
              onOpenFile={onOpenFile}
              selectedPath={selectedPath}
            />
          ))}
          {childrenEntries?.length === 0 && (
            <li role="none" style={{ paddingLeft: 22 + depth * 16, opacity: 0.7, fontSize: 12 }}>
              empty
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

/** Metadata placeholder for the selected file. Honest by design: no content
 *  viewer exists in this slice (W3 scope), so the panel says exactly that. */
export function FileMetaPanel({ file }: { file: SelectedFile | null }) {
  if (!file) {
    return (
      <section className="card" style={cardStyle} data-testid="file-meta-empty">
        Select a file to see its details.
      </section>
    );
  }
  return (
    <section className="card" style={cardStyle} aria-label={`File ${file.path}`} data-testid="file-meta">
      <h2 style={{ marginTop: 0 }}>{file.name}</h2>
      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 16px", fontSize: 13 }}>
        <dt style={{ fontWeight: 600 }}>Path</dt>
        <dd style={{ margin: 0 }}>{file.path}</dd>
        <dt style={{ fontWeight: 600 }}>Type</dt>
        <dd style={{ margin: 0 }}>file</dd>
        <dt style={{ fontWeight: 600 }}>Size</dt>
        <dd style={{ margin: 0 }}>
          {formatFileSize(file.size)} ({file.size} bytes)
        </dd>
      </dl>
      <p style={{ fontSize: 12, opacity: 0.75 }}>
        Content viewer lands in W3 — this slice is read-only metadata.
      </p>
    </section>
  );
}

/** Interactive tree body: owns lazy per-folder fetch state. Rendered by the
 *  route once Next resolves the [projectId] param; tests drive the pure state
 *  machine (treeReducer) directly since static rendering doesn't run effects. */
export function RepoTreeView({ projectId }: { projectId: string }) {
  const [state, dispatch] = useReducer(treeReducer, initialTreeState);
  const { entriesByDir, stateByDir } = state;
  const [selected, setSelected] = useState<SelectedFile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeDir, setActiveDir] = useState("");

  const loadDir = useCallback(
    async (dir: string) => {
      const session = loadSession();
      if (!session) {
        setError("Not signed in — log in first.");
        dispatch({ type: "error", dir });
        return;
      }
      setActiveDir(normalizeTreePath(dir));
      dispatch({ type: "loading", dir });
      try {
        const listing = await listTree(projectId, dir, session.token);
        dispatch({ type: "ready", dir, entries: listing.entries });
      } catch (e) {
        setError(displayErrorMessage(e));
        dispatch({ type: "error", dir });
      }
    },
    [projectId],
  );

  useEffect(() => {
    void loadDir("");
  }, [loadDir]);

  const toggleDir = useCallback(
    (dir: string) => {
      setSelected(null);
      setActiveDir(dir);
      if (!shouldFetchOnToggle(stateByDir, dir)) {
        // Collapse: release the fetched rows; reopening refetches lazily.
        dispatch({ type: "collapse", dir });
        return;
      }
      void loadDir(dir);
    },
    [stateByDir, loadDir],
  );

  const navigateTo = useCallback(
    (dir: string) => {
      setSelected(null);
      setActiveDir(normalizeTreePath(dir));
      if (stateByDir[dir] !== "ready") void loadDir(dir);
    },
    [stateByDir, loadDir],
  );

  const rootEntries = entriesByDir[""];
  const rootState = stateByDir[""];

  return (
    <section className="card" style={cardStyle}>
      <h1>Files</h1>
      {/* M21-X3: searchable symbol/file index over Section 11's frozen
          W21-C1 API — same index the meeting @ref autocomplete uses. */}
      <IndexSearchBar projectId={projectId} />
      {error && (
        <p role="alert" style={{ color: "var(--danger, #c33)" }}>
          {error}
        </p>
      )}
      <TreeBreadcrumbs dir={activeDir} projectName={projectId} onNavigate={navigateTo} />
      <div
        role="tree"
        aria-label={`Repository files for ${projectId}`}
        data-testid="repo-tree"
        style={{ minHeight: 40, fontFamily: "ui-monospace, monospace", fontSize: 13 }}
      >
        {rootState === "loading" && <div style={{ opacity: 0.7 }}>loading…</div>}
        {rootState === "error" && !error && <div>could not load this workspace</div>}
        {rootState === "ready" &&
          (rootEntries ?? []).map((entry) => (
            <ul key={`root-${entry.name}`} role="presentation" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              <TreeNode
                entry={entry}
                path={entry.name}
                depth={0}
                state={entry.type === "dir" ? stateByDir[entry.name] : undefined}
                childrenEntries={
                  entry.type === "dir" ? entriesByDir[entry.name] : undefined
                }
                onToggleDir={toggleDir}
                onOpenFile={setSelected}
                selectedPath={selected?.path ?? null}
              />
            </ul>
          ))}
        {rootState === "ready" && (rootEntries ?? []).length === 0 && (
          <div style={{ opacity: 0.7 }}>This workspace has no active sandbox content yet.</div>
        )}
      </div>
      <FileMetaPanel file={selected} />
    </section>
  );
}
