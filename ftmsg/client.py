from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path

from .channel import ChannelClient, ChannelServer
from .crypto import generate_or_load_encryption_keypair
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
        self.local_ip = os.environ.get("FTMSG_LOCAL_IP")

        self.discovery: BroadcastDiscovery | None = None
        self.channel_server: ChannelServer | None = None
        self.channel_client: ChannelClient | None = None
        self.is_hosting = False

        self.incoming_queue: asyncio.Queue[tuple[str, str, float]] = asyncio.Queue()
        self.events_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.store.init()
        await self.trust.init()

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
        if self.discovery:
            await asyncio.to_thread(self.discovery.stop)

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

    async def create_channel(
        self, name: str, password: str, max_users: int, is_public: bool,
    ) -> str:
        if self.channel_server or self.channel_client:
            return "already_in_channel"

        local_ip = self._resolve_local_ip()

        def on_msg(sender: str, payload: str, ts: float) -> None:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.incoming_queue.put((sender, payload, ts)),
                    self._loop,
                )

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

        def on_leave(login: str) -> None:
            _update_beacon()
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.events_queue.put(f"{login} a quitté le salon"),
                    self._loop,
                )

        self.channel_server = ChannelServer(
            name=name,
            password=password,
            max_users=max_users,
            is_public=is_public,
            owner_login=self.login,
            on_message=on_msg,
            on_member_join=on_join,
            on_member_leave=on_leave,
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
        if self.channel_server or self.channel_client:
            return "already_in_channel", ""

        def on_msg(sender: str, payload: str, ts: float) -> None:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.incoming_queue.put((sender, payload, ts)),
                    self._loop,
                )

        def on_join(login: str) -> None:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.events_queue.put(f"{login} a rejoint le salon"),
                    self._loop,
                )

        def on_leave(login: str) -> None:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.events_queue.put(f"{login} a quitté le salon"),
                    self._loop,
                )

        def on_disc(reason: str) -> None:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.events_queue.put(f"Déconnecté du salon: {reason}"),
                    self._loop,
                )
            self.channel_client = None

        self.channel_client = ChannelClient(
            login=self.login,
            on_message=on_msg,
            on_member_join=on_join,
            on_member_leave=on_leave,
            on_disconnect=on_disc,
        )

        status, detail = await self.channel_client.connect(
            host_ip, host_port, password, self.login,
        )

        if status != "connected":
            self.channel_client = None
            return status, detail

        await self.events_queue.put(
            f"Connecté au salon '{detail}' ({host_ip}:{host_port})",
        )
        return "connected", detail

    async def leave_channel(self) -> None:
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

    async def send_channel_message(self, message: str) -> str:
        if self.channel_server:
            await self.channel_server.local_message(self.login, message)
            return "sent"

        if self.channel_client and self.channel_client._connected:
            ok = await self.channel_client.send_message(message)
            if ok:
                await self.incoming_queue.put((self.login, message, time.time()))
                return "sent"
            return "disconnected"

        return "not_in_channel"

    def list_channels(self) -> list[DiscoveredChannel]:
        if self.discovery:
            return self.discovery.get_channels()
        return []

    def list_members(self) -> list[str]:
        if self.channel_client:
            return self.channel_client.members
        if self.channel_server:
            return self.channel_server.member_logins()
        return []

    def current_channel_name(self) -> str:
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
