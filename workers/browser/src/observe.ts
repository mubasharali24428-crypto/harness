import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { digestOf, sha256Hex, newId } from "./digest.js";
import type {
  Artifact,
  ElementRef,
  FrameInfo,
  Observation,
  ObservationViewport,
} from "./types.js";

/**
 * Observation capture: what the agent may reason about, and the ground truth
 * against which later actions are validated.
 *
 * Every visible interactive element gets a stable-for-this-observation ref
 * (`data-harness-ref` is stamped into the DOM so later locators are exact) plus
 * a per-element digest over tag/role/name/box. The observation-level digest
 * covers all of that plus url/title/size — any mutation between observe and
 * act is detectable.
 */

interface CollectorResult {
  url: string;
  title: string;
  refs: ElementRef[];
}

function collectorSource(): string {
  return `(() => {
    const SELECTOR = 'a[href], button, input, select, textarea, canvas, [role], [data-testid]';
    const implicitRole = (el) => {
      const explicit = el.getAttribute('role');
      if (explicit) return explicit;
      const tag = el.tagName.toLowerCase();
      if (tag === 'a') return 'link';
      if (tag === 'button') return 'button';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        return 'textbox';
      }
      return 'generic';
    };
    const nameOf = (el) => {
      const aria = el.getAttribute('aria-label');
      if (aria) return aria.trim();
      const tag = el.tagName.toLowerCase();
      if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'submit' || t === 'button' || t === 'reset') {
          const v = el.value || el.getAttribute('value');
          if (v && v.trim()) return v.trim();
        }
        const ph = el.getAttribute('placeholder');
        if (ph && ph.trim()) return ph.trim();
      }
      const text = (el.textContent || '').trim();
      return text.length > 0 ? text.slice(0, 200) : '';
    };
    const visible = (el, box) => {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      return box.width > 0 && box.height > 0;
    };
    const refs = [];
    const counts = new Map();
    let idx = 0;
    for (const el of document.querySelectorAll(SELECTOR)) {
      const box = el.getBoundingClientRect();
      if (!visible(el, box)) continue;
      const tag = el.tagName.toLowerCase();
      const role = implicitRole(el);
      const name = nameOf(el);
      const testId = el.getAttribute('data-testid') || undefined;
      const sig = tag + '|' + role + '|' + name + '|' + (testId || '');
      const sigIdx = counts.get(sig) || 0;
      counts.set(sig, sigIdx + 1);
      idx += 1;
      const ref = 'e' + idx;
      el.setAttribute('data-harness-ref', ref);
      refs.push({
        ref, tag, role, name, testId, signatureIndex: sigIdx,
        box: { x: Math.round(box.x*100)/100, y: Math.round(box.y*100)/100,
               width: Math.round(box.width*100)/100, height: Math.round(box.height*100)/100 },
        digest: '', frameIndex: 0,
      });
    }
    return { refs: refs.map((r) => ({...r})) };
  })()`;
}

export function elementDigest(ref: Omit<ElementRef, "digest" | "ref" | "frameIndex">): string {
  return digestOf([ref.tag, ref.role, ref.name, ref.testId ?? null, ref.box]);
}

export interface CaptureOptions {
  screenshot?: boolean;
  fullPage?: boolean;
  artifactsDir?: string;
  now?: () => number;
}

export async function captureObservation(
  page: Page,
  sessionId: string,
  opts: CaptureOptions = {},
): Promise<Observation> {
  const now = opts.now ?? (() => Date.now());
  const refs: ElementRef[] = [];
  const frames: FrameInfo[] = [];

  const pageFrames = page.frames();
  for (let frameIndex = 0; frameIndex < pageFrames.length; frameIndex += 1) {
    const frame = pageFrames[frameIndex]!;
    let frameUrl = "";
    try {
      frameUrl = frame.url();
    } catch {
      frameUrl = "";
    }
    let pageOrigin = "";
    try {
      pageOrigin = new URL(page.url()).origin;
    } catch {
      pageOrigin = "";
    }
    let frameOrigin = "";
    try {
      frameOrigin = new URL(frameUrl).origin;
    } catch {
      frameOrigin = "";
    }
    frames.push({
      index: frameIndex,
      url: frameUrl,
      crossOrigin: frameOrigin !== "" && pageOrigin !== "" && frameOrigin !== pageOrigin,
    });

    let result: CollectorResult | null = null;
    try {
      result = (await frame.evaluate(collectorSource())) as CollectorResult;
    } catch {
      // Cross-origin frames still get enumerated; their internals are not
      // observable from this context (honest limit, recorded in the report).
      continue;
    }
    for (const raw of result.refs) {
      const partial = {
        tag: raw.tag,
        role: raw.role,
        name: raw.name,
        testId: raw.testId,
        signatureIndex: raw.signatureIndex,
        box: raw.box,
      };
      refs.push({
        ...partial,
        ref: `f${frameIndex}:${raw.ref}`,
        digest: elementDigest(partial),
        frameIndex,
      });
    }
  }

  const viewport = await readViewport(page);
  const url = page.url();
  const title = await page.title().catch(() => "");
  const domDigest = digestOf({
    url,
    title,
    refs: refs.map((r) => r.digest),
    viewport: { width: viewport.width, height: viewport.height },
  });

  const observationId = newId("obs");
  let screenshotArtifact: Artifact | undefined;
  if (opts.screenshot) {
    const buffer = await page.screenshot({ fullPage: opts.fullPage ?? false });
    const sha = sha256Hex(buffer);
    const artifact: Artifact = {
      artifactId: newId("art"),
      mediaType: "image/png",
      sha256: sha,
      sizeBytes: buffer.length,
      redactionStatus: "none",
    };
    if (opts.artifactsDir) {
      mkdirSync(opts.artifactsDir, { recursive: true });
      const path = join(opts.artifactsDir, `${observationId}.png`);
      writeFileSync(path, buffer);
      artifact.path = path;
    }
    screenshotArtifact = artifact;
  }

  return {
    observationId,
    sessionId,
    url,
    title,
    viewport,
    domDigest,
    capturedAtMs: now(),
    refs,
    frames,
    ...(screenshotArtifact ? { screenshotArtifact } : {}),
  };
}

async function readViewport(page: Page): Promise<ObservationViewport> {
  const size = page.viewportSize() ?? { width: 0, height: 0 };
  const metrics = (await page
    .evaluate(() => ({
      dpr: window.devicePixelRatio,
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      visualScale:
        typeof window.visualViewport !== "undefined" && window.visualViewport
          ? window.visualViewport.scale
          : 1,
    }))
    .catch(() => ({ dpr: 1, scrollX: 0, scrollY: 0, visualScale: 1 }))) as {
    dpr: number;
    scrollX: number;
    scrollY: number;
    visualScale: number;
  };
  return {
    width: size.width,
    height: size.height,
    deviceScaleFactor: metrics.dpr || 1,
    scrollX: metrics.scrollX,
    scrollY: metrics.scrollY,
    visualScale: metrics.visualScale || 1,
  };
}
