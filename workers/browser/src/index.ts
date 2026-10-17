export * from "./types.js";
export { canonicalJson, digestOf, newId, sha256Hex } from "./digest.js";
export { imagePointToViewportCss, viewportCssToPage } from "./coords.js";
export {
  assertNavigationAllowed,
  checkFinalUrl,
  classifyUrl,
  type OriginPolicy,
  type UrlClassification,
} from "./policy.js";
export {
  ApprovalStore,
  BROWSER_APPROVAL_AUDIENCE,
  DEFAULT_APPROVAL_TTL_SECONDS,
  type ApprovalBinding,
  type ApprovalGrant,
  type ApprovalRequest,
} from "./approvals.js";
export {
  assertCapabilityOrigin,
  CAPABILITY_OP,
  CAPABILITY_PREFIX,
  mintCapability,
  verifyCapability,
  type CapabilityPayload,
  type CapabilityVerifyOptions,
} from "./capability.js";
export { captureObservation, elementDigest, type CaptureOptions } from "./observe.js";
export {
  BOX_TOLERANCE_PX,
  resolveTarget,
  type ResolvedTarget,
  type ResolveOptions,
} from "./targets.js";
export { SessionManager, WorkerSession, type SessionManagerOptions } from "./session.js";
export { clickApprovalArguments, executeAction, type ExecuteOptions } from "./actions.js";
export {
  createComputerUseServer,
  section1Auth,
  type Principal,
  type ServerOptions,
} from "./server.js";
export { Diagnostics, installEgressGuard, sanitizeHeaders, sanitizeText, sanitizeUrl } from "./diagnostics.js";
export { PreviewRegistry, startStaticPreview, type PreviewPolicy } from "./preview.js";
export { DEVICE_PROFILES, type DeviceProfile } from "./profiles.js";
export { buildEntry, redactAction, replayLog, type ReplayEntry } from "./replay.js";
export { VisualStore, type VisualResult } from "./visual.js";
