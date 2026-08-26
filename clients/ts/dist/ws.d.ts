/**
 * WebSocket event streaming — extraction of section7/src/lib/useEventStream.ts.
 *
 * The section7 React hook owns the browser-side lifecycle this module ports to
 * a framework-free TypeScript client (the twin of clients/python/harness_sdk/ws.py):
 *
 * | concern                        | section7 source                     | here                    |
 * |--------------------------------|-------------------------------------|-------------------------|
 * | single-use ticket per connect  | `EventStreamController.mintTicket`  | `mintTicket` (H8)       |
 * | fingerprint presentation       | `buildUrl` (?fingerprint= channel)  | `?fingerprint=` param   |
 * | resume from highest seq seen   | `lastSeq` / `afterSeq`              | `lastSeq`               |
 * | seq dedupe on reconnect overlap| `mergeEvents`                       | monotone `lastSeq` gate |
 * | malformed-frame guard          | `handleMessage` (drop, not fatal)   | `parseFrame`            |
 * | backoff reconnect w/ jitter    | backoff.ts + controller             | backoff.ts extraction   |
 *
 * Fingerprint arithmetic note: the binding value is sha256(userAgent +
 * clientIp)[:16] (section1 `ephemeral.compute_client_fingerprint`, mirrored by
 * `computeWsFingerprint` in useEventStream.ts). Like the browser client, this
 * SDK never recomputes the stored binding: POST /api/v1/ws-ticket ECHOES the
 * server-stored value (M18-C5), and presenting that echoed value verbatim —
 * over an upgrade whose User-Agent matches the mint request's — satisfies both
 * the presentation check and the server-side recomputation.
 *
 * NODE/undici HONEST WEAKENING (same spirit as section7's documented one):
 * Node's global WebSocket accepts no custom headers and pins `user-agent:
 * node`. The SDK therefore pins BOTH requests to that exact UA (mint fetch
 * headers + upgrade, which undici sends as `node`) and carries the echoed
 * fingerprint on the `?fingerprint=` query param — the same channel S7's
 * browser client uses. Browsers (global WebSocket, navigator.userAgent)
 * work identically: mint rides fetch with the browser UA, the upgrade sends
 * the same UA natively, and the fingerprint param matches either form.
 */
/** The User-Agent every harness-ts-sdk request presents (mint + upgrade must
 *  match; see the honest-weakening note above). */
export declare const WS_USER_AGENT = "node";
export interface EventEnvelope {
    type?: string;
    payload?: Record<string, unknown>;
    ts?: string;
    seq: number;
    projectId?: string;
    runId?: string;
}
export interface WsTicketInfo {
    ticket: string;
    /** Server-stored binding echoed at mint time (M18-C5). */
    fingerprint?: string | null;
    expiresInSeconds?: number;
}
export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";
export interface EventStreamStatus {
    state: ConnectionState;
    attempt: number;
    nextDelayMs: number;
}
export interface StreamOptions {
    identityBase?: string;
    afterSeq?: number;
    stopSignal?: AbortSignal;
}
export interface ControllerOptions {
    http: import("./runtime.js").HarnessHttp;
    identityBase?: string;
    /** Test seam: overrides single-use ticket minting entirely when provided. */
    mintTicket?: () => Promise<WsTicketInfo>;
    socketFactory?: SocketFactory;
    schedule?: TimerScheduler;
    onEvent?: (envelope: EventEnvelope) => void;
    onStateChange?: (status: EventStreamStatus) => void;
    /** Called once when the server permanently rejects the upgrade
     *  (4401 UNAUTHORIZED / 4403 TICKET_FINGERPRINT_MISMATCH / 4404 NOT_FOUND);
     *  the controller stops itself right after. */
    onPermanentClose?: (code: number | null) => void;
    log?: (...parts: unknown[]) => void;
}
export interface SocketLike {
    close(): void;
    onopen: (() => void) | null;
    onmessage: ((evt: {
        data: unknown;
    }) => void) | null;
    onerror: (() => void) | null;
    onclose: ((evt: {
        code?: number | null;
    }) => void) | null;
}
export type SocketFactory = (url: string) => SocketLike;
export type TimerScheduler = (fn: () => void, delayMs: number) => () => void;
/** Pure + sync: sha256(ua + ip)[:16] — byte-identical arithmetic to
 *  computeWsFingerprint in useEventStream.ts and to section1's
 *  ephemeral.compute_client_fingerprint. Pass undefined ip for the UA-only
 *  form. Exported for conformance tests against the server derivation. */
export declare function computeWsFingerprint(userAgent: string, clientIp?: string): string;
/** Guarded decode of one WS frame. section7's handleMessage drops anything
 *  without a numeric seq; like the Python runtime we additionally recognise
 *  the M16-X3 `replayTruncated` marker (no seq BY DESIGN) instead of silently
 *  discarding the notice that history was capped. Returns the parsed object,
 *  or null when the frame is garbage (never fatal). */
export declare function parseFrame(raw: unknown): Record<string, unknown> | null;
/**
 * Headless controller owning the full connect/reconnect lifecycle against an
 * injectable socket factory — the direct port of section7's
 * `EventStreamController` (the React hook there is a thin adapter over it).
 */
export declare class EventStreamController {
    private readonly http;
    private readonly identityBase;
    private readonly mintTicketOverride?;
    private readonly makeSocket;
    private readonly schedule;
    private readonly onEvent;
    private readonly onStateChange;
    private readonly onPermanentClose;
    private readonly log;
    private status;
    private stopped;
    /** Bumps on every state transition; stale sockets/timers are ignored. */
    private generation;
    private cancelTimer;
    /** Highest event seq seen — resumed automatically on reconnect. */
    lastSeq: number;
    private projectId;
    private token;
    constructor(options: ControllerOptions);
    getStatus(): EventStreamStatus;
    getLastSeq(): number;
    start(projectId: string, token: string): void;
    stop(): void;
    /** Test seam: simulate the transport dropping mid-flight. */
    forceDrop(): void;
    private currentSocket;
    /**
     * H8: tickets are single-use, so EVERY connect attempt mints a fresh one;
     * the long-lived bearer token never appears in a URL.
     */
    protected mintTicket(projectId: string, token: string): Promise<WsTicketInfo>;
    private connect;
    private buildUrl;
    private cancelPendingTimer;
    private handleClose;
    /** H-012: exponential backoff with full jitter (base 1s, cap 30s).
     *  Attempt count is surfaced through getStatus(). */
    private scheduleReconnect;
    private handleMessage;
    private setStatus;
}
/**
 * Generator wrapper around the controller — the ergonomic twin of the Python
 * SDK's `sdk.events.stream()`: yields envelopes forever until `stopSignal`
 * aborts or a permanent WS rejection (4401/4403/4404) closes the stream.
 * Reconnects with exponential backoff + jitter; resumes from the highest seq
 * seen so the server replays the gap. Permanent rejections surface as
 * ApiError (code UNAUTHORIZED / TICKET_FINGERPRINT_MISMATCH / NOT_FOUND).
 */
export declare function streamEvents(http: import("./runtime.js").HarnessHttp, projectId: string, token: string, options?: StreamOptions): AsyncGenerator<EventEnvelope>;
