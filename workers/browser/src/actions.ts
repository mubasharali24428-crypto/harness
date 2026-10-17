import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { digestOf, newId, sha256Hex } from "./digest.js";
import { assertCapabilityOrigin, verifyCapability, type CapabilityPayload } from "./capability.js";
import { assertNavigationAllowed, checkFinalUrl, classifyUrl } from "./policy.js";
import { captureObservation } from "./observe.js";
import { resolveTarget } from "./targets.js";
import { buildEntry } from "./replay.js";
import { VisualStore } from "./visual.js";
import type { WorkerSession } from "./session.js";
import {
  WorkerError,
  type ActionOutcome,
  type BrowserAction,
  type ExpectClause,
  type Observation,
  type TargetSpec,
  type WorkerErrorCode,
} from "./types.js";

/**
 * Action execution. Every call needs a valid S5 capability token (fail closed);
 * external-write clicks additionally need a one-time approval grant. Outcomes
 * use the HAP/1 response shape and always carry a fresh observation so callers
 * never act on pre-action state.
 */

export interface ExecuteOptions {
  capabilitySecret: string;
  now?: () => number;
  /** Hard cap for waits, ms. */
  maxWaitMs?: number;
  /** Root for visual baselines; visualCheck is unsupported without it. */
  baselineDir?: string;
}

const DEFAULT_NAV_TIMEOUT_MS = 15_000;
const DEFAULT_WAIT_TIMEOUT_MS = 5_000;
/** Postconditions poll briefly: navigation/rendering after an action is asynchronous. */
const EXPECT_POLL_MS = 3_000;

export function clickApprovalArguments(target: TargetSpec, url: string): unknown {
  return { kind: "click", target, url };
}

export async function executeAction(
  session: WorkerSession,
  action: BrowserAction,
  opts: ExecuteOptions,
): Promise<ActionOutcome> {
  const now = opts.now ?? (() => Date.now());
  const requestId = newId("req");

  let capability: CapabilityPayload;
  try {
    capability = verifyCapability(action.capabilityToken, {
      secret: opts.capabilitySecret,
      now: Math.floor(now() / 1000),
      binding: session.identity,
    });
  } catch (error) {
    return deniedOutcome(requestId, error);
  }

  const startedAt = now();
  let outcome: ActionOutcome;
  try {
    const result = await dispatch(session, action, capability, opts, now);
    outcome = {
      schemaVersion: "hap/1",
      requestId,
      status: "succeeded",
      result: result.result,
      artifacts: result.artifacts,
      ...(result.observation ? { observation: result.observation } : {}),
    };
  } catch (error) {
    outcome = deniedOutcome(requestId, error);
  }
  for (const a of [...outcome.artifacts, outcome.observation?.screenshotArtifact]) {
    if (a?.path) session.artifactIndex.set(a.artifactId, a);
  }
  if (action.kind !== "e2e" && action.kind !== "diagnostics") {
    session.recordReplay(
      buildEntry(session.replayLog.length, action, outcome, startedAt, now() - startedAt),
    );
  }
  return outcome;
}

interface DispatchResult {
  result: Record<string, unknown>;
  artifacts: NonNullable<ActionOutcome["artifacts"]>;
  observation?: Observation;
}

