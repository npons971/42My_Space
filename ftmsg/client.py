from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from .channel import ChannelClient, ChannelServer
from .crypto import (
    decrypt,
    encrypt,
    encrypt_symmetric,
    decrypt_symmetric,
    generate_or_load_encryption_keypair,
    generate_room_key,
)
from .discovery import BroadcastDiscovery, ChannelInfo, DiscoveredChannel
from .security import generate_or_load_signing_keypair
from .store import MessageStore
from .trust import TrustStore


class FTMessageClient:
    def __init__(self, login: str, db_path: Path | None = None) -> None:
        self.login = login
        self.db_path = db_path or (Path.home() / ".42msg" / "messages.db")

        (
            self.enc_private_key,
            self.enc_public_key,
        ) = generate_or_load_encryption_keypair()
        (
            self.sign_private_key,
            self.sign_public_key,
        ) = generate_or_load_signing_keypair()

        self.store = MessageStore(self.db_path)
        self.trust = TrustStore(self.db_path)
        self.trust = TrustStore(self.db_path)
        self.local_ip = os.environ.get("FTMSG_LOCAL_IP")
        self.relay_url = os.environ.get("FTMSG_RELAY_URL")

        self.discovery: BroadcastDiscovery | None = None
        self.channel_server: ChannelServer | None = None
        self.channel_client: ChannelClient | None = None
        self.ws: Any | None = None
        self._relay_task: asyncio.Task | None = None
        self._relay_channels: list[DiscoveredChannel] = []
        self._relay_current_channel: str | None = None
        self._relay_members: list[str] = []
        self._relay_rate_limits: list[float] = []
        self._pending_join: asyncio.Future | None = None
        self._pending_create: asyncio.Future | None = None

        self.typing_users: dict[str, float] = {}
        self.last_typing_sent: float = 0

        self.is_hosting = False
        self.room_key: bytes | None = None

        self.incoming_queue: asyncio.Queue[tuple[str, str, float]] = asyncio.Queue(maxsize=1000)
        self.events_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.store.init()
        await self.trust.init()

        if self.relay_url:
            await self._connect_relay()
        else:
            self.discovery = BroadcastDiscovery(
                on_channel=self._on_channel_discovered_sync,
            )
            self.discovery.local_ip = self._resolve_local_ip()
            try:
                await asyncio.to_thread(self.discovery.start)
            except RuntimeError as exc:
                await self.events_queue.put(f"⚠️ Découverte réseau indisponible: {exc}")
        await self.events_queue.put("Client prêt. Tape /help pour les commandes.")

    async def stop(self) -> None:
        await self.leave_channel()
        if self.ws:
            await self.ws.close()
            self.ws = None
        if self._relay_task:
            self._relay_task.cancel()
        if self.discovery:
            await asyncio.to_thread(self.discovery.stop)

    async def _connect_relay(self) -> None:
        try:
            import websockets
        except ImportError:
            await self.events_queue.put("⚠️ websockets non installé, mode relais inactif.")
            return

        try:
            self.ws = await websockets.connect(self.relay_url)
            await self.ws.send(json.dumps({"type": "REGISTER", "login": self.login}))
            raw = await self.ws.recv()
            resp = json.loads(raw)
            if resp.get("type") == "REGISTERED":
                await self.events_queue.put(f"Connecté au relais {self.relay_url}")
                self._relay_task = asyncio.create_task(self._relay_loop())
                await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))
            else:
                await self.events_queue.put(f"⚠️ Relais refusé: {resp.get('reason')}")
        except Exception as e:
            await self.events_queue.put(f"⚠️ Erreur relais: {e}")

    async def _relay_loop(self) -> None:
        try:
            async for raw in self.ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                ftype = frame.get("type")

                if ftype == "CHANNEL_LIST":
                    self._relay_channels = []
                    for c in frame.get("channels", []):
                        self._relay_channels.append(DiscoveredChannel(
                            name=c["name"], host_ip="relay", host_port=0,
                            is_public=c["is_public"], user_count=c["user_count"],
                            max_users=c["max_users"], last_seen=time.time(),
                        ))

                elif ftype == "MESSAGE":
                    sender = frame.get("sender_login", "")
                    payload = frame.get("payload", "")
                    ts = frame.get("timestamp", time.time())
                    decrypted = payload
                    if self.room_key and payload.startswith("ENCRYPTED:"):
                        try:
                            decrypted = decrypt_symmetric(payload[len("ENCRYPTED:"):], self.room_key)
                        except Exception:
                            decrypted = "[déchiffrement échoué]"
                    await self.incoming_queue.put((sender, decrypted, ts))
                    if self._relay_current_channel:
                        await self.store.add_channel_message(self._relay_current_channel, sender, decrypted, ts)

                elif ftype == "PRIVATE_MESSAGE":
                    sender = frame.get("sender_login", "")
                    payload = frame.get("payload", "")
                    ts = frame.get("timestamp", time.time())
                    await self.incoming_queue.put((sender, f"[MP←{sender}] {payload}", ts))

                elif ftype == "USER_JOINED":
                    login = frame.get("login")
                    if login not in self._relay_members:
                        self._relay_members.append(login)
                    await self.events_queue.put(f"{login} a rejoint le salon")
                    # Send room key if we are host (simple version: we don't handle pubkeys via relay yet)

                elif ftype == "USER_LEFT":
                    login = frame.get("login")
                    if login in self._relay_members:
                        self._relay_members.remove(login)
                    await self._on_member_leave(login)

                elif ftype == "TYPING":
                    sender = str(frame.get("login", ""))
                    if sender:
                        self._on_typing(sender)
                elif ftype in ("CHANNEL_CLOSED", "LEFT", "KICKED"):
                    if ftype == "CHANNEL_CLOSED":
                        await self.events_queue.put(f"Salon fermé: {frame.get('reason', '')}")
                    elif ftype == "LEFT":
                        await self.events_queue.put("Salon quitté")
                    elif ftype == "KICKED":
                        await self.events_queue.put(f"Expulsé: {frame.get('reason', '')}")
                    self.is_hosting = False
                    self.room_key = None
                    self._relay_current_channel = None
                    self._relay_members = []
                    await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))

                elif ftype == "JOIN_ACCEPTED":
                    self._relay_current_channel = frame.get("channel_name", "")
                    self._relay_members = frame.get("members", [])
                    if self._pending_join and not self._pending_join.done():
                        self._pending_join.set_result(("connected", self._relay_current_channel))

                elif ftype == "JOIN_REJECTED":
                    if self._pending_join and not self._pending_join.done():
                        self._pending_join.set_result(("rejected", frame.get("reason", "")))

                elif ftype == "CHANNEL_CREATED":
                    self._relay_current_channel = frame.get("channel_name", "")
                    self._relay_members = frame.get("members", [])
                    self.is_hosting = True
                    if self._pending_create and not self._pending_create.done():
                        self._pending_create.set_result("created")

                elif ftype == "ERROR":
                    reason = frame.get("reason", "")
                    await self.events_queue.put(f"⚠️ Erreur relais: {reason}")
                    if self._pending_create and not self._pending_create.done():
                        self._pending_create.set_result(f"error: {reason}")
                    if self._pending_join and not self._pending_join.done():
                        self._pending_join.set_result(("error", reason))

        except Exception as e:
            await self.events_queue.put(f"⚠️ Déconnecté du relais: {e}")
            self.ws = None

    def _resolve_local_ip(self) -> str:
        if self.local_ip:
            return self.local_ip
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.0)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    def _on_msg_cb(self, channel_name: str) -> Callable[[str, str, float], None]:
        def on_msg(sender: str, payload: str, ts: float) -> None:
            if self._loop:
                # Decrypt if room_key available and payload looks encrypted
                decrypted = payload
                if self.room_key and payload.startswith("ENCRYPTED:"):
                    try:
                        ciphertext = payload[len("ENCRYPTED:"):]
                        decrypted = decrypt_symmetric(ciphertext, self.room_key)
                    except Exception:
                        decrypted = "[déchiffrement échoué]"
                asyncio.run_coroutine_threadsafe(
                    self.incoming_queue.put((sender, decrypted, ts)),
                    self._loop,
                )
                asyncio.run_coroutine_threadsafe(
                    self.store.add_channel_message(channel_name, sender, decrypted, ts),
                    self._loop,
                )
        return on_msg

    def _on_member_leave(self, login: str) -> None:
        if login in self.typing_users:
            del self.typing_users[login]
        self.events_queue.put_nowait(f"{login} a quitté le salon")

    def _on_typing(self, sender: str) -> None:
        self.typing_users[sender] = time.time()

    def _on_disconnect(self, reason: str) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.events_queue.put(f"Déconnecté du salon: {reason}"),
                self._loop,
            )
        self.channel_client = None

    def _encrypt_and_send_room_key(self, login: str, pubkey_b64: str) -> None:
        if not self.room_key or not self.channel_server:
            return
        try:
            encrypted_key = encrypt(self.room_key, pubkey_b64)
            frame = {
                "type": "ROOM_KEY",
                "target_login": login,
                "encrypted_key": encrypted_key,
            }
            asyncio.run_coroutine_threadsafe(
                self.channel_server.send_to(login, frame),
                self._loop,
            )
        except Exception:
            logger.debug("room key encryption failed for %s", login, exc_info=True)

    async def create_channel(
        self, name: str, password: str, max_users: int, is_public: bool,
    ) -> str:
        if self.channel_server or self.channel_client or self._relay_current_channel:
            return "already_in_channel"

        self.room_key = generate_room_key()

        if self.ws:
            self.room_key = None  # Désactive le chiffrement de bout en bout en mode relais
            self._pending_create = asyncio.Future()
            await self.ws.send(json.dumps({
                "type": "CREATE_CHANNEL",
                "name": name,
                "password": password,
                "max_users": max_users,
                "is_public": is_public,
            }))
            status = await self._pending_create
            if status == "created":
                await self.events_queue.put(f"Salon '{name}' créé sur le relais (max {max_users}, {'public' if is_public else 'privé'})")
                await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))
            return status

        local_ip = self._resolve_local_ip()
        self.room_key = generate_room_key()

        def _update_beacon() -> None:
            if self.discovery and self.channel_server:
                info = ChannelInfo(
                    name=name,
                    host_ip=local_ip,
                    host_port=port,
                    is_public=is_public,
                    user_count=self.channel_server.member_count(),
                    max_users=max_users,
                )
                self.discovery.update_beacon(info)

        def on_join(login: str) -> None:
            _update_beacon()
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.events_queue.put(f"{login} a rejoint le salon"),
                    self._loop,
                )
            # Send room key if we know the member's pubkey
            if self.channel_server and login in self.channel_server._member_keys:
                self._encrypt_and_send_room_key(login, self.channel_server._member_keys[login])

        self.channel_server = ChannelServer(
            name, password, max_users, is_public, self.login,
            on_message=self._on_msg_cb(name),
            on_member_join=on_join,
            on_member_leave=self._on_member_leave,
            on_typing=self._on_typing,
        )

        port = await self.channel_server.start(port=0)
        self.is_hosting = True

        info = ChannelInfo(
            name=name,
            host_ip=local_ip,
            host_port=port,
            is_public=is_public,
            user_count=1,
            max_users=max_users,
        )
        if self.discovery:
            self.discovery.start_beaconing(info)
            _update_beacon()

        await self.events_queue.put(
            f"Salon '{name}' créé sur {local_ip}:{port} "
            f"(max {max_users}, {'public' if is_public else 'privé'})",
        )
        return "created"

    async def join_channel(
        self, host_ip: str, host_port: int, password: str,
    ) -> tuple[str, str]:
        if self.channel_server or self.channel_client or self._relay_current_channel:
            return "already_in_channel", ""

        if self.ws:
            # En mode relais, host_ip est utilisé comme nom de salon pour la compatibilité
            self._pending_join = asyncio.Future()
            await self.ws.send(json.dumps({
                "type": "JOIN",
                "channel": host_ip,
                "password": password,
            }))
            status, detail = await self._pending_join
            if status == "connected":
                # Load history
                history = await self.store.list_channel_messages(detail, limit=20)
                for sender, payload, ts in history:
                    await self.incoming_queue.put((sender, payload, ts))
                await self.events_queue.put(f"Connecté au salon '{detail}' via le relais")
                await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))
            return status, detail

        def on_join(login: str) -> None:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.events_queue.put(f"{login} a rejoint le salon"),
                    self._loop,
                )

        def on_room_key(encrypted_key: str) -> None:
            try:
                raw = decrypt(encrypted_key, self.enc_private_key)
                self.room_key = raw
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self.events_queue.put("Clé de salon reçue — messages chiffrés"),
                        self._loop,
                    )
            except Exception:
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self.events_queue.put("⚠️ Impossible de déchiffrer la clé de salon"),
                        self._loop,
                    )

        self.channel_client = ChannelClient(
            login=self.login,
            on_member_join=on_join,
            on_member_leave=self._on_member_leave,
            on_disconnect=self._on_disconnect,
            on_room_key=on_room_key,
            on_typing=self._on_typing,
        )

        my_pubkey = base64.b64encode(bytes(self.enc_public_key)).decode("utf-8")
        status, detail = await self.channel_client.connect(
            host_ip, host_port, password, self.login,
            encryption_pubkey_b64=my_pubkey,
        )

        if status != "connected":
            self.channel_client = None
            return status, detail

        channel_name = self.channel_client.channel_name
        self.channel_client.on_message = self._on_msg_cb(channel_name)

        # Load history
        history = await self.store.list_channel_messages(channel_name, limit=20)
        for sender, payload, ts in history:
            await self.incoming_queue.put((sender, payload, ts))

        await self.events_queue.put(
            f"Connecté au salon '{detail}' ({host_ip}:{host_port})",
        )
        return "connected", detail

    async def leave_channel(self) -> None:
        self.room_key = None
        if self.ws and self._relay_current_channel:
            await self.ws.send(json.dumps({"type": "LEAVE"}))
            return

        if self.channel_client:
            await self.channel_client.disconnect()
            self.channel_client = None
            self.is_hosting = False
            await self.events_queue.put("Salon quitté")

        if self.channel_server:
            if self.discovery:
                self.discovery._stop_beaconing()
            await self.channel_server.stop()
            self.channel_server = None
            self.is_hosting = False
            await self.events_queue.put("Salon fermé")

    def _check_relay_rate(self) -> bool:
        now = time.time()
        self._relay_rate_limits = [ts for ts in self._relay_rate_limits if now - ts < 10.0]
        if len(self._relay_rate_limits) >= 10:
            return False
        self._relay_rate_limits.append(now)
        return True

    def get_typing_users(self) -> list[str]:
        now = time.time()
        self.typing_users = {u: t for u, t in self.typing_users.items() if now - t < 3.0}
        return list(self.typing_users.keys())

    async def send_typing_indicator(self) -> None:
        now = time.time()
        if now - self.last_typing_sent < 3.0:
            return
        self.last_typing_sent = now
        
        if self.ws and self._relay_current_channel:
            try:
                await self.ws.send(json.dumps({"type": "TYPING"}))
            except Exception:
                pass
        elif self.channel_client:
            await self.channel_client.send_typing()
        elif self.channel_server:
            await self.channel_server.broadcast({"type": "TYPING", "login": self.login}, exclude=self.login)

    async def send_channel_message(self, message: str) -> str:
        if self.ws and self._relay_current_channel:
            if not self._check_relay_rate():
                return "rate_limited"
            payload = message
            if self.room_key:
                try:
                    payload = "ENCRYPTED:" + encrypt_symmetric(message, self.room_key)
                except Exception:
                    pass
            await self.ws.send(json.dumps({
                "type": "MESSAGE",
                "payload": payload,
            }))
            await self.incoming_queue.put((self.login, message, time.time()))
            await self.store.add_channel_message(self._relay_current_channel, self.login, message, time.time())
            return "sent"

        if self.channel_server:
            ok = await self.channel_server.local_message(self.login, message)
            if ok:
                return "sent"
            return "rate_limited"

        if self.channel_client and self.channel_client._connected:
            ok = await self.channel_client.send_message(message)
            if ok:
                await self.incoming_queue.put((self.login, message, time.time()))
                return "sent"
            return "disconnected"

        return "not_in_channel"

    async def kick_member(self, login: str) -> str:
        if self.ws and self._relay_current_channel and self.is_hosting:
            await self.ws.send(json.dumps({"type": "KICK", "target_login": login}))
            return "kicked"
        if not self.channel_server:
            return "not_hosting"
        ok = await self.channel_server.kick(login)
        return "kicked" if ok else "not_found"

    async def ban_member(self, login: str) -> str:
        if self.ws and self._relay_current_channel and self.is_hosting:
            await self.ws.send(json.dumps({"type": "BAN", "target_login": login}))
            return "banned"
        if not self.channel_server:
            return "not_hosting"
        ok = await self.channel_server.ban(login)
        return "banned" if ok else "not_found"

    async def send_private_message(self, target: str, message: str) -> str:
        if self.ws and self._relay_current_channel:
            await self.ws.send(json.dumps({
                "type": "PRIVATE_MESSAGE",
                "target_login": target,
                "payload": message,
            }))
            await self.incoming_queue.put((self.login, f"[MP→{target}] {message}", time.time()))
            return "sent"

        if self.channel_server:
            frame = {
                "type": "PRIVATE_MESSAGE",
                "sender_login": self.login,
                "target_login": target,
                "payload": message,
                "timestamp": time.time(),
            }
            ok = await self.channel_server.send_to(target, frame)
            if ok:
                await self.incoming_queue.put((self.login, f"[MP→{target}] {message}", time.time()))
                return "sent"
            return "not_found"

        if self.channel_client and self.channel_client._connected:
            frame = {
                "type": "PRIVATE_MESSAGE",
                "sender_login": self.login,
                "target_login": target,
                "payload": message,
                "timestamp": time.time(),
            }
            try:
                from .protocol import write_frame
                await write_frame(self.channel_client.writer, frame)
                await self.incoming_queue.put((self.login, f"[MP→{target}] {message}", time.time()))
                return "sent"
            except Exception:
                return "disconnected"

        return "not_in_channel"

    def list_channels(self) -> list[DiscoveredChannel]:
        if self.ws:
            return self._relay_channels
        if self.discovery:
            return self.discovery.get_channels()
        return []

    def list_members(self) -> list[str]:
        if self.ws and self._relay_current_channel:
            return self._relay_members
        if self.channel_client:
            return self.channel_client.members
        if self.channel_server:
            return self.channel_server.member_logins()
        return []

    def current_channel_name(self) -> str:
        if self.ws and self._relay_current_channel:
            return self._relay_current_channel
        if self.channel_client:
            return self.channel_client.channel_name
        if self.channel_server:
            return self.channel_server.name
        return ""

    def _on_channel_discovered_sync(self, ch: DiscoveredChannel) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.events_queue.put(
                    f"Nouveau salon détecté: {ch.name} "
                    f"({ch.user_count}/{ch.max_users})",
                ),
                self._loop,
            )


def default_login() -> str:
    return os.environ.get("USER", "unknown")
