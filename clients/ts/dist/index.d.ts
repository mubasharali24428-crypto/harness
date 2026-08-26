/**
 * Single entry point for scripting the Harness from TypeScript/JavaScript.
 *
 * One object, seven section clients, one event stream:
 *
 *     import { Harness } from "@harness/ts-sdk";
 *
 *     const harness = await Harness.login("you@example.com"); // or new Harness(token)
 *     const projects = await harness.identity.list_projects(5);
 *     for await (const envelope of harness.events.stream(projectId, harness.token)) {
 *       console.log(envelope.seq, envelope.type);
 *     }
 *
 * Two layers:
 * - handwritten runtime: HarnessHttp (bearer auth + frozen error envelope +
 *   retries with backoff), EventStreamController / streamEvents (ws-ticket,
 *   fingerprint presentation, replay/dedupe streaming — extracted from
 *   section7/src/lib/{api,useEventStream,backoff}.ts);
 * - one generated client module per backend section (identity, github, router,
 *   orchestrator, sandbox, meeting, review), produced by
 *   `scripts/sdk-gen/generate.py --target ts` from the sections' OpenAPI specs.
 */
import { HarnessHttp } from "./runtime.js";
import { type EventEnvelope } from "./ws.js";
import { GithubClient } from "./github.js";
import { IdentityClient, SessionResponse, OkResponse } from "./identity.js";
import { MeetingClient } from "./meeting.js";
import { OrchestratorClient } from "./orchestrator.js";
import { ReviewClient } from "./review.js";
import { RouterClient } from "./router.js";
import { SandboxClient } from "./sandbox.js";
export { ApiError, parseApiError } from "./errors.js";
export { HarnessHttp, SERVICE_PORTS, baseUrlFor, SDK_USER_AGENT, BACKOFF_BASE_MS, BACKOFF_CAP_MS, } from "./runtime.js";
export { BACKOFF_BASE_DELAY_MS, BACKOFF_MAX_DELAY_MS, exponentialDelay, backoffDelay, } from "./backoff.js";
export { EventStreamController, streamEvents, parseFrame, computeWsFingerprint } from "./ws.js";
export type { EventEnvelope, EventStreamStatus, ConnectionState, WsTicketInfo, } from "./ws.js";
export { SERVICES } from "./services.js";
export type { ServiceKey } from "./services.js";
export * as identityTypes from "./identity.js";
export * as githubTypes from "./github.js";
export * as routerTypes from "./router.js";
export * as orchestratorTypes from "./orchestrator.js";
export * as sandboxTypes from "./sandbox.js";
export * as meetingTypes from "./meeting.js";
export * as reviewTypes from "./review.js";
/** Per-service base-URL overrides; default is the HARNESS_<SERVICE>_URL env
 *  var, else http://localhost:<default port> (same resolution as the
 *  Python SDK). */
export interface HarnessOptions {
    token?: string | null;
    /** Per-request timeout in milliseconds. */
    timeoutMs?: number;
    maxRetries?: number;
    identityUrl?: string;
    githubUrl?: string;
    routerUrl?: string;
    orchestratorUrl?: string;
    sandboxUrl?: string;
    meetingUrl?: string;
    reviewUrl?: string;
    http?: HarnessHttp;
}
interface PrincipalResponse {
    userId?: string;
    teamId?: string;
    role?: string;
}
/**
 * Aggregate SDK: `harness.<service>.<method>(...)` plus `harness.events`.
 */
export declare class Harness {
    token: string | null;
    readonly http: HarnessHttp;
    readonly identity: IdentityClient;
    readonly github: GithubClient;
    readonly router: RouterClient;
    readonly orchestrator: OrchestratorClient;
    readonly sandbox: SandboxClient;
    readonly meeting: MeetingClient;
    readonly review: ReviewClient;
    principal: PrincipalResponse;
    constructor(options?: HarnessOptions);
    /**
     * Legacy passwordless login -> ready Harness.
     *
     * Throws ApiError(code="OWNERSHIP_REQUIRED") when the deployment requires
     * inbox ownership (the default since H2r) — catch it and fall back to
     * requestOtp() + verifyOtp().
     */
    static login(email: string, opts?: Omit<HarnessOptions, "token"> & {
        teamName?: string;
    }): Promise<Harness>;
    /** Ownership login step 1: email a single-use code. Dev stacks print it to
     *  the identity container logs — grep `HARNESS-OTP`. */
    request_otp(email: string): Promise<OkResponse>;
    /** Ownership login step 1, camelCase alias. */
    requestOtp(email: string): Promise<OkResponse>;
    /** Ownership login step 2: swap the code for a session. Updates this
     *  instance's token in place and returns the raw response. */
    verify_otp(email: string, code: string): Promise<SessionResponse>;
    /** verify_otp camelCase alias. */
    verifyOtp(email: string, code: string): Promise<SessionResponse>;
    /** Streaming view over the per-project event bus — the twin of the Python
     *  SDK's sdk.events.stream(). Shares this instance's transport so ticket
     *  minting rides the same bearer-auth policy. Pass an AbortSignal as the
     *  third argument to stop consuming. */
    get events(): {
        stream: (projectId: string, token?: string, stopSignal?: AbortSignal) => AsyncGenerator<EventEnvelope>;
    };
}