async function dispatch(
  session: WorkerSession,
  action: BrowserAction,
  capability: CapabilityPayload,
  opts: ExecuteOptions,
  now: () => number,
): Promise<DispatchResult> {
  const page = session.page;
  const maxWait = opts.maxWaitMs ?? 30_000;

  switch (action.kind) {
    case "observe": {
      const observation = await captureObservation(page, session.sessionId, {
        screenshot: action.screenshot ?? false,
        artifactsDir: session.artifactsDir,
        now,
      });
      session.rememberObservation(observation);
      return {
        result: { observationId: observation.observationId, refCount: observation.refs.length },
        artifacts: observation.screenshotArtifact ? [observation.screenshotArtifact] : [],
        observation,
      };
    }

    case "navigate": {
      const cls = assertNavigationAllowed(action.url, { allowedOrigins: session.allowedOrigins });
      assertCapabilityOrigin(capability, cls.origin);
      await page.goto(action.url, {
        waitUntil: "domcontentloaded",
        timeout: DEFAULT_NAV_TIMEOUT_MS,
      });
      await settleNavigation(page);
      try {
        checkFinalUrl(page.url(), { allowedOrigins: session.allowedOrigins });
      } catch (error) {
        await page.goto("about:blank").catch(() => undefined);
        throw error;
      }
      if (action.expect) await evaluateExpect(session, action.expect, undefined);
      return await finish(session, action.screenshot ?? false, now, { url: page.url() });
    }

    case "click": {
      if (action.effect === "external_write") {
        await requireExternalWriteApproval(session, action);
      }
      const fresh = await captureObservation(page, session.sessionId, { now });
      const stored = session.storedObservation(action.observationId);
      const resolved = await resolveTarget(page, action.target, {
        observationId: action.observationId,
        freshObservation: fresh,
        storedObservation: stored,
        allowedOrigins: session.allowedOrigins,
      });
      if (action.effect !== "read" && resolved.externalOrigin) {
        await requireExternalWriteApproval(session, action);
      }
      if (resolved.locator) {
        await resolved.locator.click({ timeout: DEFAULT_NAV_TIMEOUT_MS });
      } else if (resolved.point) {
        await page.mouse.click(resolved.point.x, resolved.point.y);
      } else {
        throw new WorkerError("UNSUPPORTED", "resolved target has neither locator nor point");
      }
      await settleNavigation(page);
      try {
        checkFinalUrl(page.url(), { allowedOrigins: session.allowedOrigins });
      } catch (error) {
        await page.goto("about:blank").catch(() => undefined);
        throw error;
      }
      if (action.expect) await evaluateExpect(session, action.expect, action.observationId);
      return await finish(session, action.screenshot ?? false, now, {
        target: resolved.descriptor,
        url: page.url(),
      });
    }

    case "type": {
      const fresh = await captureObservation(page, session.sessionId, { now });
      const resolved = await resolveTarget(page, action.target, {
        observationId: action.observationId,
        freshObservation: fresh,
        storedObservation: session.storedObservation(action.observationId),
        allowedOrigins: session.allowedOrigins,
      });
      if (!resolved.locator) throw new WorkerError("BAD_REQUEST", "type needs an element target");
      if (action.clearFirst ?? true) await resolved.locator.fill("");
      await resolved.locator.pressSequentially(action.text);
      // Secret hygiene: the typed value is never echoed back — digest only.
      if (action.expect) await evaluateExpect(session, action.expect, action.observationId);
      return await finish(session, false, now, {
        valueDigest: sha256Hex(action.text),
        length: action.text.length,
      });
    }

    case "press": {
      if (!/^[A-Za-z0-9_+.\-]+$/.test(action.key)) {
        throw new WorkerError("BAD_REQUEST", `unsupported key '${action.key}'`);
      }
      if (action.target) {
        const fresh = await captureObservation(page, session.sessionId, { now });
        const resolved = await resolveTarget(page, action.target, {
          observationId: action.observationId,
          freshObservation: fresh,
          storedObservation: session.storedObservation(action.observationId),
          allowedOrigins: session.allowedOrigins,
        });
        if (resolved.externalOrigin) {
          throw new WorkerError(
            "APPROVAL_REQUIRED",
            `'${action.key}' in a form posting to '${resolved.externalOrigin}' needs explicit handling`,
          );
        }
        if (resolved.locator) await resolved.locator.focus();
      }
      await page.keyboard.press(action.key);
      await settleNavigation(page);
      try {
        checkFinalUrl(page.url(), { allowedOrigins: session.allowedOrigins });
      } catch (error) {
        await page.goto("about:blank").catch(() => undefined);
        throw error;
      }
      if (action.expect) await evaluateExpect(session, action.expect, action.observationId);
      return await finish(session, false, now, { key: action.key });
    }

    case "scroll": {
      if (action.toTarget) {
        const fresh = await captureObservation(page, session.sessionId, { now });
        const resolved = await resolveTarget(page, action.toTarget, {
          observationId: action.observationId,
          freshObservation: fresh,
          storedObservation: session.storedObservation(action.observationId),
          allowedOrigins: session.allowedOrigins,
        });
        if (!resolved.locator) throw new WorkerError("BAD_REQUEST", "scroll needs an element");
        await resolved.locator.scrollIntoViewIfNeeded();
      } else {
        const dy = action.dy ?? 0;
        const dx = action.dx ?? 0;
        if (Math.abs(dy) > 20_000 || Math.abs(dx) > 20_000) {
          throw new WorkerError("BAD_REQUEST", "scroll delta beyond sane bounds");
        }
        await page.evaluate(([x, y]) => window.scrollBy(x as number, y as number), [dx, dy]);
      }
      if (action.expect) await evaluateExpect(session, action.expect, action.observationId);
      return await finish(session, false, now, {
        scroll: await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY })),
      });
    }

    case "hover": {
      const fresh = await captureObservation(page, session.sessionId, { now });
      const resolved = await resolveTarget(page, action.target, {
        observationId: action.observationId,
        freshObservation: fresh,
        storedObservation: session.storedObservation(action.observationId),
        allowedOrigins: session.allowedOrigins,
      });
      if (resolved.locator) await resolved.locator.hover();
      else if (resolved.point) await page.mouse.move(resolved.point.x, resolved.point.y);
      return await finish(session, false, now, { target: resolved.descriptor });
    }

    case "selectOption": {
      const fresh = await captureObservation(page, session.sessionId, { now });
      const resolved = await resolveTarget(page, action.target, {
        observationId: action.observationId,
        freshObservation: fresh,
        storedObservation: session.storedObservation(action.observationId),
        allowedOrigins: session.allowedOrigins,
      });
      if (!resolved.locator) throw new WorkerError("BAD_REQUEST", "selectOption needs an element");
      await resolved.locator.selectOption(action.value);
      if (action.expect) await evaluateExpect(session, action.expect, action.observationId);
      return await finish(session, false, now, { value: action.value });
    }

    case "waitFor": {
      const timeout = Math.min(action.timeoutMs ?? DEFAULT_WAIT_TIMEOUT_MS, maxWait);
      if (action.urlContains) {
        await page
          .waitForURL((url) => url.href.includes(action.urlContains as string), { timeout })
          .catch(() => {
            throw new WorkerError(
              "POSTCONDITION_FAILED",
              `url never contained '${action.urlContains}' within ${timeout}ms (at ${session.page.url()})`,
            );
          });
      }
      if (action.target) {
        const deadline = Date.now() + timeout;
        for (;;) {
          try {
            const fresh = await captureObservation(page, session.sessionId, { now });
            const resolved = await resolveTarget(page, action.target, {
              observationId: undefined,
              freshObservation: fresh,
              allowedOrigins: session.allowedOrigins,
            });
            if (resolved.locator) {
              await resolved.locator.waitFor({
                state: "visible",
                timeout: Math.max(50, deadline - Date.now()),
              });
            }
            break;
          } catch (error) {
            const retryable = error instanceof WorkerError && error.code === "TARGET_NOT_FOUND";
            if (Date.now() >= deadline || (!retryable && error instanceof WorkerError)) {
              throw new WorkerError(
                error instanceof WorkerError && !retryable ? error.code : "POSTCONDITION_FAILED",
                `target never became visible within ${timeout}ms`,
              );
            }
            await page.waitForTimeout(100);
          }
        }
      }
      return await finish(session, false, now, { url: page.url() });
    }

    case "tap": {
      const fresh = await captureObservation(page, session.sessionId, { now });
      const resolved = await resolveTarget(page, action.target, {
        observationId: action.observationId,
        freshObservation: fresh,
        storedObservation: session.storedObservation(action.observationId),
        allowedOrigins: session.allowedOrigins,
      });
      if (!resolved.locator) throw new WorkerError("BAD_REQUEST", "tap needs an element target");
      if (resolved.externalOrigin) {
        throw new WorkerError("APPROVAL_REQUIRED", `tap leads to '${resolved.externalOrigin}'; use click with approval`);
      }
      const touch = await page.evaluate(() => navigator.maxTouchPoints > 0);
      if (!touch) throw new WorkerError("UNSUPPORTED", "session profile has no touch support; open with a mobile profile");
      await resolved.locator.tap({ timeout: DEFAULT_NAV_TIMEOUT_MS });
      await settleNavigation(page);
      checkFinalUrl(page.url(), { allowedOrigins: session.allowedOrigins });
      if (action.expect) await evaluateExpect(session, action.expect, action.observationId);
      return await finish(session, false, now, { target: resolved.descriptor, url: page.url() });
    }

    case "diagnostics": {
      const snap = session.diagnostics.snapshot({ sinceSeq: action.sinceSeq });
      return { result: { ...snap }, artifacts: [] };
    }

    case "pwaCheck": {
      const info = await page.evaluate(async () => {
        const link = document.querySelector('link[rel="manifest"]') as HTMLLinkElement | null;
        let manifest: Record<string, unknown> | null = null;
        if (link) {
          try {
            const m = await (await fetch(link.href)).json();
            manifest = { name: m.name, shortName: m.short_name, display: m.display, startUrl: m.start_url, iconCount: (m.icons ?? []).length };
          } catch { manifest = null; }
        }
        const regs = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistrations() : [];
        return {
          manifestLinked: !!link, manifest, serviceWorkers: regs.length,
          standalone: window.matchMedia("(display-mode: standalone)").matches,
          viewportMeta: !!document.querySelector('meta[name="viewport"]'),
        };
      });
      return { result: { ...info }, artifacts: [] };
    }

    case "visualCheck": {
      if (!opts.baselineDir) throw new WorkerError("UNSUPPORTED", "visual baselines are not configured");
      const store = new VisualStore(join(opts.baselineDir, session.identity.teamId, session.identity.projectId));
      for (const sel of action.mask ?? []) {
        await page.evaluate((s) => document.querySelectorAll(s).forEach((e) => ((e as HTMLElement).style.visibility = "hidden")), sel);
      }
      const png = await page.screenshot({ animations: "disabled", caret: "hide", scale: "css" });
      if (action.approveAs) {
        const { sha256 } = store.approveBaseline(action.name, png, action.approveAs);
        return { result: { status: "baselineApproved", sha256, approvedBy: action.approveAs }, artifacts: [] };
      }
      const r = store.compare(action.name, png, { maxDiffRatio: action.maxDiffRatio });
      const artifacts: DispatchResult["artifacts"] = [];
      if (r.diffPng) {
        mkdirSync(session.artifactsDir, { recursive: true, mode: 0o700 });
        const path = join(session.artifactsDir, `${newId("diff")}.png`);
        writeFileSync(path, r.diffPng);
        const art = { artifactId: newId("art"), mediaType: "image/png", sha256: sha256Hex(r.diffPng), sizeBytes: r.diffPng.length, redactionStatus: "none" as const, path };
        session.artifactIndex.set(art.artifactId, art);
        artifacts.push(art);
      }
      if (r.status !== "match") {
        throw new WorkerError("POSTCONDITION_FAILED", `visual check '${action.name}': ${r.status} (diffRatio ${r.diffRatio.toFixed(5)})`, {
          status: r.status, diffRatio: r.diffRatio,
          diffArtifactIds: artifacts.map((a) => a.artifactId),
        });
      }
      return { result: { status: r.status, diffRatio: r.diffRatio }, artifacts };
    }

    case "e2e": {
      const steps: Record<string, unknown>[] = [];
      let failed = 0;
      for (const [i, step] of action.steps.entries()) {
        if ((step as { kind: string }).kind === "e2e") throw new WorkerError("BAD_REQUEST", "e2e scenarios cannot nest");
        const t0 = now();
        const out = await executeAction(session, { ...step, capabilityToken: action.capabilityToken } as BrowserAction, opts);
        const ok = out.status === "succeeded";
        steps.push({ index: i, kind: (step as { kind: string }).kind, status: out.status, ms: now() - t0, ...(out.error ? { error: out.error } : {}) });
        if (!ok) {
          failed += 1;
          if (!action.continueOnFailure) break;
        }
      }
      if (failed > 0) {
        throw new WorkerError("POSTCONDITION_FAILED", `e2e scenario failed ${failed}/${action.steps.length} step(s)`, { steps });
      }
      return { result: { passed: steps.length, steps }, artifacts: [] };
    }

    case "screenshot": {
      const observation = await captureObservation(page, session.sessionId, {
        screenshot: true,
        fullPage: action.fullPage ?? false,
        artifactsDir: session.artifactsDir,
        now,
      });
      session.rememberObservation(observation);
      return {
        result: { observationId: observation.observationId },
        artifacts: observation.screenshotArtifact ? [observation.screenshotArtifact] : [],
        observation,
      };
    }

    default: {
      const exhaustive: never = action;
      throw new WorkerError("BAD_REQUEST", `unknown action ${JSON.stringify(exhaustive)}`);
    }
  }
}

