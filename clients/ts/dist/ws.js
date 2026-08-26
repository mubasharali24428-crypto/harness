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
import { createHash } from "node:crypto";
import { ApiError } from "./errors.js";
import { baseUrlFor } from "./runtime.js";
import { backoffDelay, exponentialDelay, BACKOFF_MAX_DELAY_MS } from "./backoff.js";
/** The User-Agent every harness-ts-sdk request presents (mint + upgrade must
 *  match; see the honest-weakening note above). */
export const WS_USER_AGENT = "node";
const defaultSchedule = (fn, delayMs) => {
    const t = setTimeout(fn, delayMs);
    return () => clearTimeout(t);
};
const noop = () => { };
/** Pure + sync: sha256(ua + ip)[:16] — byte-identical arithmetic to
 *  computeWsFingerprint in useEventStream.ts and to section1's
 *  ephemeral.compute_client_fingerprint. Pass undefined ip for the UA-only
 *  form. Exported for conformance tests against the server derivation. */
export function computeWsFingerprint(userAgent, clientIp) {
    return createHash("sha256").update(`${userAgent ?? ""}${clientIp ?? ""}`).digest("hex").slice(0, 16);
}
/** Guarded decode of one WS frame. section7's handleMessage drops anything
 *  without a numeric seq; like the Python runtime we additionally recognise
 *  the M16-X3 `replayTruncated` marker (no seq BY DESIGN) instead of silently
 *  discarding the notice that history was capped. Returns the parsed object,
 *  or null when the frame is garbage (never fatal). */
export function parseFrame(raw) {
    let parsed = raw;
    if (typeof raw === "string" || raw instanceof Uint8Array || raw instanceof ArrayBuffer) {
        try {
            parsed = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw));
        }
        catch {
            return null; // malformed frame dropped
        }
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return null; // frame is not an object
    }
    const obj = parsed;
    if (obj.replayTruncated === true && obj.seq === undefined)
        return obj;
    const seq = obj.seq;
    if (typeof seq !== "number" || !Number.isFinite(seq))
        return null;
    return obj;
}
/**
 * Headless controller owning the full connect/reconnect lifecycle against an
 * injectable socket factory — the direct port of section7's
 * `EventStreamController` (the React hook there is a thin adapter over it).
 */
