import { WorkerError, type ObservationViewport } from "./types.js";

/**
 * Image-pixel → viewport-CSS coordinate transforms.
 *
 * Screenshot pixels are device pixels; Playwright input takes CSS pixels. Two
 * independent factors must be undone:
 *  - deviceScaleFactor (Retina/DPR, includes browser page zoom in Chromium);
 *  - scroll offset, for full-page screenshots only (a viewport screenshot
 *    starts at the current scroll position, so its CSS coords already are
 *    viewport-relative).
 * `visualScale` (visualViewport.scale, pinch-zoom) scales the CSS viewport on
 * top; we require it to be 1 unless the caller opts into scaling.
 */
export interface ImagePoint {
  x: number;
  y: number;
}

export interface CssPoint {
  x: number;
  y: number;
}

export interface PointTransformOptions {
  viewport: Pick<ObservationViewport, "deviceScaleFactor" | "scrollX" | "scrollY" | "visualScale">;
  /** "viewport" (default) or "fullPage" — fullPage images start at the page origin. */
  space?: "viewport" | "fullPage";
}

export function imagePointToViewportCss(
  point: ImagePoint,
  opts: PointTransformOptions,
): CssPoint {
  const { deviceScaleFactor, scrollX, scrollY, visualScale } = opts.viewport;
  const space = opts.space ?? "viewport";
  if (!Number.isFinite(deviceScaleFactor) || deviceScaleFactor <= 0) {
    throw new WorkerError("BAD_REQUEST", `invalid deviceScaleFactor: ${deviceScaleFactor}`);
  }
  if (!(visualScale > 0) || !Number.isFinite(visualScale)) {
    throw new WorkerError("BAD_REQUEST", `invalid visualScale: ${visualScale}`);
  }

  // CSS pixels under zoom: css = imagePx / (dpr)  (dpr already folds page zoom)
  let x = point.x / deviceScaleFactor;
  let y = point.y / deviceScaleFactor;

  // Pinch-zoom: the visual viewport shows a scaled window of the layout viewport.
  x = x / visualScale;
  y = y / visualScale;

  if (space === "fullPage") {
    x -= scrollX;
    y -= scrollY;
  }
  return { x, y };
}

export function viewportCssToPage(pagePoint: CssPoint, viewport: Pick<ObservationViewport, "scrollX" | "scrollY">): CssPoint {
  return { x: pagePoint.x + viewport.scrollX, y: pagePoint.y + viewport.scrollY };
}
