from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .protocol import Frame, read_frame, write_frame


FrameHandler = Callable[[Frame, str], Awaitable[None]]


class AsyncTransport:
    def __init__(self, host: str = "0.0.0.0", port: int = 0, on_frame: FrameHandler | None = None) -> None:
        self.host = host
        self.port = port
        self.on_frame = on_frame
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        if self._server is not None:
            sockets = self._server.sockets or []
            return sockets[0].getsockname()[1] if sockets else self.port

        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sockets = self._server.sockets or []
        if not sockets:
            raise RuntimeError("TCP server started without bound socket")
        self.port = sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def send_frame(self, ip: str, port: int, frame: Frame, timeout: float = 3.0) -> bool:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
            await write_frame(writer, frame)
            writer.close()
            await writer.wait_closed()
            return True
        except (TimeoutError, OSError, asyncio.IncompleteReadError):
            return False

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        sender_ip = peername[0] if peername else "unknown"

        try:
            while True:
                frame = await read_frame(reader)
                if self.on_frame is not None:
                    await self.on_frame(frame, sender_ip)
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