export class EventStreamController {
    http;
    identityBase;
    mintTicketOverride;
    makeSocket;
    schedule;
    onEvent;
    onStateChange;
    onPermanentClose;
    log;
    status = { state: "connecting", attempt: 0, nextDelayMs: 0 };
    stopped = false;
    /** Bumps on every state transition; stale sockets/timers are ignored. */
    generation = 0;
    cancelTimer = null;
    /** Highest event seq seen — resumed automatically on reconnect. */
    lastSeq = 0;
    projectId = "";
    token = "";
    constructor(options) {
        this.http = options.http;
        this.identityBase = (options.identityBase ?? baseUrlFor("identity")).replace(/\/+$/, "");
        this.mintTicketOverride = options.mintTicket;
        // Node ≥22 ships the global WebSocket (undici); browsers likewise.
        this.makeSocket = options.socketFactory ?? ((url) => new WebSocket(url));
        this.schedule = options.schedule ?? defaultSchedule;
        this.onEvent = options.onEvent ?? noop;
        this.onStateChange = options.onStateChange ?? noop;
        this.onPermanentClose = options.onPermanentClose ?? noop;
        this.log = options.log ?? noop;
    }
    getStatus() {
        return { ...this.status };
    }
    getLastSeq() {
        return this.lastSeq;
    }
    start(projectId, token) {
        this.projectId = projectId;
        this.token = token;
        this.stopped = false;
        void this.connect();
    }
    stop() {
        this.stopped = true;
        this.generation += 1;
        this.cancelPendingTimer();
        this.currentSocket?.close();
        this.currentSocket = null;
        this.setStatus({ state: "closed", nextDelayMs: 0 });
    }
    /** Test seam: simulate the transport dropping mid-flight. */
    forceDrop() {
        this.handleClose(this.generation);
    }
    currentSocket = null;
    /**
     * H8: tickets are single-use, so EVERY connect attempt mints a fresh one;
     * the long-lived bearer token never appears in a URL.
     */
    async mintTicket(projectId, token) {
        if (this.mintTicketOverride)
            return this.mintTicketOverride();
        const body = await this.http.request(this.identityBase, "/api/v1/ws-ticket", "POST", { projectId }, undefined, 
        // The ticket is fingerprint-bound to sha256(UA + IP): this request's UA
        // MUST equal the one the WebSocket upgrade sends (undici pins "node";
        // browsers send navigator.userAgent natively on both channels).
        { auth: true, token, userAgent: WS_USER_AGENT });
        const ticket = typeof body?.ticket === "string" ? body.ticket : null;
        if (!ticket) {
            throw new ApiError("ws-ticket response missing 'ticket'", "UNKNOWN");
        }
        return {
            ticket,
            fingerprint: typeof body.fingerprint === "string" ? body.fingerprint : null,
            expiresInSeconds: typeof body.expiresInSeconds === "number" ? body.expiresInSeconds : undefined,
        };
    }
    async connect() {
        if (this.stopped)
            return;
        const gen = ++this.generation;
        this.cancelPendingTimer();
        this.setStatus({
            state: this.status.attempt > 0 ? "reconnecting" : "connecting",
            nextDelayMs: 0,
        });
        let ticketInfo;
        try {
            ticketInfo = await this.mintTicket(this.projectId, this.token);
        }
        catch {
            // Ticket minting failed (backend down / auth rejected / no session).
            if (this.stopped || gen !== this.generation)
                return;
            this.scheduleReconnect();
            return;
        }
        if (this.stopped || gen !== this.generation)
            return;
        const url = this.buildUrl(ticketInfo);
        const ws = this.makeSocket(url);
        this.currentSocket = ws;
        ws.onopen = () => {
            if (gen !== this.generation)
                return;
            // A successful open clears the consecutive-failure count, so the next
            // drop starts the backoff sequence over from the base delay.
            this.setStatus({ state: "open", attempt: 0 });
        };
        ws.onmessage = (evt) => {
            if (gen !== this.generation)
                return;
            this.handleMessage(evt.data);
        };
        ws.onerror = () => {
            if (gen !== this.generation)
                return;
            try {
                ws.close();
            }
            catch {
                /* already closing */
            }
        };
        ws.onclose = (evt) => {
            if (gen !== this.generation)
                return;
            const code = evt?.code ?? null;
            if (code === 4401 || code === 4403 || code === 4404) {
                // Permanent rejection — stop and surface it instead of spinning.
                this.stopped = true;
                this.setStatus({ state: "closed", nextDelayMs: 0 });
                this.log(`stream closed permanently (${code})`);
                this.onPermanentClose(code);
                return;
            }
            this.handleClose(gen); // incl. 1013 bus overflow → replaying reconnect
        };
    }
    buildUrl(ticketInfo) {
        // Single-use tickets are short-lived secrets — always URL-encode them and
        // never log or persist the resulting URL.
        const pid = encodeURIComponent(this.projectId);
        // M15-W5-S2/M18-C5: carry the echoed client fingerprint on the upgrade.
        // Node's WebSocket cannot set custom headers, so the X-WS-Fingerprint
        // value rides as a query param — the same browser channel S7 uses, and
        // the server accepts header-or-param. The mint request pinned the exact
        // UA this socket sends ("node" under undici), so the server-side
        // sha256(UA+IP) recomputation still validates.
        let fpParam = "";
        if (ticketInfo.fingerprint)
            fpParam = `&fingerprint=${encodeURIComponent(ticketInfo.fingerprint)}`;
        const base = this.identityBase
            .replace(/^https:\/\//, "wss://")
            .replace(/^http:\/\//, "ws://");
        return `${base}/api/v1/ws/${pid}?afterSeq=${this.lastSeq}&ticket=${encodeURIComponent(ticketInfo.ticket)}${fpParam}`;
    }
    cancelPendingTimer() {
        if (this.cancelTimer) {
            this.cancelTimer();
            this.cancelTimer = null;
        }
    }
    handleClose(gen) {
        if (this.stopped || gen !== this.generation)
            return;
        this.currentSocket = null;
        this.scheduleReconnect();
    }
    /** H-012: exponential backoff with full jitter (base 1s, cap 30s).
     *  Attempt count is surfaced through getStatus(). */
    scheduleReconnect() {
        if (this.stopped)
            return;
        const attempt = this.status.attempt + 1;
        const ceiling = exponentialDelay(attempt);
        const jittered = Math.min(BACKOFF_MAX_DELAY_MS, backoffDelay(attempt));
        this.setStatus({ state: "reconnecting", attempt, nextDelayMs: ceiling });
        const genAtSchedule = ++this.generation;
        this.cancelPendingTimer();
        let cancelled = false;
        this.cancelTimer = () => {
            cancelled = true;
        };
        const fire = () => {
            if (cancelled || this.stopped || genAtSchedule !== this.generation)
                return;
            this.cancelTimer = null;
            void this.connect();
        };
        void this.schedule(fire, jittered);
    }
    handleMessage(raw) {
        // Guarded parse — a malformed frame is logged and dropped, never allowed
        // to throw out of the message handler.
        const frame = parseFrame(raw);
        if (frame === null) {
            this.log("[harness-ts-sdk] malformed WS frame dropped:", raw);
            return;
        }
        if (!("seq" in frame)) {
            // replayTruncated marker: surface it, don't update lastSeq.
            this.onEvent(frame);
            return;
        }
        const envelope = frame;
        // mergeEvents semantics for a linear stream: accept strictly ascending
        // seq; duplicates/stale frames (server re-send racing our reconnect) drop.
        if (envelope.seq <= this.lastSeq)
            return;
        this.lastSeq = envelope.seq;
        try {
            this.onEvent(envelope);
        }
        catch (err) {
            // A consumer bug must not tear down the socket either.
            this.log("[harness-ts-sdk] onEvent handler threw:", err instanceof Error ? err.message : err);
        }
    }
    setStatus(patch) {
        this.status = { ...this.status, ...patch };
        this.onStateChange(this.getStatus());
    }
}
/**
 * Generator wrapper around the controller — the ergonomic twin of the Python
 * SDK's `sdk.events.stream()`: yields envelopes forever until `stopSignal`
 * aborts or a permanent WS rejection (4401/4403/4404) closes the stream.
 * Reconnects with exponential backoff + jitter; resumes from the highest seq
 * seen so the server replays the gap. Permanent rejections surface as
 * ApiError (code UNAUTHORIZED / TICKET_FINGERPRINT_MISMATCH / NOT_FOUND).
 */
export async function* streamEvents(http, projectId, token, options = {}) {
    const identityBase = options.identityBase ?? baseUrlFor("identity");
    const queue = [];
    let wake = null;
    // TS narrows `terminal` to never after the throw below (assignment happens
    // only inside a callback it cannot see), so keep it in an opaque holder.
    const terminal = {};
    const controller = new EventStreamController({
        http,
        identityBase,
        onEvent: (envelope) => {
            queue.push(envelope);
            wake?.();
        },
        onPermanentClose: (code) => {
            const map = {
                4401: ["UNAUTHORIZED", "WS rejected: session invalid or expired — log in again"],
                4403: [
                    "TICKET_FINGERPRINT_MISMATCH",
                    "WS rejected: ticket replayed from a different client/host than the one that minted it",
                ],
                4404: ["NOT_FOUND", "WS rejected: unknown or inaccessible project"],
            };
            const entry = code !== null && map[code] ? map[code] : null;
            terminal.err = new ApiError(entry?.[1] ?? `WS closed permanently (${code ?? "unknown"})`, entry?.[0] ?? "UNKNOWN", code === null ? 0 : Number(code));
            wake?.();
        },
        log: () => { },
    });
    const onAbort = () => {
        controller.stop();
        wake?.();
    };
    options.stopSignal?.addEventListener("abort", onAbort, { once: true });
    controller.start(projectId, token);
    try {
        for (;;) {
            while (queue.length > 0) {
                yield queue.shift();
            }
            if (terminal.err !== undefined)
                throw terminal.err;
            if (options.stopSignal?.aborted)
                return;
            await new Promise((resolve) => {
                wake = resolve;
            });
            wake = null;
        }
    }
    finally {
        options.stopSignal?.removeEventListener("abort", onAbort);
        controller.stop();
    }
}
