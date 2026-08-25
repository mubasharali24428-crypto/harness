"""WebSocket event streaming — extraction of section7/src/lib/useEventStream.ts.

The section7 React hook owns the browser-side lifecycle this module ports to
Python for scripters:

| concern                        | section7 source                     | here                    |
|--------------------------------|-------------------------------------|-------------------------|
| single-use ticket per connect  | ``EventStreamController.mintTicket`` | ``_mint_ticket`` (H8)   |
| fingerprint presentation       | ``buildUrl`` (?fingerprint= channel)| ``X-WS-Fingerprint`` header (M18-C5 makes it REQUIRED once mint recorded one; Python can set headers, so the header channel replaces the browser's query-param workaround) |
| resume from highest seq seen   | ``lastSeq`` / ``afterSeq``          | ``self.last_seq``       |
| seq dedupe on reconnect overlap| ``mergeEvents``                     | monotone ``last_seq`` filter |
| malformed-frame guard          | ``handleMessage`` (drop, not fatal) | ``_parse_frame``        |
| backoff reconnect w/ jitter, counter reset on open | backoff.ts + controller | ``backoff_delay`` + attempt reset |

Fingerprint arithmetic note: the binding value is ``sha256(userAgent +
clientIp)[:16]`` (section1 ``ephemeral.compute_client_fingerprint``, mirrored
by ``computeWsFingerprint`` in useEventStream.ts). The SDK never recomputes it:
POST /api/v1/ws-ticket ECHOES the stored binding (M18-C5), and presenting that
echoed value verbatim — over a connection whose User-Agent matches the mint
request — satisfies both the presentation check and the server-side
recomputation. Both requests therefore pin ``user-agent: harness-sdk/…``.

Bus overflow (queue overrun under sustained load) surfaces as close code 1013
from section1's WS handler; like every other drop in section7's controller it
flows into the same backoff reconnect, resuming from ``last_seq`` so the
server replays whatever the client missed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Any, AsyncIterator, Iterator

import httpx

from .errors import ApiError
from .runtime import SDK_USER_AGENT, HarnessHTTP, backoff_delay, base_url_for

logger = logging.getLogger("harness_sdk.ws")

try:  # websockets >= 11 ships the sync client; >= 13 prefers additional_headers
    from websockets.sync.client import connect as _ws_connect
    from websockets.exceptions import ConnectionClosed, InvalidStatus
except ImportError as _err:  # pragma: no cover - dependency guard
    raise RuntimeError(
        "harness_sdk event streaming needs 'websockets' (pip install websockets>=13)"
    ) from _err

__all__ = ["WsEventStream"]


def _ws_connect_kwargs(headers: dict[str, str]) -> dict[str, Any]:
    """websockets renamed extra_headers -> additional_headers in v13."""
    try:
        import inspect

        params = inspect.signature(_ws_connect).parameters
        key = "additional_headers" if "additional_headers" in params else "extra_headers"
    except (TypeError, ValueError):  # pragma: no cover - defensive
        key = "additional_headers"
    return {key: headers}


class WsEventStream:
    """Streaming event-bus client for ``GET /api/v1/ws/{projectId}``.

    Reuse it; don't hand-roll ticket/replay/dedupe logic (that duplication is
    exactly what mission finding F-3 flags). Typical use::

        sdk = Harness(token)
        with sdk.events.stream(PROJECT_ID, token) as stream:
            for envelope in stream:
                print(envelope["seq"], envelope["type"])
    """

    def __init__(
        self,
        http: HarnessHTTP,
        *,
        identity_base: str | None = None,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 30.0,
    ) -> None:
        self._http = http
        self.identity_base = (identity_base or base_url_for("identity")).rstrip("/")
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        #: Highest event seq delivered — resumed automatically on reconnect.
        self.last_seq = 0

    # ------------------------------------------------------------------
    # Ticket acquisition (extraction of api.ts fetchWsTicket + H8 law)
    # ------------------------------------------------------------------

    def _mint_ticket(self, project_id: str, token: str) -> dict[str, Any]:
        """Mint a fresh single-use, fingerprint-bound ticket.

        Tickets are ONE-SHOT: every connect attempt mints another one, so the
        long-lived bearer token never rides in a URL (H8). The response also
        carries the server-stored ``fingerprint`` echo (M18-C5) that the
        upgrade must present.
        """
        body = self._http.request(
            self.identity_base, "POST", "/api/v1/ws-ticket",
            json_body={"projectId": project_id}, auth=True, token=token,
        )
        ticket = body.get("ticket") if isinstance(body, dict) else None
        if not ticket:
            raise ApiError("ws-ticket response missing 'ticket'", "UNKNOWN",
                           body=body)
        fingerprint = body.get("fingerprint") if isinstance(body, dict) else None
        expires = body.get("expiresInSeconds") if isinstance(body, dict) else None
        logger.debug("minted ws-ticket (expiresInSeconds=%s)", expires)
        return {"ticket": ticket, "fingerprint": fingerprint}

    # ------------------------------------------------------------------
    # Frame handling (extraction of handleMessage + mergeEvents semantics)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_frame(raw: Any) -> dict[str, Any] | None:
        """Guarded decode: returns the envelope dict, a control dict
        (``replayTruncated`` markers), or None for garbage frames.

        section7's handleMessage drops anything without a numeric ``seq``;
        the SDK additionally recognises the M16-X3 ``replayTruncated``
        marker (which has no seq BY DESIGN) and surfaces it instead of
        silently discarding the notice that history was capped.
        """
        try:
            parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (ValueError, TypeError):
            logger.warning("malformed WS frame dropped: not JSON")
            return None
        if not isinstance(parsed, dict):
            logger.warning("malformed WS frame dropped: frame is not an object")
            return None
        if parsed.get("replayTruncated"):
            return parsed
        seq = parsed.get("seq")
        if not isinstance(seq, (int, float)) or isinstance(seq, bool):
            logger.warning("malformed WS frame dropped: missing numeric seq")
            return None
        return parsed

    def _accept_frame(self, envelope: dict[str, Any]) -> bool:
        """Seq dedupe + ordering: True when the caller should see this frame.

        mergeEvents guards the reconnect race where the server might resend
        an event whose seq we already have; for a linear stream the same
        guarantee is ``seq > last delivered seq`` (server replays ascend and
        live pushes arrive in publish order, so nothing undelivered can be
        skipped).
        """
        if "replayTruncated" in envelope and "seq" not in envelope:
            return True
        seq = envelope["seq"]
        if seq <= self.last_seq:
            logger.debug("dropped duplicate/stale frame seq=%s (last=%s)",
                         seq, self.last_seq)
            return False
        self.last_seq = seq
        return True

    # ------------------------------------------------------------------
    # Sync streaming API
    # ------------------------------------------------------------------

    def stream(
        self,
        project_id: str,
        token: str,
        *,
        after_seq: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield event envelopes forever until ``stop_event`` is set, the
        socket dies unrecoverably, or the consumer closes the generator.

        Reconnects with exponential backoff + jitter; resumes from the highest
        seq seen so the server replays the gap. Raises :class:`ApiError` for
        permanent auth failures (close 4401 / fingerprint mismatch 4403 /
        foreign project 4404).
        """
        own_stop = stop_event is None
        stop = stop_event or threading.Event()
        attempt = 0  # consecutive failures since last successful open
        try:
            while not stop.is_set():
                gen = self._generation = getattr(self, "_generation", 0) + 1
                try:
                    ticket_info = self._mint_ticket(project_id, token)
                    for frame in self._pump_once(project_id, ticket_info,
                                                 after_seq, stop):
                        if self._generation != gen:
                            break
                        if self._accept_frame(frame):
                            attempt = 0  # successful delivery resets backoff
                            yield frame
                        if stop.is_set():
                            break
                    if self._generation == gen and not stop.is_set():
                        # _pump_once returned: clean close by peer
                        attempt += 1
                except (_WsDrop,) as drop:
                    attempt += 1
                    mapped = _map_close(drop)
                    if mapped is not None:
                        raise mapped from None
                except ApiError as err:
                    if err.code in ("UNAUTHORIZED", "TICKET_FINGERPRINT_MISMATCH",
                                    "NOT_FOUND"):
                        raise
                    attempt += 1
                delay = backoff_delay(attempt, self.backoff_base_s, self.backoff_cap_s)
                if not stop.is_set():
                    logger.debug("reconnecting in %.2fs (attempt %d, last_seq=%d)",
                                 delay, attempt, self.last_seq)
                    stop.wait(delay)
        finally:
            self._generation = getattr(self, "_generation", 0) + 1  # orphan stale pumps

    def __enter__(self) -> "WsEventStream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def _pump_once(
        self,
        project_id: str,
        ticket_info: dict[str, Any],
        after_seq: int | None,
        stop: threading.Event,
    ) -> Iterator[dict[str, Any]]:
        """One connection lifetime: yields frames, raises _WsDrop when the
        socket closes for any reason (reconnect decisions belong to stream())."""
        url = (
            f"{self.identity_base}/api/v1/ws/{project_id}"
            f"?afterSeq={int(after_seq) if after_seq is not None else int(self.last_seq)}"
            f"&ticket={ticket_info['ticket']}"
        )
        # http(s) base URLs -> ws(s):// for the upgrade (websockets rejects
        # anything but ws/wss schemes).
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://" + url[len("http://"):]
        headers = {
            # MUST match the mint request's UA: the ticket is fingerprint-bound
            # to sha256(UA + ip), and the server recomputes it at upgrade.
            "user-agent": SDK_USER_AGENT,
        }
        if ticket_info.get("fingerprint"):
            # Presentation REQUIRED since M18-C5 once mint recorded a
            # fingerprint. Header channel (browsers use ?fingerprint= instead).
            headers["x-ws-fingerprint"] = str(ticket_info["fingerprint"])
        try:
            with _ws_connect(url, open_timeout=10,
                             **_ws_connect_kwargs(headers)) as ws:
                logger.debug("ws open project=%s afterSeq>=%d", project_id, self.last_seq)
                while True:
                    if stop.is_set():
                        ws.close()
                        return
                    try:
                        raw = ws.recv(timeout=5.0)
                    except TimeoutError:
                        continue  # ping/pong keeps liveness; re-check stop flag
                    frame = self._parse_frame(raw)
                    if frame is None:
                        continue
                    yield frame
        except (_WsDrop, StopIteration):
            raise
        except ConnectionClosed as err:
            raise _WsDrop(getattr(err, "rcvd", None)) from err
        except (OSError, RuntimeError, ValueError, TimeoutError) as err:
            raise _WsDrop(None) from err
        except InvalidStatus as err:  # handshake rejection (pre-accept close)
            status = None
            resp = getattr(err, "response", None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
            raise _WsReject(status) from err

    # ------------------------------------------------------------------
    # Async API — same lifecycle, pumped off-loop
    # ------------------------------------------------------------------

    async def astream(
        self,
        project_id: str,
        token: str,
        *,
        after_seq: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async twin of :meth:`stream` (runs the sync lifecycle on a worker
        thread and bridges frames onto the event loop)."""
        loop = asyncio.get_running_loop()
        bridge: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        stop = threading.Event()

        def pump() -> None:
            try:
                for envelope in self.stream(project_id, token,
                                            after_seq=after_seq, stop_event=stop):
                    loop.call_soon_threadsafe(bridge.put_nowait, ("event", envelope))
            except BaseException as err:  # noqa: BLE001 - forwarded to consumer
                loop.call_soon_threadsafe(bridge.put_nowait, ("error", err))
            finally:
                loop.call_soon_threadsafe(bridge.put_nowait, ("done", None))

        thread = threading.Thread(target=pump, name="harness-sdk-ws", daemon=True)
        thread.start()
        try:
            while True:
                kind, payload = await bridge.get()
                if kind == "event":
                    yield payload
                elif kind == "error":
                    raise payload
                else:
                    break
        finally:
            stop.set()


class _WsDrop(Exception):
    """Internal: transport-level drop (retryable). Carries close frame info."""

    def __init__(self, close: Any) -> None:
        super().__init__(f"ws dropped: {close!r}")
        self.close = close


class _WsReject(Exception):
    """Internal: handshake rejected pre-accept with an HTTP status."""

    def __init__(self, status: int | None) -> None:
        super().__init__(f"ws upgrade rejected (status={status})")
        self.status = status


def _map_close(err: Exception) -> ApiError | None:
    """Translate permanent WS rejections into ApiError; None = retryable."""
    if isinstance(err, _WsReject):
        status = err.status
        if status == 4401:
            return ApiError("WS rejected: session invalid or expired — log in again",
                            "UNAUTHORIZED", 401)
        if status == 4403:
            return ApiError(
                "WS rejected: TICKET_FINGERPRINT_MISMATCH — ticket replayed from "
                "a different client/host than the one that minted it",
                "TICKET_FINGERPRINT_MISMATCH", 403)
        if status == 4404:
            return ApiError("WS rejected: unknown or inaccessible project",
                            "NOT_FOUND", 404)
        return None  # generic rejection (e.g. proxy 502): retryable
    close = getattr(err, "close", None)
    code = getattr(close, "code", None)
    if code == 1013:
        # Bus overflow: retryable BY DESIGN — reconnect replays the missed span.
        logger.warning("bus overflow (1013); reconnecting to replay missed events")
        return None
    return None
