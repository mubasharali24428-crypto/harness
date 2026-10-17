/**
 * A3 browser worker — typed observation/action surface.
 *
 * Lane A3 owns this namespace (workers/browser/**). Wire-adjacent shapes follow
 * the predeclared HAP/1 transport profile: camelCase, statuses
 * succeeded|denied|failed|needsApproval|unsupported, artifacts described by
 * {artifactId, mediaType, sha256, sizeBytes, redactionStatus}.
 */

export type WorkerErrorCode =
  | "BAD_REQUEST"
  | "SESSION_NOT_FOUND"
  | "TARGET_NOT_FOUND"
  | "AMBIGUOUS_TARGET"
  | "STALE_OBSERVATION"
  | "POSTCONDITION_FAILED"
  | "EGRESS_DENIED"
  | "SSRF_DENIED"
  | "APPROVAL_REQUIRED"
  | "APPROVAL_INVALID"
  | "APPROVAL_EXPIRED"
  | "APPROVAL_REPLAYED"
  | "CAPABILITY_INVALID"
  | "CAPABILITY_EXPIRED"
  | "CAPABILITY_SCOPE"
  | "UNSUPPORTED"
  | "EXECUTION_FAILED";

export class WorkerError extends Error {
  readonly code: WorkerErrorCode;
  readonly details?: unknown;

  constructor(code: WorkerErrorCode, message: string, details?: unknown) {
    super(message);
    this.name = "WorkerError";
    this.code = code;
    this.details = details;
  }
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type TargetSpec =
  | { by: "ref"; ref: string }
  | { by: "selector"; selector: string }
  | { by: "role"; role: string; name: string }
  | { by: "text"; text: string; exact?: boolean }
  | { by: "point"; x: number; y: number; space: "css" | "image" };

export interface ObservationViewport {
  width: number;
  height: number;
  deviceScaleFactor: number;
  scrollX: number;
  scrollY: number;
  visualScale: number;
}

export interface ElementRef {
  ref: string;
  tag: string;
  role: string;
  name: string;
  testId?: string;
  /** index among elements sharing the same signature — disambiguates duplicates */
  signatureIndex: number;
  box: Box;
  /** sha256 over tag|role|name|testId|box — change ⇒ element geometry/text moved */
  digest: string;
  frameIndex: number;
}

export interface FrameInfo {
  index: number;
  url: string;
  crossOrigin: boolean;
}

export interface Observation {
  observationId: string;
  sessionId: string;
  url: string;
  title: string;
  viewport: ObservationViewport;
  domDigest: string;
  capturedAtMs: number;
  refs: ElementRef[];
  frames: FrameInfo[];
  screenshotArtifact?: Artifact;
}

export interface Artifact {
  artifactId: string;
  mediaType: string;
  sha256: string;
  sizeBytes: number;
  redactionStatus: "none" | "sanitized";
  path?: string;
}

export interface ExpectClause {
  urlContains?: string;
  selectorVisible?: string;
  selectorHidden?: string;
  valueEquals?: { target: TargetSpec; equals: string };
}

export type BrowserAction =
  | {
      kind: "navigate";
      url: string;
      capabilityToken: string;
      expect?: ExpectClause;
      screenshot?: boolean;
    }
  | {
      kind: "click";
      target: TargetSpec;
      observationId?: string;
      capabilityToken: string;
      /**
       * "external_write" marks an action that can send data outside the
       * allowed origins (form submit, OAuth popup): it requires a consumed
       * human approval in addition to the capability token.
       */
      effect?: "read" | "external_write";
      approvalId?: string;
      expect?: ExpectClause;
      screenshot?: boolean;
    }
  | {
      kind: "type";
      target: TargetSpec;
      text: string;
      observationId?: string;
      clearFirst?: boolean;
      capabilityToken: string;
      expect?: ExpectClause;
    }
  | {
      kind: "press";
      key: string;
      target?: TargetSpec;
      observationId?: string;
      capabilityToken: string;
      expect?: ExpectClause;
    }
  | {
      kind: "scroll";
      dy?: number;
      dx?: number;
      toTarget?: TargetSpec;
      observationId?: string;
      capabilityToken: string;
      expect?: ExpectClause;
    }
  | {
      kind: "hover";
      target: TargetSpec;
      observationId?: string;
      capabilityToken: string;
    }
  | {
      kind: "selectOption";
      target: TargetSpec;
      value: string;
      observationId?: string;
      capabilityToken: string;
      expect?: ExpectClause;
    }
  | {
      kind: "waitFor";
      target?: TargetSpec;
      urlContains?: string;
      timeoutMs?: number;
      capabilityToken: string;
    }
  | {
      kind: "screenshot";
      fullPage?: boolean;
      capabilityToken: string;
    }
  | {
      kind: "observe";
      screenshot?: boolean;
      capabilityToken: string;
    }
  | {
      kind: "tap";
      target: TargetSpec;
      observationId?: string;
      capabilityToken: string;
      expect?: ExpectClause;
    }
  | { kind: "diagnostics"; sinceSeq?: number; capabilityToken: string }
  | { kind: "pwaCheck"; capabilityToken: string }
  | {
      kind: "visualCheck";
      name: string;
      /** CSS selectors blanked before comparison (clocks, ads, avatars). */
      mask?: string[];
      maxDiffRatio?: number;
      /** Baseline write requires a named human approver. */
      approveAs?: string;
      capabilityToken: string;
    }
  | {
      kind: "e2e";
      steps: DistributiveOmit<Exclude<BrowserAction, { kind: "e2e" }>, "capabilityToken">[];
      continueOnFailure?: boolean;
      capabilityToken: string;
    };

type DistributiveOmit<T, K extends keyof never> = T extends unknown ? Omit<T, K> : never;

export type ActionStatus = "succeeded" | "denied" | "failed" | "needsApproval" | "unsupported";

export interface ActionOutcome {
  schemaVersion: "hap/1";
  requestId: string;
  status: ActionStatus;
  result?: Record<string, unknown>;
  artifacts: Artifact[];
  observation?: Observation;
  error?: { code: WorkerErrorCode; message: string };
}

export interface SessionIdentity {
  teamId: string;
  projectId: string;
  runId: string;
}

export interface SessionOptions {
  allowedOrigins: string[];
  viewport?: { width: number; height: number };
  deviceScaleFactor?: number;
  /** Named device profile (see profiles.ts): viewport, DPR, touch, UA. */
  profile?: string;
}
