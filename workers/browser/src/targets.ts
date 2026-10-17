import type { Locator, Page } from "@playwright/test";
import { imagePointToViewportCss } from "./coords.js";
import { WorkerError, type ElementRef, type Observation, type TargetSpec } from "./types.js";

/**
 * Target resolution: refs (observation-bound, staleness-validated), selectors,
 * ARIA role+name, visible text, and raw points (CSS or screenshot-image space).
 *
 * Determinism rules:
 *  - ref targets MUST name the observation they were read from; resolution
 *    compares the stored element signature against a fresh snapshot and
 *    rejects box drift beyond tolerance as STALE_OBSERVATION;
 *  - selector/role/text targets must resolve to exactly one element —
 *    multiple matches are AMBIGUOUS_TARGET with candidate descriptors.
 */

/** Box drift tolerated between observe and act, in CSS px. */
export const BOX_TOLERANCE_PX = 2;

const ALLOWED_ROLES = new Set([
  "button",
  "link",
  "textbox",
  "checkbox",
  "radio",
  "combobox",
  "heading",
  "tab",
  "menuitem",
  "option",
  "switch",
]);

export interface ResolveOptions {
  observationId?: string;
  /** Snapshot taken immediately before acting; refs are validated against it. */
  freshObservation: Observation;
  /** Stored observation the ref was read from (looked up by caller). */
  storedObservation?: Observation;
  allowedOrigins: string[];
}

export interface ResolvedTarget {
  locator?: Locator;
  point?: { x: number; y: number };
  descriptor: Record<string, unknown>;
  elementRef?: ElementRef;
  /** Origin this element would post/navigate to when outside allowedOrigins. */
  externalOrigin?: string;
}

export async function resolveTarget(
  page: Page,
  spec: TargetSpec,
  opts: ResolveOptions,
): Promise<ResolvedTarget> {
  switch (spec.by) {
    case "ref":
      return resolveRef(page, spec.ref, opts);
    case "selector":
      return resolveLocator(
        page.locator(spec.selector),
        { by: "selector", selector: spec.selector },
        opts.allowedOrigins,
      );
    case "role": {
      if (!ALLOWED_ROLES.has(spec.role)) {
        throw new WorkerError("BAD_REQUEST", `unsupported role '${spec.role}'`);
      }
      return resolveLocator(
        page.getByRole(spec.role as Parameters<Page["getByRole"]>[0], { name: spec.name }),
        { by: "role", role: spec.role, name: spec.name },
        opts.allowedOrigins,
      );
    }
    case "text":
      return resolveLocator(page.getByText(spec.text, { exact: spec.exact ?? false }), {
        by: "text",
        text: spec.text,
      }, opts.allowedOrigins);
    case "point": {
      if (spec.space === "image") {
        if (!opts.storedObservation) {
          throw new WorkerError(
            "BAD_REQUEST",
            "image-space point targets require observationId (geometry source)",
          );
        }
        const css = imagePointToViewportCss(
          { x: spec.x, y: spec.y },
          { viewport: opts.storedObservation.viewport, space: "viewport" },
        );
        return { point: css, descriptor: { ...spec, css } };
      }
      return { point: { x: spec.x, y: spec.y }, descriptor: { ...spec } };
    }
    default: {
      const exhaustive: never = spec;
      throw new WorkerError("BAD_REQUEST", `unknown target ${JSON.stringify(exhaustive)}`);
    }
  }
}

