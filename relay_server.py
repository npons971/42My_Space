#!/usr/bin/env python3
"""
42msg relay server — WebSocket relay for isolated networks.

Hosts channels in memory and forwards messages between connected clients.
Deploy on Render (free) or any platform with WebSocket support.

Usage:
    pip install websockets
    python relay_server.py              # default port 8765
    PORT=10000 python relay_server.py   # custom port
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

try:
    import websockets
    from websockets.asyncio.server import serve, ServerConnection
except ImportError:
    # Fallback for websockets < 13
    try:
        import websockets  # type: ignore[no-redef]
        from websockets.server import serve  # type: ignore[assignment]
        ServerConnection = Any  # type: ignore[assignment,misc]
    except ImportError:
        print("ERROR: pip install websockets")
        raise SystemExit(1)

logger = logging.getLogger("42msg-relay")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

PORT = int(os.environ.get("PORT", 8765))
LOGIN_RE = re.compile(r"^[a-zA-Z0-9_-]{1,20}$")
RATE_LIMIT_WINDOW = 10.0
RATE_LIMIT_MAX = 10


# ═══════════════════════════════════════════════════════════════════════════
#  State
# ═══════════════════════════════════════════════════════════════════════════

class ClientState:
    __slots__ = ("ws", "login", "channel_name")

    def __init__(self, ws: Any, login: str) -> None:
        self.ws = ws
        self.login = login
        self.channel_name: str | None = None


class ChannelState:
    __slots__ = (
        "name", "owner_login", "password", "max_users", "is_public",
        "members", "banned", "rate_limits", "created_at",
    )

    def __init__(
        self, name: str, owner_login: str, password: str,
        max_users: int, is_public: bool,
    ) -> None:
        self.name = name
        self.owner_login = owner_login
        self.password = password
        self.max_users = max_users
        self.is_public = is_public
        self.members: dict[str, ClientState] = {}
        self.banned: set[str] = set()
        self.rate_limits: dict[str, list[float]] = {}
        self.created_at = time.time()


_clients: dict[Any, ClientState] = {}
_logins: dict[str, ClientState] = {}
_channels: dict[str, ChannelState] = {}


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

async def _send(ws: Any, frame: dict[str, Any]) -> None:
    try:
        await ws.send(json.dumps(frame, separators=(",", ":")))
    except Exception:
        pass


async def _broadcast(
    channel: ChannelState, frame: dict[str, Any], exclude: str | None = None,
) -> None:
    for login, client in list(channel.members.items()):
        if login != exclude:
            await _send(client.ws, frame)


def _check_rate(channel: ChannelState, login: str) -> bool:
    now = time.time()
    ts = channel.rate_limits.get(login, [])
    ts = [t for t in ts if now - t < RATE_LIMIT_WINDOW]
    channel.rate_limits[login] = ts
    if len(ts) >= RATE_LIMIT_MAX:
        return False
    ts.append(now)
    return True


async def _remove_from_channel(client: ClientState) -> None:
    if not client.channel_name:
        return
    ch = _channels.get(client.channel_name)
    if not ch:
        client.channel_name = None
        return

    was_member = client.login in ch.members
    ch.members.pop(client.login, None)
    ch.rate_limits.pop(client.login, None)
    client.channel_name = None

    if client.login == ch.owner_login:
        await _broadcast(ch, {"type": "CHANNEL_CLOSED", "reason": "host disconnected"})
        for member in list(ch.members.values()):
            member.channel_name = None
        del _channels[ch.name]
        logger.info("channel '%s' closed (owner left)", ch.name)
    elif was_member:
        await _broadcast(ch, {"type": "USER_LEFT", "login": client.login})


async def _cleanup(client: ClientState) -> None:
    await _remove_from_channel(client)
    _logins.pop(client.login, None)
    _clients.pop(client.ws, None)
    logger.info("%s disconnected (%d online)", client.login, len(_clients))


# ═══════════════════════════════════════════════════════════════════════════
#  Frame handlers
# ═══════════════════════════════════════════════════════════════════════════

async def _handle_list(client: ClientState, frame: dict) -> None:
    ch_list = []
    for ch in _channels.values():
        ch_list.append({
            "name": ch.name,
            "owner_login": ch.owner_login,
            "is_public": ch.is_public,
            "user_count": len(ch.members),
            "max_users": ch.max_users,
        })
    await _send(client.ws, {"type": "CHANNEL_LIST", "channels": ch_list})


async def _handle_create(client: ClientState, frame: dict) -> None:
    if client.channel_name:
        await _send(client.ws, {"type": "ERROR", "reason": "already_in_channel"})
        return

    name = str(frame.get("name", "")).strip()
    if not name or len(name) > 30:
        await _send(client.ws, {"type": "ERROR", "reason": "invalid channel name"})
        return
    if name in _channels:
        await _send(client.ws, {"type": "ERROR", "reason": "channel name already taken"})
        return

    password = str(frame.get("password", ""))
    max_users = min(max(int(frame.get("max_users", 10)), 2), 50)
    is_public = bool(frame.get("is_public", password == ""))

    ch = ChannelState(name, client.login, password, max_users, is_public)
    ch.members[client.login] = client
    _channels[name] = ch
    client.channel_name = name

    await _send(client.ws, {
        "type": "CHANNEL_CREATED",
        "channel_name": name,
        "members": [client.login],
    })
    logger.info("%s created channel '%s' (%d max, %s)",
                client.login, name, max_users, "public" if is_public else "private")


async def _handle_join(client: ClientState, frame: dict) -> None:
    if client.channel_name:
        await _send(client.ws, {"type": "JOIN_REJECTED", "reason": "already in a channel"})
        return

    channel_name = str(frame.get("channel", "")).strip()
    password = str(frame.get("password", ""))

    ch = _channels.get(channel_name)
    if not ch:
        await _send(client.ws, {"type": "JOIN_REJECTED", "reason": "channel not found"})
        return

    if client.login in ch.banned:
        await _send(client.ws, {"type": "JOIN_REJECTED", "reason": "banned"})
        return

    if client.login in ch.members:
        await _send(client.ws, {"type": "JOIN_REJECTED", "reason": "login already in channel"})
        return

    if not ch.is_public and password != ch.password:
        await _send(client.ws, {"type": "JOIN_REJECTED", "reason": "wrong password"})
        return

    if len(ch.members) >= ch.max_users:
        await _send(client.ws, {"type": "JOIN_REJECTED", "reason": "channel full"})
        return

    ch.members[client.login] = client
    client.channel_name = channel_name

    members = list(ch.members.keys())
    await _send(client.ws, {
        "type": "JOIN_ACCEPTED",
        "channel_name": channel_name,
        "members": members,
    })
    await _broadcast(ch, {"type": "USER_JOINED", "login": client.login},
                     exclude=client.login)
    logger.info("%s joined '%s' (%d/%d)",
                client.login, channel_name, len(ch.members), ch.max_users)


async def _handle_leave(client: ClientState, frame: dict) -> None:
    if not client.channel_name:
        await _send(client.ws, {"type": "LEFT"})
        return
    await _remove_from_channel(client)
    await _send(client.ws, {"type": "LEFT"})


async def _handle_message(client: ClientState, frame: dict) -> None:
    if not client.channel_name:
        return
    ch = _channels.get(client.channel_name)
    if not ch:
        return
    if not _check_rate(ch, client.login):
        await _send(client.ws, {
            "type": "RATE_LIMITED", "retry_after": int(RATE_LIMIT_WINDOW),
        })
        return

    payload = str(frame.get("payload", ""))
    now = time.time()
    await _broadcast(ch, {
        "type": "MESSAGE",
        "sender_login": client.login,
        "payload": payload,
        "timestamp": now,
    }, exclude=client.login)


async def _handle_pm(client: ClientState, frame: dict) -> None:
    if not client.channel_name:
        return
    ch = _channels.get(client.channel_name)
    if not ch:
        return
    target_login = str(frame.get("target_login", ""))
    target = ch.members.get(target_login)
    if not target:
        await _send(client.ws, {"type": "ERROR", "reason": f"{target_login} not found"})
        return
    await _send(target.ws, {
        "type": "PRIVATE_MESSAGE",
        "sender_login": client.login,
        "payload": str(frame.get("payload", "")),
        "timestamp": time.time(),
    })


async def _handle_kick(client: ClientState, frame: dict) -> None:
    if not client.channel_name:
        return
    ch = _channels.get(client.channel_name)
    if not ch or ch.owner_login != client.login:
        await _send(client.ws, {"type": "ERROR", "reason": "not the host"})
        return
    target_login = str(frame.get("target_login", ""))
    target = ch.members.get(target_login)
    if not target:
        await _send(client.ws, {"type": "ERROR", "reason": f"{target_login} not found"})
        return
    if target_login == client.login:
        await _send(client.ws, {"type": "ERROR", "reason": "cannot kick yourself"})
        return
    await _send(target.ws, {"type": "KICKED", "reason": "kicked by host"})
    await _remove_from_channel(target)
    logger.info("%s kicked %s from '%s'", client.login, target_login, ch.name)


async def _handle_ban(client: ClientState, frame: dict) -> None:
    if not client.channel_name:
        return
    ch = _channels.get(client.channel_name)
    if not ch or ch.owner_login != client.login:
        await _send(client.ws, {"type": "ERROR", "reason": "not the host"})
        return
    target_login = str(frame.get("target_login", ""))
    if target_login == client.login:
        await _send(client.ws, {"type": "ERROR", "reason": "cannot ban yourself"})
        return
    ch.banned.add(target_login)
    target = ch.members.get(target_login)
    if target:
        await _send(target.ws, {"type": "KICKED", "reason": "banned by host"})
        await _remove_from_channel(target)
    logger.info("%s banned %s from '%s'", client.login, target_login, ch.name)


_HANDLERS = {
    "LIST_CHANNELS": _handle_list,
    "CREATE_CHANNEL": _handle_create,
    "JOIN": _handle_join,
    "LEAVE": _handle_leave,
    "MESSAGE": _handle_message,
    "PRIVATE_MESSAGE": _handle_pm,
    "KICK": _handle_kick,
    "BAN": _handle_ban,
}


# ═══════════════════════════════════════════════════════════════════════════
#  WebSocket handler
# ═══════════════════════════════════════════════════════════════════════════

async def handler(websocket: Any) -> None:
    client: ClientState | None = None
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=10.0)
        frame = json.loads(raw)

        if frame.get("type") != "REGISTER":
            await _send(websocket, {"type": "ERROR", "reason": "expected REGISTER"})
            return

        login = str(frame.get("login", "")).strip()
        if not LOGIN_RE.match(login):
            await _send(websocket, {
                "type": "ERROR",
                "reason": "invalid login (1-20 chars, alphanumeric/_/-)",
            })
            return

        if login in _logins:
            await _send(websocket, {
                "type": "ERROR", "reason": "login already connected",
            })
            return

        client = ClientState(websocket, login)
        _clients[websocket] = client
        _logins[login] = client

        await _send(websocket, {"type": "REGISTERED", "login": login})
        logger.info("%s connected (%d online)", login, len(_clients))

        async for raw in websocket:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue

            ftype = frame.get("type", "")
            handler_fn = _HANDLERS.get(ftype)
            if handler_fn:
                await handler_fn(client, frame)
            elif ftype == "PONG":
                pass
            else:
                await _send(client.ws, {
                    "type": "ERROR", "reason": f"unknown type: {ftype}",
                })

    except (asyncio.TimeoutError, json.JSONDecodeError):
        pass
    except Exception:
        logger.exception("handler error")
    finally:
        if client:
            await _cleanup(client)


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

async def main() -> None:
    logger.info("starting relay on port %d", PORT)
    try:
        async with serve(handler, "0.0.0.0", PORT,
                         ping_interval=20, ping_timeout=60):
            logger.info("relay ready — ws://0.0.0.0:%d", PORT)
            await asyncio.Future()
    except OSError as exc:
        logger.error("cannot bind: %s", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("relay stopped")
