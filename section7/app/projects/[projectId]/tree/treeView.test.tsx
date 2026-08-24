// M12-G8 — repo tree component tests.
// Node-env constraint (no jsdom/RTL in this repo; installs are out of scope):
// presentational exports are rendered with react-dom/server's real renderer
// and asserted on emitted markup, same protocol as runsTable.test.tsx /
// planGate.test.tsx. The lazy per-folder loading behavior lives in the exported
// pure treeReducer/shouldFetchOnToggle machine, which these tests drive
// directly (static rendering doesn't run effects, so fetch stubbing can't
// observe mount-time loads).
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  FileMetaPanel,
  TreeBreadcrumbs,
  TreeNode,
  initialTreeState,
  shouldFetchOnToggle,
  treeReducer,
} from "./RepoTreeView";
import type { SelectedFile } from "./RepoTreeView";

function dirEntry(name: string) {
  return { name, type: "dir" as const, size: 0 };
}
function fileEntry(name: string, size: number) {
  return { name, type: "file" as const, size };
}

describe("TreeBreadcrumbs", () => {
  it("renders root crumb as the project name plus one anchor per ancestor", () => {
    const html = renderToStaticMarkup(
      <TreeBreadcrumbs dir="src/lib" projectName="proj-1" onNavigate={() => {}} />,
    );
    expect(html).toContain(">proj-1</a>");
    expect(html).toContain(">src</a>");
    expect(html).toContain(">lib</a>");
    expect(html).toContain('aria-label="Workspace path"');
  });

  it("renders only the project crumb at the workspace root", () => {
    const html = renderToStaticMarkup(
      <TreeBreadcrumbs dir="" projectName="proj-1" onNavigate={() => {}} />,
    );
    expect(html).toContain(">proj-1</a>");
    expect(html).not.toContain(" / ");
  });
});