async function settleNavigation(page: WorkerSession["page"]): Promise<void> {
  await page.waitForLoadState("domcontentloaded", { timeout: 5_000 }).catch(() => undefined);
}

async function finish(
  session: WorkerSession,
  screenshot: boolean,
  now: () => number,
  result: Record<string, unknown>,
): Promise<DispatchResult> {
  const observation = await captureObservation(session.page, session.sessionId, {
    screenshot,
    artifactsDir: session.artifactsDir,
    now,
  });
  // Keep the last N observations per session to bound memory.
  if (session.observations.size >= 50) {
    const firstKey = session.observations.keys().next().value;
    if (firstKey !== undefined) session.observations.delete(firstKey);
  }
  session.rememberObservation(observation);
  return {
    result: { ...result, observationId: observation.observationId },
    artifacts: observation.screenshotArtifact ? [observation.screenshotArtifact] : [],
    observation,
  };
}

async function requireExternalWriteApproval(
  session: WorkerSession,
  action: Extract<BrowserAction, { kind: "click" }>,
): Promise<void> {
  const args = clickApprovalArguments(action.target, session.page.url());
  if (!action.approvalId) {
    throw new WorkerError("APPROVAL_REQUIRED", "external write requires a human approval grant", {
      operation: "click",
      argumentsDigest: digestOf(args),
    });
  }
  session.approvals.consume(action.approvalId, {
    identity: session.identity,
    operation: "click",
    arguments: args,
  });
}

