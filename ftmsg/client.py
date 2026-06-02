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
from .games.base import GameInvite, BaseGameSession, get_game, list_games, list_multiplayer_games
from .profile import ProfileManager
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
        self.profile = ProfileManager(login)
        self.local_ip = os.environ.get("FTMSG_LOCAL_IP")
        self.relay_url = os.environ.get("FTMSG_RELAY_URL")

        self.discovery: BroadcastDiscovery | None = None
        self.channel_server: ChannelServer | None = None
        self.channel_client: ChannelClient | None = None
        self.ws: Any | None = None
        self._relay_task: asyncio.Task | None = None
        self._relay_keepalive_task: asyncio.Task | None = None
        self._relay_channels: list[DiscoveredChannel] = []
        self._relay_current_channel: str | None = None
        self._relay_members: list[str] = []
        self._relay_member_keys: dict[str, str] = {}
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

        # Game state
        self.active_invites: dict[str, GameInvite] = {}
        self.current_game_session: BaseGameSession | None = None
        self.current_game_invite: GameInvite | None = None
        self.on_game_state_change: Callable[[dict[str, Any]], None] | None = None
        self.on_game_invite: Callable[[GameInvite], None] | None = None
        self.on_game_end: Callable[[str | None], None] | None = None

        # Score/leaderboard state
        self._score_responses: dict[str, dict[str, Any]] = {}
        self._score_request_id: str = ""
        self._score_request_game_id: str = ""

        # Profile state
        self._profile_responses: dict[str, dict[str, Any]] = {}
        self._profile_request_id: str = ""

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
        if self._relay_keepalive_task:
            self._relay_keepalive_task.cancel()
            try:
                await self._relay_keepalive_task
            except asyncio.CancelledError:
                pass
            self._relay_keepalive_task = None
        if self._relay_task:
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass
            self._relay_task = None
        if self.ws:
            await self.ws.close()
            self.ws = None
        if self.discovery:
            await asyncio.to_thread(self.discovery.stop)

    async def _connect_relay(self) -> None:
        try:
            import websockets
        except ImportError:
            await self.events_queue.put("⚠️ websockets non installé, mode relais inactif.")
            return

        last_error: Exception | None = None
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                self.ws = await websockets.connect(self.relay_url, ping_interval=25, ping_timeout=25)
                await self.ws.send(json.dumps({"type": "REGISTER", "login": self.login}))
                raw = await self.ws.recv()
                resp = json.loads(raw)
                if resp.get("type") == "REGISTERED":
                    await self.events_queue.put(f"Connecté au relais {self.relay_url}")
                    self._relay_task = asyncio.create_task(self._relay_loop())
                    await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))
                    return
                else:
                    await self.events_queue.put(f"⚠️ Relais refusé: {resp.get('reason')}")
                    return
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await self.events_queue.put(
                        f"⚠️ Relais indisponible (tentative {attempt}/{max_retries}), nouvelle tentative dans 10s..."
                    )
                    await asyncio.sleep(10)
                else:
                    break

        await self.events_queue.put(
            f"⚠️ Erreur relais: {last_error}"
        )

    async def _relay_loop(self) -> None:
        if self.ws is None:
            return
        try:
            async for raw in self.ws:
                if self.ws is None:
                    break
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
                            campus_only=c.get("campus_only", False),
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

                elif ftype == "PUBLIC_KEY":
                    login = frame.get("login")
                    pubkey_b64 = frame.get("encryption_pubkey_b64", "")
                    if login and pubkey_b64:
                        self._relay_member_keys[login] = pubkey_b64
                        if self.is_hosting and self.room_key and login != self.login:
                            try:
                                encrypted_key = encrypt(self.room_key, pubkey_b64)
                                await self.ws.send(json.dumps({
                                    "type": "ROOM_KEY",
                                    "target_login": login,
                                    "encrypted_key": encrypted_key,
                                }))
                            except Exception:
                                logger.debug("room key encryption failed for %s", login, exc_info=True)

                elif ftype == "ROOM_KEY":
                    target = frame.get("target_login", "")
                    if target == self.login:
                        encrypted_key = frame.get("encrypted_key", "")
                        try:
                            raw = decrypt(encrypted_key, self.enc_private_key)
                            self.room_key = raw
                            await self.events_queue.put("Clé de salon reçue — messages chiffrés (mode relais)")
                        except Exception:
                            await self.events_queue.put("⚠️ Impossible de déchiffrer la clé de salon (mode relais)")

                elif ftype == "USER_LEFT":
                    login = frame.get("login")
                    if login in self._relay_members:
                        self._relay_members.remove(login)
                    await self._on_member_leave(login)

                elif ftype == "TYPING":
                    sender = str(frame.get("login", ""))
                    if sender:
                        self._on_typing(sender)
                elif ftype.startswith(("GAME_", "SCORE_")):
                    if self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self._handle_game_frame(frame),
                            self._loop,
                        )
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
                    self._relay_member_keys.clear()
                    if self.ws:
                        try:
                            await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))
                        except Exception:
                            pass

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

        except asyncio.CancelledError:
            # Task cancelled by stop() — don't reconnect
            raise
        except Exception as e:
            msg = str(e)
            # ConnectionClosed without close frame is common on idle/free-tier hosts
            if "no close frame" in msg.lower() or "connection closed" in msg.lower():
                await self.events_queue.put("Relais déconnecté (temps d'inactivité). Reconnexion...")
            else:
                await self.events_queue.put(f"⚠️ Déconnecté du relais: {e}")
            # auto-reconnect
            if getattr(self, "relay_url", None) and self._loop:
                ws = self.ws
                self.ws = None
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                await asyncio.sleep(5)
                if getattr(self, "relay_url", None):
                    await self._connect_relay()
                return
        finally:
            ws = self.ws
            self.ws = None
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass

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
        self, name: str, password: str, max_users: int, is_public: bool, campus_only: bool = False,
    ) -> str:
        if self.channel_server or self.channel_client or self._relay_current_channel:
            return "already_in_channel"

        self.room_key = generate_room_key()

        if self.ws:
            self._pending_create = asyncio.Future()
            await self.ws.send(json.dumps({
                "type": "CREATE_CHANNEL",
                "name": name,
                "password": password,
                "max_users": max_users,
                "is_public": is_public,
                "campus_only": campus_only,
            }))
            status = await self._pending_create
            if status == "created":
                await self.events_queue.put(f"Salon '{name}' créé sur le relais (max {max_users}, {'public' if is_public else 'privé'})")
                await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))
            return status

        local_ip = self._resolve_local_ip()
        self.room_key = generate_room_key()

        allowed_network = None
        if campus_only:
            try:
                import ipaddress
                allowed_network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
            except Exception:
                logger.debug("failed to compute allowed network", exc_info=True)

        def _update_beacon() -> None:
            if self.discovery and self.channel_server:
                info = ChannelInfo(
                    name=name,
                    host_ip=local_ip,
                    host_port=port,
                    is_public=is_public,
                    user_count=self.channel_server.member_count(),
                    max_users=max_users,
                    campus_only=campus_only,
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
            campus_only=campus_only,
            allowed_network=allowed_network,
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
            campus_only=campus_only,
        )
        if self.discovery:
            self.discovery.start_beaconing(info)
            _update_beacon()

        net_label = "campus" if campus_only else "public" if is_public else "privé"
        await self.events_queue.put(
            f"Salon '{name}' créé sur {local_ip}:{port} "
            f"(max {max_users}, {net_label})",
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
                await self.events_queue.put(f"Connecté au salon '{detail}' via le relais")
                await self.ws.send(json.dumps({"type": "LIST_CHANNELS"}))
                # Broadcast our public key so the host can send us the room key
                my_pubkey = base64.b64encode(bytes(self.enc_public_key)).decode("utf-8")
                try:
                    await self.ws.send(json.dumps({
                        "type": "PUBLIC_KEY",
                        "login": self.login,
                        "encryption_pubkey_b64": my_pubkey,
                    }))
                except Exception:
                    pass
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

        await self.events_queue.put(
            f"Connecté au salon '{detail}' ({host_ip}:{host_port})",
        )
        return "connected", detail

    async def leave_channel(self) -> None:
        self.room_key = None
        self._relay_member_keys.clear()
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


    # ------------------------------------------------------------------ #
    # Games
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Score & Profile
    # ------------------------------------------------------------------ #

    def _on_game_score(self, score_dict: dict[str, Any]) -> None:
        """Called when a solo/host game session ends and reports a score."""
        if not self.current_game_invite:
            return
        game_id = self.current_game_invite.game_id
        self._record_score_for_game(game_id, score_dict, is_host=True)

    def _record_score_for_game(self, game_id: str, score_dict: dict[str, Any], is_host: bool = False) -> None:
        if game_id == "snake":
            score = score_dict.get("score", 0)
            self.profile.record_score("snake", {"score": score, "best_score": score, "games_played": 1})
        elif game_id == "tictactoe":
            winner = score_dict.get("winner")
            draw = score_dict.get("draw", False)
            players = score_dict.get("players", [])
            if draw:
                self.profile.record_score("tictactoe", {"draws": 1, "games_played": 1})
            elif winner == self.login:
                self.profile.record_score("tictactoe", {"wins": 1, "games_played": 1})
            elif self.login in players:
                self.profile.record_score("tictactoe", {"losses": 1, "games_played": 1})
        elif game_id == "wordrace":
            raw_scores = score_dict.get("scores", {})
            winner = score_dict.get("winner")
            my_score = raw_scores.get(self.login, 0)
            rounds_won = sum(1 for p, s in raw_scores.items() if p == self.login)
            # We can't know exact rounds won from final state, use my_score as proxy for rounds won
            self.profile.record_score("wordrace", {
                "wins": 1 if winner == self.login else 0,
                "rounds_won": my_score,
                "games_played": 1,
            })

    def _record_multiplayer_score(self, invite: GameInvite | None, final_state: dict[str, Any], winner: str | None) -> None:
        if not invite:
            return
        game_id = invite.game_id
        if game_id == "tictactoe":
            players = final_state.get("players", invite.players)
            draw = winner is None and all(cell is not None for row in final_state.get("board", []) for cell in row)
            if draw:
                self.profile.record_score("tictactoe", {"draws": 1, "games_played": 1})
            elif winner == self.login:
                self.profile.record_score("tictactoe", {"wins": 1, "games_played": 1})
            elif self.login in players:
                self.profile.record_score("tictactoe", {"losses": 1, "games_played": 1})
        elif game_id == "wordrace":
            scores = final_state.get("scores", {})
            my_score = scores.get(self.login, 0)
            self.profile.record_score("wordrace", {
                "wins": 1 if winner == self.login else 0,
                "rounds_won": my_score,
                "games_played": 1,
            })

    # ------------------------------------------------------------------ #
    # Games
    # ------------------------------------------------------------------ #

    async def _handle_game_frame(self, frame: dict[str, Any]) -> None:
        ftype = frame.get("type")
        if ftype == "GAME_INVITE":
            invite = GameInvite.from_dict(frame)
            self.active_invites[invite.invite_id] = invite
            if self.on_game_invite:
                self.on_game_invite(invite)
            await self.events_queue.put(
                f"🎮 {invite.host_login} propose {invite.game_name} — tape /game_join {invite.invite_id}"
            )
        elif ftype == "GAME_JOIN":
            invite_id = frame.get("invite_id", "")
            login = frame.get("login", "")
            invite = self.active_invites.get(invite_id)
            if invite and login and login not in invite.players:
                invite.players.append(login)
            if self.current_game_invite and self.current_game_invite.invite_id == invite_id:
                self.current_game_invite.players = invite.players if invite else self.current_game_invite.players
            await self.events_queue.put(f"🎮 {login} a rejoint la partie {invite_id}")
        elif ftype == "GAME_LEAVE":
            invite_id = frame.get("invite_id", "")
            login = frame.get("login", "")
            invite = self.active_invites.get(invite_id)
            if invite and login in invite.players:
                invite.players.remove(login)
            await self.events_queue.put(f"🎮 {login} a quitté la partie {invite_id}")
        elif ftype == "GAME_STATE":
            if self.on_game_state_change:
                self.on_game_state_change(frame.get("state", {}))
        elif ftype == "GAME_ACTION":
            if self.current_game_session:
                player = frame.get("login", "")
                action = frame.get("action", "")
                data = frame.get("data", {})
                self.current_game_session.handle_action(player, action, data)
                # Host broadcasts updated state
                if self.is_hosting:
                    await self.broadcast_game_state()
        elif ftype == "GAME_END":
            winner = frame.get("winner")
            final_state = frame.get("final_state", {})
            if not self.is_hosting:
                self._record_multiplayer_score(self.current_game_invite, final_state, winner)
            if self.on_game_end:
                self.on_game_end(winner)
            await self.events_queue.put(
                f"🎮 Partie terminée — gagnant: {winner or 'aucun (égalité)' }"
            )
            self.current_game_session = None
            self.current_game_invite = None
        elif ftype.startswith("SCORE_"):
            await self._handle_score_frame(frame)
        elif ftype.startswith("PROFILE_"):
            await self._handle_profile_frame(frame)

    async def _handle_profile_frame(self, frame: dict[str, Any]) -> None:
        ftype = frame.get("type")
        if ftype == "PROFILE_REQ":
            req_id = frame.get("request_id", "")
            target_login = frame.get("target_login", "")
            if target_login == self.login:
                resp = {
                    "type": "PROFILE_RESP",
                    "request_id": req_id,
                    "profile": self.profile.get_summary(),
                }
                await self._send_game_frame(resp)
        elif ftype == "PROFILE_RESP":
            req_id = frame.get("request_id", "")
            if req_id == self._profile_request_id:
                profile = frame.get("profile", {})
                login = profile.get("login", "")
                if login:
                    self._profile_responses[login] = profile

    async def _handle_score_frame(self, frame: dict[str, Any]) -> None:
        ftype = frame.get("type")
        if ftype == "SCORE_REQ":
            req_id = frame.get("request_id", "")
            game_id = frame.get("game_id", "")
            my_scores = self.profile.get_game_score(game_id)
            resp = {
                "type": "SCORE_RESP",
                "request_id": req_id,
                "game_id": game_id,
                "login": self.login,
                "scores": my_scores or {},
            }
            await self._send_game_frame(resp)
        elif ftype == "SCORE_RESP":
            req_id = frame.get("request_id", "")
            if req_id == self._score_request_id:
                login = frame.get("login", "")
                scores = frame.get("scores", {})
                self._score_responses[login] = scores

    async def score_share(self, game_id: str) -> str:
        """Return a formatted score string for the given game_id."""
        scores = self.profile.get_game_score(game_id)
        if not scores:
            return f"Aucun score enregistré pour {game_id}"
        lines = [f"📊  Score de {self.login} sur {game_id}  📊"]
        for key, value in scores.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    async def score_list(self) -> list[tuple[int, str, str]]:
        """Return a list of (index, game_id, game_name) for games with saved scores."""
        game_ids = self.profile.list_games_with_scores()
        result: list[tuple[int, str, str]] = []
        for i, gid in enumerate(game_ids):
            g = get_game(gid)
            name = g.name if g else gid
            result.append((i, gid, name))
        return result

    async def leaderboard_request(self, game_id: str) -> dict[str, dict[str, Any]]:
        """Broadcast a score request and collect responses for ~2.5s."""
        import uuid
        req_id = str(uuid.uuid4())[:8]
        self._score_request_id = req_id
        self._score_request_game_id = game_id
        self._score_responses.clear()
        # Include ourselves
        self._score_responses[self.login] = self.profile.get_game_score(game_id) or {}

        frame = {
            "type": "SCORE_REQ",
            "request_id": req_id,
            "game_id": game_id,
        }
        await self._send_game_frame(frame)
        await asyncio.sleep(2.5)
        return dict(self._score_responses)

    async def profile_request(self, target_login: str) -> dict[str, Any] | None:
        """Broadcast a profile request and wait for a response."""
        import uuid
        req_id = str(uuid.uuid4())[:8]
        self._profile_request_id = req_id
        self._profile_responses.clear()
        
        if target_login == self.login:
            return self.profile.get_summary()

        frame = {
            "type": "PROFILE_REQ",
            "request_id": req_id,
            "target_login": target_login,
        }
        await self._send_game_frame(frame)
        
        # Wait up to 2 seconds for a response
        for _ in range(20):
            if target_login in self._profile_responses:
                return self._profile_responses[target_login]
            await asyncio.sleep(0.1)
            
        return None

    async def create_game_invite(self, game_id: str) -> tuple[str, GameInvite | None]:
        game = get_game(game_id)
        if not game:
            return "unknown_game", None
        if game.is_solo:
            invite = GameInvite(
                invite_id=f"solo-{self.login}-{int(time.time())}",
                game_id=game_id,
                game_name=game.name,
                host_login=self.login,
                max_players=game.max_players,
                players=[self.login],
            )
            self.active_invites[invite.invite_id] = invite
            self.current_game_invite = invite
            session = game.create_session(invite, on_state_change=self._on_local_game_state_change, on_score=self._on_game_score)
            self.current_game_session = session
            return "solo_started", invite

        # Multiplayer — must be in a channel
        if not self.current_channel_name():
            return "not_in_channel", None

        invite = GameInvite(
            invite_id=f"mp-{self.login}-{int(time.time())}",
            game_id=game_id,
            game_name=game.name,
            host_login=self.login,
            max_players=game.max_players,
            players=[self.login],
        )
        self.active_invites[invite.invite_id] = invite
        self.current_game_invite = invite

        frame = {"type": "GAME_INVITE", **invite.to_dict()}
        await self._send_game_frame(frame)
        return "created", invite

    async def join_game_invite(self, invite_id: str) -> str:
        invite = self.active_invites.get(invite_id)
        if not invite:
            return "unknown_invite"
        if self.login in invite.players:
            return "already_joined"
        if len(invite.players) >= invite.max_players:
            return "full"
        invite.players.append(self.login)
        self.current_game_invite = invite

        frame = {"type": "GAME_JOIN", "invite_id": invite_id, "login": self.login}
        await self._send_game_frame(frame)

        game = get_game(invite.game_id)
        if game and not game.is_solo:
            session = game.create_session(invite, on_state_change=self._on_local_game_state_change, on_score=self._on_game_score)
            self.current_game_session = session

        # If we are the host and have enough players, start the game
        if self.is_hosting and invite.host_login == self.login and len(invite.players) >= game.min_players:
            await self._start_game_session()
        return "joined"

    async def leave_game(self) -> None:
        if self.current_game_invite:
            invite_id = self.current_game_invite.invite_id
            frame = {"type": "GAME_LEAVE", "invite_id": invite_id, "login": self.login}
            await self._send_game_frame(frame)
        self.current_game_session = None
        self.current_game_invite = None

    async def send_game_action(self, action: str, data: dict[str, Any]) -> None:
        if not self.current_game_invite:
            return
        frame = {
            "type": "GAME_ACTION",
            "invite_id": self.current_game_invite.invite_id,
            "login": self.login,
            "action": action,
            "data": data,
        }
        await self._send_game_frame(frame)
        # Solo: directly handle locally
        if self.current_game_session:
            self.current_game_session.handle_action(self.login, action, data)
            if self.on_game_state_change:
                self.on_game_state_change(self.current_game_session.get_render_state())

    async def broadcast_game_state(self) -> None:
        if not self.current_game_session or not self.current_game_invite:
            return
        frame = {
            "type": "GAME_STATE",
            "invite_id": self.current_game_invite.invite_id,
            "state": self.current_game_session.get_render_state(),
        }
        await self._send_game_frame(frame)

    async def end_current_game(self, winner: str | None = None) -> None:
        if not self.current_game_invite:
            return
        final_state = self.current_game_session.get_render_state() if self.current_game_session else {}
        frame = {
            "type": "GAME_END",
            "invite_id": self.current_game_invite.invite_id,
            "winner": winner,
            "final_state": final_state,
        }
        await self._send_game_frame(frame)
        self.current_game_session = None
        self.current_game_invite = None

    async def _start_game_session(self) -> None:
        if not self.current_game_invite or not self.is_hosting:
            return
        game = get_game(self.current_game_invite.game_id)
        if not game:
            return
        session = game.create_session(self.current_game_invite, on_state_change=self._on_host_game_state_change, on_score=self._on_game_score)
        self.current_game_session = session
        await self.broadcast_game_state()

    def _on_local_game_state_change(self, state: dict[str, Any]) -> None:
        if self.on_game_state_change:
            self.on_game_state_change(state)

    def _on_host_game_state_change(self, state: dict[str, Any]) -> None:
        if self.on_game_state_change:
            self.on_game_state_change(state)
        # Auto-broadcast from host
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_game_state(),
                self._loop,
            )

    async def _send_game_frame(self, frame: dict[str, Any]) -> None:
        if self.ws and self._relay_current_channel:
            try:
                await self.ws.send(json.dumps(frame))
            except Exception:
                pass
        elif self.channel_server:
            await self.channel_server.broadcast(frame)
        elif self.channel_client and self.channel_client._connected:
            await self.channel_client.send_game_frame(frame)


def default_login() -> str:
    return os.environ.get("USER", "unknown")
