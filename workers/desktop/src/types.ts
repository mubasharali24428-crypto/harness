/** Display geometry in physical pixels (image space) and logical points (input space). */
export interface Display {
  id: string;
  /** logical points */
  width: number;
  height: number;
  /** physical px per logical point (2 on Retina) */
  scale: number;
}

export interface Region { x: number; y: number; width: number; height: number } // logical points

export type Capability = "capture" | "input";

/** What a human granted. Without a grant nothing runs; there is no implicit full-host control. */
export interface DesktopGrant {
  grantId: string;
  teamId: string;
  runId: string;
  capabilities: Capability[];
  /** All capture and input must stay inside this region (logical points). */
  region: Region;
  expiresAtMs: number;
}

export type DesktopOp =
  | { kind: "click"; x: number; y: number; space: "image" | "point"; button?: "left" | "right"; double?: boolean }
  | { kind: "move"; x: number; y: number; space: "image" | "point" }
  | { kind: "type"; text: string }
  | { kind: "key"; combo: string };

export interface ScreenObservation {
  observationId: string;
  display: Display;
  region: Region;
  /** pixel size of the captured image */
  imageSize: { width: number; height: number };
  sha256: string;
  capturedAtMs: number;
  imagePath: string;
}

/** The OS seam. Production = macos adapter; tests inject a recording stand-in. */
export interface DesktopAdapter {
  readonly name: string;
  probe(): Promise<{ ok: boolean; reasons: string[]; display?: Display }>;
  capture(region: Region, outPath: string): Promise<void>;
  perform(op: { kind: DesktopOp["kind"]; [k: string]: unknown }): Promise<void>;
}

export type DesktopErrorCode =
  | "UNSUPPORTED" | "NO_GRANT" | "GRANT_EXPIRED" | "GRANT_SCOPE" | "OUT_OF_REGION"
  | "STALE_OBSERVATION" | "NEEDS_OBSERVATION" | "HUMAN_CONTROL" | "BAD_REQUEST";

export class DesktopError extends Error {
  constructor(readonly code: DesktopErrorCode, message: string) {
    super(message);
  }
}
