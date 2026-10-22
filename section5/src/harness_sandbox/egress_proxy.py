"""Allow-list HTTPS CONNECT proxy: the only route out for opt-in sandbox networking.

Sandbox containers sit on an ``internal`` Docker network with no gateway; this
proxy is the one peer that also reaches the internet. Only ``CONNECT host:443``
to hosts accepted by ``is_allowed_destination`` is tunnelled. Plain HTTP and
every other method or port are refused.
"""

import asyncio

from harness_sandbox.egress import is_allowed_destination

_ALLOWED_PORT = 443
_CONNECT_TIMEOUT = 10


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _deny(writer: asyncio.StreamWriter, status: str) -> None:
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n".encode())
    await writer.drain()
    writer.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), _CONNECT_TIMEOUT)
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass  # discard headers
        parts = request_line.decode("latin-1").split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            return await _deny(writer, "405 Method Not Allowed")
        host, _, port = parts[1].rpartition(":")
        if not host or port != str(_ALLOWED_PORT) or not is_allowed_destination(host):
            return await _deny(writer, "403 Forbidden")
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, _ALLOWED_PORT), _CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError):
            return await _deny(writer, "502 Bad Gateway")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    except (TimeoutError, ConnectionError, OSError):
        writer.close()


async def serve(host: str = "0.0.0.0", port: int = 3128) -> None:
    server = await asyncio.start_server(handle, host, port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
