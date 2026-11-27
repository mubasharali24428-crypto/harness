"""Allow-list HTTPS CONNECT proxy: the only route out for opt-in sandbox networking.

Sandbox containers sit on an ``internal`` Docker network with no gateway; this
proxy is the one peer that also reaches the internet. Only ``CONNECT host:443``
to hosts accepted by ``is_allowed_destination`` is tunnelled. Plain HTTP and
every other method or port are refused.
"""

import asyncio
import os
import socket
import sys

from harness_sandbox import egress
from harness_sandbox.egress import is_allowed_destination

_ALLOWED_PORT = 443
_CONNECT_TIMEOUT = 10

# G9 (p04-sandbox-review §5): a wedged or hostile client could previously hold
# a tunnel forever (no idle deadline, no byte ceilings) and denials were
# invisible. Every tunnel now has hard caps, and denials are logged
# one-line-sampled with a per-host counter so a flood shows as a count, not a
# wall of lines (the compose healthcheck's 10 s denied probe stays quiet).
_TUNNEL_IDLE_TIMEOUT = int(os.environ.get("EGRESS_TUNNEL_IDLE_TIMEOUT", "300"))
_TUNNEL_MAX_BYTES = int(os.environ.get("EGRESS_TUNNEL_MAX_BYTES", "268435456"))  # 256 MiB
_TUNNEL_MAX_CONCURRENT = int(os.environ.get("EGRESS_TUNNEL_MAX_CONCURRENT", "16"))
_DENIAL_LOG_EVERY = 10

_active_tunnels = 0
_denial_counts: dict[str, int] = {}

#: Purpose ports. The sandbox shell gets 3128; only the engine worker is wired to
#: 3129, so "which port did you reach" is the purpose boundary (a caller cannot
#: be trusted to declare its own purpose in a header).
SANDBOX_PORT = int(os.environ.get("EGRESS_SANDBOX_PORT", "3128"))
WEB_PORT = int(os.environ.get("EGRESS_WEB_PORT", "3129"))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """One direction of an open tunnel, under byte + idle caps (G9)."""
    moved = 0
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), _TUNNEL_IDLE_TIMEOUT)
            if not data:
                break
            moved += len(data)
            if moved > _TUNNEL_MAX_BYTES:
                print(f"tunnel byte cap exceeded ({moved} bytes), closing", flush=True)
                break
            writer.write(data)
            await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _deny(writer: asyncio.StreamWriter, status: str) -> None:
    _denial_counts[status] = _denial_counts.get(status, 0) + 1
    if _denial_counts[status] == 1 or _denial_counts[status] % _DENIAL_LOG_EVERY == 0:
        print(f"denied {status} (count={_denial_counts[status]})", flush=True)
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n".encode())
    await writer.drain()
    writer.close()


def _log_tunnel(host: str, purpose: str) -> None:
    """One line per tunnel that actually opened — the proxy's audit trail.

    Only *allowed* tunnels are logged: a refusal is already visible to the
    caller, while this stream answers "what left the machine", which is what an
    install or fetch drill has to show. Denials are deliberately not logged
    because the compose healthcheck probes with a denied CONNECT every 10s.
    """
    print(f"CONNECT {host}:{_ALLOWED_PORT} purpose={purpose} allowed", flush=True)


async def handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    purpose: str = egress.PURPOSE_SANDBOX,
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), _CONNECT_TIMEOUT)
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass  # discard headers
        parts = request_line.decode("latin-1").split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            return await _deny(writer, "405 Method Not Allowed")
        host, _, port = parts[1].rpartition(":")
        if not host or port != str(_ALLOWED_PORT) or not is_allowed_destination(host, purpose):
            return await _deny(writer, "403 Forbidden")
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, _ALLOWED_PORT), _CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError):
            return await _deny(writer, "502 Bad Gateway")
        global _active_tunnels
        if _active_tunnels >= _TUNNEL_MAX_CONCURRENT:
            return await _deny(writer, "503 Busy")
        _active_tunnels += 1
        try:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            _log_tunnel(host, purpose)
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
        finally:
            _active_tunnels -= 1
    except (TimeoutError, ConnectionError, OSError):
        writer.close()


async def serve(
    host: str = "0.0.0.0",
    port: int = SANDBOX_PORT,
    web_port: int = WEB_PORT,
) -> None:
    """Two listeners, two purposes.

    ``SANDBOX_PORT`` is for sandboxed shells: package registries, git hosts and
    provider endpoints. ``WEB_PORT`` is for the engine's ``web_fetch`` tool:
    provider docs and provider endpoints. They are separate ports because a
    caller cannot be trusted to send an honest purpose header — the boundary is
    *which port you reached*, and only the engine worker gets the web port.
    """
    sandbox = await asyncio.start_server(
        lambda r, w: handle(r, w, egress.PURPOSE_SANDBOX), host, port
    )
    web = await asyncio.start_server(lambda r, w: handle(r, w, egress.PURPOSE_WEB), host, web_port)
    async with sandbox, web:
        await asyncio.gather(sandbox.serve_forever(), web.serve_forever())


def healthcheck(host: str = "127.0.0.1", ports: tuple[int, ...] | None = None) -> int:
    """Both listeners enforce their own allow-list, or this exits non-zero.

    A CONNECT to ``example.com`` must come back 403 on every purpose port. That
    needs no outbound network, so the check is deterministic on an isolated
    runner — and a dead listener, a broken allow-list or a wedged event loop all
    read as unhealthy instead of "up".
    """
    for port in ports or (SANDBOX_PORT, WEB_PORT):
        try:
            with socket.create_connection((host, port), 3) as sock:
                sock.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
                reply = sock.recv(64)
        except OSError as exc:
            print(f"port {port}: {exc}", file=sys.stderr)
            return 1
        if b" 403 " not in reply:
            print(f"port {port}: expected 403, got {reply!r}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    if "--healthcheck" in sys.argv[1:]:
        raise SystemExit(healthcheck())
    asyncio.run(serve())