async function evaluateExpect(
  session: WorkerSession,
  clause: ExpectClause,
  observationId: string | undefined,
): Promise<void> {
  const page = session.page;
  if (clause.urlContains && !page.url().includes(clause.urlContains)) {
    await page
      .waitForURL((u) => u.href.includes(clause.urlContains as string), { timeout: EXPECT_POLL_MS })
      .catch(() => undefined);
  }
  if (clause.urlContains && !page.url().includes(clause.urlContains)) {
    throw new WorkerError(
      "POSTCONDITION_FAILED",
      `url '${page.url()}' does not contain '${clause.urlContains}'`,
    );
  }
  if (clause.selectorVisible !== undefined) {
    const visible = await page
      .locator(clause.selectorVisible)
      .first()
      .waitFor({ state: "visible", timeout: EXPECT_POLL_MS })
      .then(() => true)
      .catch(() => false);
    if (!visible) {
      throw new WorkerError(
        "POSTCONDITION_FAILED",
        `selector '${clause.selectorVisible}' is not visible after action`,
      );
    }
  }
  if (clause.selectorHidden !== undefined) {
    const visible = await page
      .locator(clause.selectorHidden)
      .first()
      .waitFor({ state: "hidden", timeout: EXPECT_POLL_MS })
      .then(() => false)
      .catch(() => true);
    if (visible) {
      throw new WorkerError(
        "POSTCONDITION_FAILED",
        `selector '${clause.selectorHidden}' is still visible after action`,
      );
    }
  }
  if (clause.valueEquals) {
    const fresh = await captureObservation(page, session.sessionId);
    const resolved = await resolveTarget(page, clause.valueEquals.target, {
      observationId,
      freshObservation: fresh,
      storedObservation: session.storedObservation(observationId),
      allowedOrigins: session.allowedOrigins,
    });
    if (!resolved.locator) {
      throw new WorkerError("BAD_REQUEST", "valueEquals needs an element target");
    }
    const value = await resolved.locator.inputValue().catch(() => null);
    if (value !== clause.valueEquals.equals) {
      throw new WorkerError(
        "POSTCONDITION_FAILED",
        `value at target is not '${clause.valueEquals.equals}'`,
        { observed: value === null ? "not-an-input" : "[redacted]" },
      );
    }
  }
}

function deniedOutcome(requestId: string, error: unknown): ActionOutcome {
  if (error instanceof WorkerError) {
    return {
      schemaVersion: "hap/1",
      requestId,
      status: statusForCode(error.code),
      artifacts: [],
      error: { code: error.code, message: error.message },
      ...(error.details !== undefined ? { result: { details: error.details } } : {}),
    };
  }
  return {
    schemaVersion: "hap/1",
    requestId,
    status: "failed",
    artifacts: [],
    error: {
      code: "EXECUTION_FAILED",
      message: error instanceof Error ? error.message : String(error),
    },
  };
}

function statusForCode(code: WorkerErrorCode): ActionOutcome["status"] {
  switch (code) {
    case "APPROVAL_REQUIRED":
      return "needsApproval";
    case "EGRESS_DENIED":
    case "SSRF_DENIED":
    case "APPROVAL_INVALID":
    case "APPROVAL_EXPIRED":
    case "APPROVAL_REPLAYED":
    case "CAPABILITY_INVALID":
    case "CAPABILITY_EXPIRED":
    case "CAPABILITY_SCOPE":
      return "denied";
    case "UNSUPPORTED":
      return "unsupported";
    default:
      return "failed";
  }
}
