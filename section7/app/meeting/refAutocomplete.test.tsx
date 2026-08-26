// M21-X3 — @ref autocomplete wiring tests.
//
// Node-env protocol: pure helpers (createRefAutocomplete state machine,
// pathsFromChunks, refChipTone) driven directly with an injected query
// function; presentational exports rendered via react-dom/server. The
// debounce is real — vi.useFakeTimers advances it so the test proves the
// composer fires ONE debounced lookup per pause, not one per keystroke.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  createRefAutocomplete,
  pathsFromChunks,
  REF_DEBOUNCE_MS,
  REF_SUGGESTION_LIMIT,
  RefChips,
  refChipTone,
} from "./RefAutocomplete";

afterEach(() => {
  vi.useRealTimers();
});

describe("pathsFromChunks", () => {
  it("maps chunk hits to distinct suggestion paths, capped", () => {
    const hits = [
      { path: "src/a.py" },
      { path: "src/b.tsx" },
      { path: "src/a.py" }, // dup
      { path: "" }, // junk
      { path: "src/c.go" },
    ];
    const paths = pathsFromChunks(hits, 3);
    expect(paths).toEqual(["src/a.py", "src/b.tsx", "src/c.go"]);
  });

  it("caps at the shared REF_SUGGESTION_LIMIT by default", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ path: `f${i}.py` }));
    expect(pathsFromChunks(many)).toHaveLength(REF_SUGGESTION_LIMIT);
  });
});

describe("refChipTone", () => {
  it("files are blue, symbols yellow (shared four-tone vocabulary)", () => {
    expect(refChipTone({ kind: "file", name: "a.py", span: [0, 5] })).toBe("blue");
    expect(refChipTone({ kind: "symbol", name: "Auth", span: [0, 5] })).toBe("yellow");
  });
});

describe("createRefAutocomplete (debounced wiring)", () => {
  it("debounces bursts into ONE query after the pause", async () => {
    vi.useFakeTimers();
    const queryRef = vi.fn(async () => ["src/auth.py"]);
    const ac = createRefAutocomplete(queryRef);
    const onResult = vi.fn();

    ac.schedule("@a".slice(1), onResult);
    await vi.advanceTimersByTimeAsync(REF_DEBOUNCE_MS - 50);
    ac.schedule("a", onResult); // keystroke inside the window reschedules
    expect(queryRef).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(REF_DEBOUNCE_MS);
    expect(queryRef).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith([{ value: "src/auth.py", kind: "file" }]);
  });

  it("an error degrades to an empty honest list, never a throw", async () => {
    vi.useFakeTimers();
    const ac = createRefAutocomplete(async () => {
      throw new Error("index down");
    });
    const onResult = vi.fn();
    ac.schedule("auth", onResult);
    await vi.advanceTimersByTimeAsync(REF_DEBOUNCE_MS);
    expect(onResult).toHaveBeenCalledWith([]);
  });

  it("cancel() prevents a pending lookup from ever firing (unmount safety)", async () => {
    vi.useFakeTimers();
    const queryRef = vi.fn(async () => ["src/auth.py"]);
    const ac = createRefAutocomplete(queryRef);
    const onResult = vi.fn();
    ac.schedule("auth", onResult);
    ac.cancel();
    await vi.advanceTimersByTimeAsync(REF_DEBOUNCE_MS * 2);
    expect(queryRef).not.toHaveBeenCalled();
    expect(onResult).not.toHaveBeenCalled();
  });
});

describe("RefChips (resolved refs made visible)", () => {
  it("renders one kind-labeled chip per parsed ref in the draft", () => {
    const html = renderToStaticMarkup(
      <RefChips draft="touch @src/util/auth.py then @AuthService." />,
    );
    expect(html).toContain('data-testid="ref-chips"');
    expect(html).toContain("@src/util/auth.py");
    expect(html).toContain("@AuthService");
    // kind faces over the .pill class hooks:
    expect(html).toContain('class="pill blue"');
    expect(html).toContain('class="pill yellow"');
  });

  it("renders nothing for a ref-less draft (zero-footprint)", () => {
    const html = renderToStaticMarkup(<RefChips draft="no refs in here" />);
    expect(html).not.toContain("ref-chips");
  });

  it("keeps duplicate mentions as separate chips (spans differ)", () => {
    const html = renderToStaticMarkup(<RefChips draft="@a.py and @a.py again" />);
    expect(html.match(/@a\.py</g)).toHaveLength(2);
  });
});
