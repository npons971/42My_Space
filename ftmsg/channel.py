from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .protocol import Frame, read_frame, write_frame

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_TIMEOUT = 90.0
RATE_LIMIT_WINDOW = 10.0
RATE_LIMIT_MAX = 10

LOGIN_RE = re.compile(r"^[a-zA-Z0-9_-]{1,20}$")


def validate_login(login: str) -> str | None:
    if not login:
        return "login required"
    if not LOGIN_RE.match(login):
        return "login must be 1-20 chars, alphanumeric/_/-"
    return None


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
        self._member_keys: dict[str, str] = {}
        self._last_activity: dict[str, float] = {}
        self._rate_limits: dict[str, list[float]] = {}
        self._banned: set[str] = set()
        self._server: asyncio.AbstractServer | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self.port: int = 0

    async def start(self, host: str = "0.0.0.0", port: int = 0) -> int:
        self._server = await asyncio.start_server(
            self._handle_client, host, port,
        )
        socks = self._server.sockets or []
        self.port = socks[0].getsockname()[1]

        self._clients[self.owner_login] = None
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self.port

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        leave = {"type": "CHANNEL_CLOSED", "reason": "host disconnected"}
        for login, writer in list(self._clients.items()):
            if writer is not None:
                try:
                    await write_frame(writer, leave)
                except Exception:
                    logger.debug("broadcast CHANNEL_CLOSED failed for %s", login, exc_info=True)
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    logger.debug("close writer failed for %s", login, exc_info=True)
        self._clients.clear()
        self._member_keys.clear()
        self._last_activity.clear()
        self._rate_limits.clear()
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
                logger.debug("broadcast to %s failed", login, exc_info=True)

    async def send_to(self, login: str, frame: Frame) -> bool:
        writer = self._clients.get(login)
        if writer is None:
            return False
        try:
            await write_frame(writer, frame)
            return True
        except Exception:
            logger.debug("send_to %s failed", login, exc_info=True)
            return False

    async def local_message(self, sender_login: str, payload: str) -> bool:
        if not self._check_rate_limit(sender_login):
            return False
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
        return True

    def member_count(self) -> int:
        return len(self._clients)

    def member_logins(self) -> list[str]:
        return list(self._clients.keys())

    def is_banned(self, login: str) -> bool:
        return login in self._banned

    async def kick(self, login: str) -> bool:
        writer = self._clients.get(login)
        if writer is None:
            return False
        try:
            await write_frame(writer, {"type": "KICKED", "reason": "kicked by host"})
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass
        return True

    async def ban(self, login: str) -> bool:
        self._banned.add(login)
        return await self.kick(login)

    def beacon_info(self, local_ip: str) -> ChannelInfo:
        return ChannelInfo(
            name=self.name,
            host_ip=local_ip,
            host_port=self.port,
            is_public=self.is_public,
            user_count=len(self._clients),
            max_users=self.max_users,
        )

    def _check_rate_limit(self, login: str) -> bool:
        now = time.time()
        timestamps = self._rate_limits.get(login, [])
        timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
        self._rate_limits[login] = timestamps
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        timestamps.append(now)
        return True

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self.broadcast({"type": "PING"})
                # Disconnect idle peers
                now = time.time()
                stale = [
                    login for login, ts in list(self._last_activity.items())
                    if now - ts > HEARTBEAT_TIMEOUT
                ]
                for login in stale:
                    writer = self._clients.get(login)
                    if writer is not None:
                        try:
                            writer.close()
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Enable TCP keepalive to detect dead peers
        sock = writer.transport.get_extra_info("socket")
        if sock is not None and hasattr(socket, "SO_KEEPALIVE"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, "TCP_KEEPIDLE"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except OSError:
                pass

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

            join_frame = await asyncio.wait_for(read_frame(reader), timeout=10.0)
            if join_frame.get("type") != "JOIN":
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "expected JOIN"})
                return

            login = str(join_frame.get("login", ""))
            password = str(join_frame.get("password", ""))
            enc_pubkey = str(join_frame.get("encryption_pubkey_b64", ""))

            err = validate_login(login)
            if err:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": err})
                return

            if login in self._clients:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "login already in channel"})
                return

            if login == self.owner_login:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "login reserved"})
                return

            if login in self._banned:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "banned"})
                return

            if not self.is_public and password != self.password:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "wrong password"})
                return

            if len(self._clients) >= self.max_users:
                await write_frame(writer, {"type": "JOIN_REJECTED", "reason": "channel full"})
                return

            self._clients[login] = writer
            if enc_pubkey:
                self._member_keys[login] = enc_pubkey
            self._last_activity[login] = time.time()

            members = list(self._clients.keys())
            await write_frame(writer, {
                "type": "JOIN_ACCEPTED",
                "channel_name": self.name,
                "members": members,
                "member_keys": dict(self._member_keys),
            })

            await self.broadcast({"type": "USER_JOINED", "login": login}, exclude=login)
            if self.on_member_join:
                self.on_member_join(login)

            while True:
                frame = await asyncio.wait_for(read_frame(reader), timeout=60.0)
                self._last_activity[login] = time.time()
                ftype = frame.get("type")

                if ftype == "LEAVE":
                    break
                if ftype == "PONG":
                    continue
                if ftype == "ROOM_KEY":
                    target = str(frame.get("target_login", ""))
                    if target and target in self._clients:
                        target_writer = self._clients[target]
                        if target_writer is not None:
                            try:
                                await write_frame(target_writer, frame)
                            except Exception:
                                logger.debug("ROOM_KEY routing failed for %s", target, exc_info=True)
                    continue
                if ftype == "PRIVATE_MESSAGE":
                    target = str(frame.get("target_login", ""))
                    if target and target in self._clients:
                        target_writer = self._clients[target]
                        if target_writer is not None:
                            try:
                                await write_frame(target_writer, frame)
                            except Exception:
                                logger.debug("PRIVATE_MESSAGE routing failed for %s", target, exc_info=True)
                    continue
                if ftype == "MESSAGE":
                    if not self._check_rate_limit(login):
                        try:
                            await write_frame(writer, {"type": "RATE_LIMITED", "retry_after": int(RATE_LIMIT_WINDOW)})
                        except Exception:
                            pass
                        continue
                    now = time.time()
                    payload = str(frame.get("payload", ""))
                    payload_enc = str(frame.get("payload_encrypted", ""))
                    msg_frame: Frame = {
                        "type": "MESSAGE",
                        "sender_login": login,
                        "timestamp": now,
                    }
                    if payload_enc:
                        msg_frame["payload_encrypted"] = payload_enc
                    else:
                        msg_frame["payload"] = payload
                    await self.broadcast(msg_frame, exclude=login)
                    display = payload if payload else "[encrypted]"
                    if self.on_message:
                        self.on_message(login, display, now)

        except (asyncio.IncompleteReadError, ConnectionError, OSError, TimeoutError):
            pass
        finally:
            if login and login in self._clients:
                del self._clients[login]
                self._last_activity.pop(login, None)
                self._rate_limits.pop(login, None)
                await self.broadcast({"type": "USER_LEFT", "login": login})
                if self.on_member_leave:
                    self.on_member_leave(login)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                logger.debug("close peer writer failed", exc_info=True)


