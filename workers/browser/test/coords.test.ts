import { describe, expect, it } from "vitest";
import { imagePointToViewportCss, viewportCssToPage } from "../src/coords.js";
import { WorkerError } from "../src/types.js";

describe("imagePointToViewportCss", () => {
  it("undoes the device scale factor", () => {
    const css = imagePointToViewportCss(
      { x: 200, y: 100 },
      { viewport: { deviceScaleFactor: 2, scrollX: 0, scrollY: 0, visualScale: 1 } },
    );
    expect(css).toEqual({ x: 100, y: 50 });
  });

  it("subtracts scroll for full-page images only", () => {
    const viewport = { deviceScaleFactor: 2, scrollX: 40, scrollY: 100, visualScale: 1 };
    expect(imagePointToViewportCss({ x: 200, y: 100 }, { viewport })).toEqual({ x: 100, y: 50 });
    expect(
      imagePointToViewportCss({ x: 200, y: 100 }, { viewport, space: "fullPage" }),
    ).toEqual({ x: 60, y: -50 });
  });

  it("applies visual viewport scale", () => {
    const css = imagePointToViewportCss(
      { x: 400, y: 200 },
      { viewport: { deviceScaleFactor: 2, scrollX: 0, scrollY: 0, visualScale: 2 } },
    );
    expect(css).toEqual({ x: 100, y: 50 });
  });

  it("rejects invalid geometry instead of guessing", () => {
    expect(() =>
      imagePointToViewportCss(
        { x: 1, y: 1 },
        { viewport: { deviceScaleFactor: 0, scrollX: 0, scrollY: 0, visualScale: 1 } },
      ),
    ).toThrow(WorkerError);
  });
});

describe("viewportCssToPage", () => {
  it("adds the scroll offset", () => {
    expect(viewportCssToPage({ x: 10, y: 20 }, { scrollX: 5, scrollY: 7 })).toEqual({
      x: 15,
      y: 27,
    });
  });
});
