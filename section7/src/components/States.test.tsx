import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { EmptyState, ErrorState, LoadingState } from "./States";
import { loadErrorMessage } from "@/lib/humanize";

describe("page states", () => {
  it("announces loading without claiming content is ready", () => {
    const html = renderToStaticMarkup(<LoadingState label="Loading runs…" rows={2} />);
    expect(html).toContain('role="status"');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("Loading runs…");
  });

  it("renders an empty state with an optional next step", () => {
    const html = renderToStaticMarkup(
      <EmptyState
        title="No runs yet"
        body="Start from the Command Center."
        action={<a href="/">Open</a>}
      />,
    );
    expect(html).toContain("No runs yet");
    expect(html).toContain("Start from the Command Center.");
    expect(html).toContain('href="/"');
  });

  it("renders a retry action only when a handler exists", () => {
    const plain = renderToStaticMarkup(<ErrorState message="boom" />);
    expect(plain).toContain('role="alert"');
    expect(plain).not.toContain("Try again");

    const retry = vi.fn();
    const withRetry = renderToStaticMarkup(<ErrorState message="boom" onRetry={retry} />);
    expect(withRetry).toContain("Try again");
  });
});

describe("transport failures read as sentences", () => {
  it("replaces raw fetch text", () => {
    expect(loadErrorMessage(new TypeError("Failed to fetch"))).toBe(
      "We couldn’t reach the server. Check your connection, then try again.",
    );
    expect(loadErrorMessage(new Error("Load failed"))).toContain("couldn’t reach the server");
  });

  it("passes server-written messages through unchanged", () => {
    expect(loadErrorMessage(new Error("run not found (404)"))).toBe("run not found (404)");
    expect(loadErrorMessage(null)).toBe("Something went wrong while loading this page.");
  });
});