async function resolveRef(page: Page, ref: string, opts: ResolveOptions): Promise<ResolvedTarget> {
  if (!opts.storedObservation) {
    throw new WorkerError("BAD_REQUEST", `ref '${ref}' requires a known observationId`);
  }
  const stored = opts.storedObservation.refs.find((r) => r.ref === ref);
  if (!stored) {
    throw new WorkerError("BAD_REQUEST", `ref '${ref}' does not exist in that observation`);
  }
  const fresh = opts.freshObservation.refs.find(
    (r) =>
      r.frameIndex === stored.frameIndex &&
      r.tag === stored.tag &&
      r.role === stored.role &&
      r.name === stored.name &&
      (r.testId ?? "") === (stored.testId ?? "") &&
      r.signatureIndex === stored.signatureIndex,
  );
  if (!fresh) {
    throw new WorkerError(
      "STALE_OBSERVATION",
      `element for ref '${ref}' (${stored.tag} '${stored.name}') is no longer present`,
    );
  }
  const drift = Math.max(
    Math.abs(fresh.box.x - stored.box.x),
    Math.abs(fresh.box.y - stored.box.y),
    Math.abs(fresh.box.width - stored.box.width),
    Math.abs(fresh.box.height - stored.box.height),
  );
  if (drift > BOX_TOLERANCE_PX || fresh.digest !== stored.digest) {
    throw new WorkerError(
      "STALE_OBSERVATION",
      `element for ref '${ref}' changed since observation (drift ${drift.toFixed(2)}px)`,
      { storedBox: stored.box, freshBox: fresh.box },
    );
  }
  const frame = page.frames()[fresh.frameIndex];
  if (!frame) {
    throw new WorkerError("STALE_OBSERVATION", `frame for ref '${ref}' no longer exists`);
  }
  const locator = frame.locator(`[data-harness-ref="${fresh.ref.replace(/^f\d+:/, "")}"]`);
  const externalOrigin = await detectExternalOrigin(locator, opts.allowedOrigins);
  return {
    locator,
    descriptor: {
      by: "ref",
      ref,
      tag: fresh.tag,
      role: fresh.role,
      name: fresh.name,
      box: fresh.box,
    },
    elementRef: fresh,
    ...(externalOrigin ? { externalOrigin } : {}),
  };
}

async function resolveLocator(
  locator: Locator,
  descriptor: Record<string, unknown>,
  allowedOrigins: string[],
): Promise<ResolvedTarget> {
  const count = await locator.count();
  if (count === 0) {
    throw new WorkerError("TARGET_NOT_FOUND", `target not found: ${JSON.stringify(descriptor)}`);
  }
  if (count > 1) {
    const candidates = await locator.evaluateAll((els) =>
      els.slice(0, 5).map((el) => {
        const box = el.getBoundingClientRect();
        return {
          tag: el.tagName.toLowerCase(),
          text: (el.textContent ?? "").trim().slice(0, 80),
          box: { x: Math.round(box.x), y: Math.round(box.y) },
        };
      }),
    );
    throw new WorkerError(
      "AMBIGUOUS_TARGET",
      `target matched ${count} elements: ${JSON.stringify(descriptor)}`,
      { count, candidates },
    );
  }
  const externalOrigin = await detectExternalOrigin(locator.first(), allowedOrigins);
  return { locator: locator.first(), descriptor, ...(externalOrigin ? { externalOrigin } : {}) };
}

async function detectExternalOrigin(
  locator: Locator,
  allowedOrigins: string[],
): Promise<string | undefined> {
  try {
    const origin = await locator.evaluate((el, allowed) => {
      const originOf = (raw: string): string | null => {
        try {
          return new URL(raw, window.location.href).origin;
        } catch {
          return null;
        }
      };
      const viaLink = el.closest("a[href]");
      if (viaLink) {
        const href = viaLink.getAttribute("href");
        if (href && !href.startsWith("#")) {
          const origin = originOf(href);
          if (origin && !allowed.includes(origin)) return origin;
        }
      }
      const form = el.closest("form");
      if (form) {
        const action = form.getAttribute("action");
        const origin = originOf(action && action.trim() !== "" ? action : "unset:unset");
        if (origin && !allowed.includes(origin)) return origin;
      }
      return null;
    }, allowedOrigins);
    return origin ?? undefined;
  } catch {
    return undefined;
  }
}