class ChannelClient:
    def __init__(
        self,
        login: str,
        on_message: Callable[[str, str, float], None] | None = None,
        on_member_join: Callable[[str], None] | None = None,
        on_member_leave: Callable[[str], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
        on_room_key: Callable[[str], None] | None = None,
    ) -> None:
        self.login = login
        self.on_message = on_message
        self.on_member_join = on_member_join
        self.on_member_leave = on_member_leave
        self.on_disconnect = on_disconnect
        self.on_room_key = on_room_key

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.channel_name: str = ""
        self.members: list[str] = []
        self.member_keys: dict[str, str] = {}
        self._connected = False
        self._read_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._last_server_frame: float = 0.0

    async def connect(
        self, host: str, port: int, password: str, login: str,
        encryption_pubkey_b64: str = "",
    ) -> tuple[str, str]:
        err = validate_login(login)
        if err:
            return "invalid_login", err

        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=5.0,
            )
        except (TimeoutError, OSError) as exc:
            return "connect_failed", str(exc)

        # Enable TCP keepalive
        sock = self.writer.transport.get_extra_info("socket")
        if sock is not None and hasattr(socket, "SO_KEEPALIVE"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, "TCP_KEEPIDLE"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except OSError:
                pass

        try:
            info = await read_frame(self.reader)
            if info.get("type") != "CHANNEL_INFO":
                return "protocol_error", "expected CHANNEL_INFO"

            join_frame: Frame = {
                "type": "JOIN", "login": login, "password": password,
            }
            if encryption_pubkey_b64:
                join_frame["encryption_pubkey_b64"] = encryption_pubkey_b64
            await write_frame(self.writer, join_frame)

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
            self.member_keys = dict(response.get("member_keys", {}))
            self._connected = True
            self._last_server_frame = time.time()
            self._read_task = asyncio.create_task(self._read_loop())
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

            for m in self.members:
                if m != login and self.on_member_join:
                    self.on_member_join(m)

            return "connected", self.channel_name

        except (TimeoutError, OSError, asyncio.IncompleteReadError) as exc:
            self.writer.close()
            return "connect_failed", str(exc)

    async def disconnect(self) -> None:
        self._connected = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self.writer:
            try:
                await write_frame(self.writer, {"type": "LEAVE"})
            except Exception:
                logger.debug("send LEAVE failed", exc_info=True)
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                logger.debug("close writer failed", exc_info=True)
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

    async def send_room_key(self, target_login: str, encrypted_key: str) -> bool:
        if not self._connected or not self.writer:
            return False
        try:
            await write_frame(self.writer, {
                "type": "ROOM_KEY",
                "target_login": target_login,
                "encrypted_key": encrypted_key,
            })
            return True
        except (OSError, ConnectionError):
            self._connected = False
            return False

    async def _read_loop(self) -> None:
        try:
            while self._connected and self.reader:
                frame = await read_frame(self.reader)
                self._last_server_frame = time.time()
                ftype = frame.get("type")

                if ftype == "MESSAGE":
                    sender = str(frame.get("sender_login", ""))
                    payload = str(frame.get("payload", ""))
                    payload_enc = str(frame.get("payload_encrypted", ""))
                    ts = float(frame.get("timestamp", time.time()))
                    display = payload if payload else payload_enc
                    if self.on_message and sender:
                        self.on_message(sender, display, ts)

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

                elif ftype == "PING":
                    try:
                        await write_frame(self.writer, {"type": "PONG"})
                    except Exception:
                        logger.debug("send PONG failed", exc_info=True)

                elif ftype == "RATE_LIMITED":
                    retry = int(frame.get("retry_after", 10))
                    if self.on_message:
                        self.on_message("", f"[Rate limit: wait {retry}s]", time.time())

                elif ftype == "ROOM_KEY":
                    encrypted_key = str(frame.get("encrypted_key", ""))
                    if encrypted_key and self.on_room_key:
                        self.on_room_key(encrypted_key)

                elif ftype == "KICKED":
                    reason = str(frame.get("reason", "kicked"))
                    self._connected = False
                    if self.on_disconnect:
                        self.on_disconnect(reason)
                    break

                elif ftype == "PRIVATE_MESSAGE":
                    sender = str(frame.get("sender_login", ""))
                    payload = str(frame.get("payload", ""))
                    ts = float(frame.get("timestamp", time.time()))
                    if self.on_message and sender:
                        self.on_message(sender, f"[MP] {payload}", ts)

                else:
                    pass

        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._connected = False
            if self.on_disconnect:
                self.on_disconnect("connection lost")

    async def _watchdog_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if time.time() - self._last_server_frame > HEARTBEAT_TIMEOUT:
                    logger.debug("watchdog timeout, disconnecting")
                    self._connected = False
                    if self.on_disconnect:
                        self.on_disconnect("heartbeat timeout")
                    break
        except asyncio.CancelledError:
            pass
