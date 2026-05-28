from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .protocol import Frame, read_frame, write_frame


@dataclass
class ChannelInfo:
    name: str
    host_ip: str
    host_port: int
    is_public: bool
    user_count: int
    max_users: int


class ChannelServer:
    def __init__(
        self,
        name: str,
        password: str,
        max_users: int,
        is_public: bool,
        owner_login: str,
        on_message: Callable[[str, str, float], None] | None = None,
        on_member_join: Callable[[str], None] | None = None,
        on_member_leave: Callable[[str], None] | None = None,
    ) -> None:
        self.name = name
        self.password = password
        self.max_users = max_users
        self.is_public = is_public
        self.owner_login = owner_login
        self.on_message = on_message
        self.on_member_join = on_member_join
        self.on_member_leave = on_member_leave

        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._server: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self, host: str = "0.0.0.0", port: int = 0) -> int:
        self._server = await asyncio.start_server(
            self._handle_client, host, port,
        )
        socks = self._server.sockets or []
        self.port = socks[0].getsockname()[1]

        self._clients[self.owner_login] = None
        return self.port

    async def stop(self) -> None:
        leave = {"type": "CHANNEL_CLOSED", "reason": "host disconnected"}
        for login, writer in list(self._clients.items()):
            if writer is not None:
                try:
                    await write_frame(writer, leave)
                except Exception:
                    pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def broadcast(self, frame: Frame, exclude: str | None = None) -> None:
        for login, writer in list(self._clients.items()):
            if login == exclude:
                continue
            if writer is None:
                continue
            try:
                await write_frame(writer, frame)
            except Exception:
                pass

    async def local_message(self, sender_login: str, payload: str) -> None:
        now = time.time()
        frame: Frame = {
            "type": "MESSAGE",
            "sender_login": sender_login,
            "payload": payload,
            "timestamp": now,
        }
        await self.broadcast(frame, exclude=sender_login)
        if self.on_message:
            self.on_message(sender_login, payload, now)

    def member_count(self) -> int:
        return len(self._clients)

    def member_logins(self) -> list[str]:
        return list(self._clients.keys())

    def beacon_info(self, local_ip: str) -> ChannelInfo:
        return ChannelInfo(
            name=self.name,
            host_ip=local_ip,
            host_port=self.port,
            is_public=self.is_public,
            user_count=len(self._clients),
            max_users=self.max_users,
        )

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        login: str | None = None
        try:
            info: Frame = {
                "type": "CHANNEL_INFO",
                "channel_name": self.name,
                "is_public": self.is_public,
                "max_users": self.max_users,
                "user_count": len(self._clients),
            }
            await write_frame(writer, info)

            join_frame = await read_frame(reader)
            if join_frame.get("type") != "JOIN":
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "expected JOIN"})
                return

            login = str(join_frame.get("login", ""))
            password = str(join_frame.get("password", ""))

            if not login:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "login required"})
                return

            if login in self._clients:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "login already in channel"})
                return

            if login == self.owner_login:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "login reserved"})
                return

            if not self.is_public and password != self.password:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "wrong password"})
                return

            if len(self._clients) >= self.max_users:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "channel full"})
                return

            self._clients[login] = writer

            members = list(self._clients.keys())
            await write_frame(writer, {
                "type": "JOIN_ACCEPTED",
                "channel_name": self.name,
                "members": members,
            })

            await self.broadcast({"type": "USER_JOINED", "login": login}, exclude=login)
            if self.on_member_join:
                self.on_member_join(login)

            while True:
                frame = await read_frame(reader)
                if frame.get("type") == "LEAVE":
                    break
                if frame.get("type") == "MESSAGE":
                    now = time.time()
                    payload = str(frame.get("payload", ""))
                    msg_frame: Frame = {
                        "type": "MESSAGE",
                        "sender_login": login,
                        "payload": payload,
                        "timestamp": now,
                    }
                    await self.broadcast(msg_frame, exclude=login)
                    if self.on_message:
                        self.on_message(login, payload, now)

        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            if login and login in self._clients:
                del self._clients[login]
                await self.broadcast({"type": "USER_LEFT", "login": login})
                if self.on_member_leave:
                    self.on_member_leave(login)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


class ChannelClient:
    def __init__(
        self,
        login: str,
        on_message: Callable[[str, str, float], None] | None = None,
        on_member_join: Callable[[str], None] | None = None,
        on_member_leave: Callable[[str], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
    ) -> None:
        self.login = login
        self.on_message = on_message
        self.on_member_join = on_member_join
        self.on_member_leave = on_member_leave
        self.on_disconnect = on_disconnect

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.channel_name: str = ""
        self.members: list[str] = []
        self._connected = False
        self._read_task: asyncio.Task[None] | None = None

    async def connect(
        self, host: str, port: int, password: str, login: str,
    ) -> tuple[str, str]:
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=5.0,
            )
        except (TimeoutError, OSError) as exc:
            return "connect_failed", str(exc)

        try:
            info = await read_frame(self.reader)
            if info.get("type") != "CHANNEL_INFO":
                return "protocol_error", "expected CHANNEL_INFO"

            await write_frame(self.writer, {
                "type": "JOIN", "login": login, "password": password,
            })

            response = await asyncio.wait_for(
                read_frame(self.reader), timeout=5.0,
            )
            if response.get("type") == "JOIN_REJECTED":
                reason = str(response.get("reason", "unknown"))
                self.writer.close()
                return "rejected", reason

            if response.get("type") != "JOIN_ACCEPTED":
                self.writer.close()
                return "protocol_error", "expected JOIN_ACCEPTED"

            self.channel_name = str(response.get("channel_name", ""))
            self.members = list(response.get("members", []))
            self._connected = True
            self._read_task = asyncio.create_task(self._read_loop())

            for m in self.members:
                if m != login and self.on_member_join:
                    self.on_member_join(m)

            return "connected", self.channel_name

        except (TimeoutError, OSError, asyncio.IncompleteReadError) as exc:
            self.writer.close()
            return "connect_failed", str(exc)

    async def disconnect(self) -> None:
        self._connected = False
        if self.writer:
            try:
                await write_frame(self.writer, {"type": "LEAVE"})
            except Exception:
                pass
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None

    async def send_message(self, payload: str) -> bool:
        if not self._connected or not self.writer:
            return False
        try:
            await write_frame(self.writer, {
                "type": "MESSAGE", "payload": payload,
            })
            return True
        except (OSError, ConnectionError):
            self._connected = False
            return False

    async def _read_loop(self) -> None:
        try:
            while self._connected and self.reader:
                frame = await read_frame(self.reader)
                ftype = frame.get("type")

                if ftype == "MESSAGE":
                    sender = str(frame.get("sender_login", ""))
                    payload = str(frame.get("payload", ""))
                    ts = float(frame.get("timestamp", time.time()))
                    if self.on_message and sender:
                        self.on_message(sender, payload, ts)

                elif ftype == "USER_JOINED":
                    login = str(frame.get("login", ""))
                    if login and login not in self.members:
                        self.members.append(login)
                    if self.on_member_join:
                        self.on_member_join(login)

                elif ftype == "USER_LEFT":
                    login = str(frame.get("login", ""))
                    if login in self.members:
                        self.members.remove(login)
                    if self.on_member_leave:
                        self.on_member_leave(login)

                elif ftype == "CHANNEL_CLOSED":
                    reason = str(frame.get("reason", "closed"))
                    self._connected = False
                    if self.on_disconnect:
                        self.on_disconnect(reason)
                    break

                else:
                    pass

        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._connected = False
            if self.on_disconnect:
                self.on_disconnect("connection lost")