describe("TreeNode", () => {
  it("renders a collapsed dir row with aria-expanded=false and its path", () => {
    const html = renderToStaticMarkup(
      <ul>
        <TreeNode
          entry={dirEntry("src")}
          path="src"
          depth={0}
          onToggleDir={() => {}}
          onOpenFile={() => {}}
          selectedPath={null}
        />
      </ul>,
    );
    expect(html).toContain('role="treeitem"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('data-path="src"');
    expect(html).toContain("src/");
  });

  it("renders an open dir with nested children rows (depth indentation)", () => {
    const html = renderToStaticMarkup(
      <ul>
        <TreeNode
          entry={dirEntry("src")}
          path="src"
          depth={0}
          state="ready"
          childrenEntries={[fileEntry("main.py", 12)]}
          onToggleDir={() => {}}
          onOpenFile={() => {}}
          selectedPath={null}
        />
      </ul>,
    );
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('data-path="src/main.py"');
    expect(html).toContain("main.py");
    expect(html).toContain('role="group"');
  });

  it("marks the selected file row with aria-selected", () => {
    const html = renderToStaticMarkup(
      <ul>
        <TreeNode
          entry={fileEntry("README.md", 5)}
          path="README.md"
          depth={0}
          onToggleDir={() => {}}
          onOpenFile={() => {}}
          selectedPath="README.md"
        />
      </ul>,
    );
    expect(html).toContain('aria-selected="true"');
  });
});

describe("FileMetaPanel", () => {
  it("shows name, path and human size for a selected file", () => {
    const html = renderToStaticMarkup(
      <FileMetaPanel file={{ path: "src/main.py", name: "main.py", size: 12 }} />,
    );
    expect(html).toContain(">main.py</h2>");
    expect(html).toContain("src/main.py");
    expect(html).toContain("12 B");
    expect(html).toContain("(12 bytes)");
  });

  it("says honestly that content viewing lands in W3 — no fake viewer", () => {
    const html = renderToStaticMarkup(
      <FileMetaPanel file={{ path: "a.txt", name: "a.txt", size: 1 }} />,
    );
    expect(html).toContain("Content viewer lands in W3");
  });

  it("renders the neutral empty-state prompt when nothing is selected", () => {
    const html = renderToStaticMarkup(<FileMetaPanel file={null} />);
    expect(html).toContain("Select a file to see its details.");
    expect(html).not.toContain("Content viewer");
  });
});

describe("treeReducer lazy-loading state machine", () => {
  it("walks loading → ready per dir, keeping each folder's rows independent", () => {
    let state = treeReducer(initialTreeState, { type: "loading", dir: "" });
    expect(state.stateByDir[""]).toBe("loading");
    expect(state.entriesByDir[""]).toBeUndefined();

    state = treeReducer(state, {
      type: "ready",
      dir: "",
      entries: [dirEntry("src"), fileEntry("README.md", 5)],
    });
    state = treeReducer(state, { type: "loading", dir: "src" });
    // Root rows survive while a child folder loads (lazy per-node fetch).
    expect(state.stateByDir["src"]).toBe("loading");
    expect(state.entriesByDir[""]?.map((e) => e.name)).toEqual(["src", "README.md"]);

    state = treeReducer(state, { type: "ready", dir: "src", entries: [fileEntry("main.py", 12)] });
    expect(state.entriesByDir["src"]).toEqual([fileEntry("main.py", 12)]);
    expect(state.entriesByDir[""]).toHaveLength(2);
  });

  it("marks per-dir errors without wiping other folders' listings", () => {
    let state = treeReducer(initialTreeState, { type: "ready", dir: "", entries: [dirEntry("x")] });
    state = treeReducer(state, { type: "error", dir: "x" });
    expect(state.stateByDir["x"]).toBe("error");
    expect(state.entriesByDir[""]).toEqual([dirEntry("x")]);
  });

  it("collapse releases both rows and cache so reopening refetches lazily", () => {
    let state = treeReducer(initialTreeState, { type: "ready", dir: "src", entries: [fileEntry("main.py", 12)] });
    expect(shouldFetchOnToggle(state.stateByDir, "src")).toBe(false); // open → collapse
    state = treeReducer(state, { type: "collapse", dir: "src" });
    expect(state.entriesByDir["src"]).toBeUndefined();
    expect(state.stateByDir["src"]).toBeUndefined();
    // After collapse the folder must be fetched again on next toggle.
    expect(shouldFetchOnToggle(state.stateByDir, "src")).toBe(true);
  });

  it("unopened and errored folders always trigger a lazy fetch", () => {
    expect(shouldFetchOnToggle({}, "never-opened")).toBe(true);
    expect(shouldFetchOnToggle({ a: "error" }, "a")).toBe(true);
    expect(shouldFetchOnToggle({ a: "loading" }, "a")).toBe(true);
  });

  it("renders an open ready folder's children as nested treeitem rows", () => {
    const html = renderToStaticMarkup(
      <ul>
        <TreeNode
          entry={dirEntry("src")}
          path="src"
          depth={0}
          state="ready"
          childrenEntries={[dirEntry("lib"), fileEntry("main.py", 12)]}
          onToggleDir={() => {}}
          onOpenFile={() => {}}
          selectedPath={null}
        />
      </ul>,
    );
    expect(html).toContain('data-path="src/lib"');
    expect(html).toContain('data-path="src/main.py"');
    // dirs-first ordering is preserved inside nested listings
    expect(html.indexOf("src/lib")).toBeLessThan(html.indexOf("src/main.py"));
  });

  it("renders an empty folder with an honest empty marker", () => {
    const html = renderToStaticMarkup(
      <ul>
        <TreeNode
          entry={dirEntry("empty")}
          path="empty"
          depth={0}
          state="ready"
          childrenEntries={[]}
          onToggleDir={() => {}}
          onOpenFile={() => {}}
          selectedPath={null}
        />
      </ul>,
    );
    expect(html).toContain("empty");
  });
});

describe("SelectedFile contract", () => {
  it("carries exactly path/name/size into the metadata panel", () => {
    const file: SelectedFile = { path: "a/b.txt", name: "b.txt", size: 3 };
    const html = renderToStaticMarkup(<FileMetaPanel file={file} />);
    expect(html).toContain("a/b.txt");
    expect(html).toContain(">b.txt</h2>");
    expect(html).toContain("(3 bytes)");
  });
});

