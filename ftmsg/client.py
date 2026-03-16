from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path

from .crypto import decrypt, encrypt, generate_or_load_encryption_keypair
from .discovery import MdnsDiscovery, Peer
from .forwarding import StoreAndForward
from .security import (
    generate_or_load_signing_keypair,
    sign_frame,
    verify_frame_signature,
)
from .store import MessageStore
from .transport import AsyncTransport
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

        self.transport = AsyncTransport(on_frame=self._on_frame)
        self.store = MessageStore(self.db_path)
        self.trust = TrustStore(self.db_path)
        self.forwarding = StoreAndForward(self.store, self.transport)
        self.local_ip = os.environ.get("FTMSG_LOCAL_IP")

        self.discovery: MdnsDiscovery | None = None
        self.incoming_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.events_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.store.init()
        await self.trust.init()

        listen_port = await self.transport.start()
        self.discovery = MdnsDiscovery(
            login=self.login,
            listen_port=listen_port,
            signing_key_b64=base64.b64encode(
                bytes(self.sign_public_key),
            ).decode("utf-8"),
            encryption_key_b64=base64.b64encode(
                bytes(self.enc_public_key),
            ).decode("utf-8"),
            local_ip=self.local_ip,
            on_peer_online=self._on_peer_online_sync,
            on_peer_offline=self._on_peer_offline_sync,
        )
        await asyncio.to_thread(self.discovery.start)
        await self.events_queue.put(
            f"Node prêt: {self.login} écoute sur {listen_port}",
        )

    async def stop(self) -> None:
        if self.discovery is not None:
            await asyncio.to_thread(self.discovery.stop)
        await self.transport.stop()

    async def send_message(self, target_login: str, message: str) -> str:
        peer = (
            self.discovery.online_peers().get(target_login)
            if self.discovery
            else None
        )

        if peer is not None:
            await self._trust_peer(peer)
            encryption_pubkey = peer.encryption_key_b64
        else:
            identity = await self.trust.get_identity(target_login)
            encryption_pubkey = (
                identity.encryption_pubkey if identity else None
            )

        if not encryption_pubkey:
            return "unknown_peer"

        payload = encrypt(message, encryption_pubkey)
        frame = {
            "sender_login": self.login,
            "timestamp": int(time.time()),
            "type": "MESSAGE",
            "payload": payload,
        }
        frame["signature"] = sign_frame(frame, self.sign_private_key)
        return await self.forwarding.send_or_queue(
            target_login=target_login,
            frame=frame,
            peer=peer,
        )

    def list_online_peers(self) -> dict[str, Peer]:
        if self.discovery is None:
            return {}
        return self.discovery.online_peers()

    async def _on_frame(self, frame: dict, sender_ip: str) -> None:
        if frame.get("type") != "MESSAGE":
            return

        sender_login = frame.get("sender_login")
        if not sender_login or not isinstance(sender_login, str):
            return

        identity = await self.trust.get_identity(sender_login)
        if identity is None:
            peer = (
                self.discovery.online_peers().get(sender_login)
                if self.discovery
                else None
            )
            if (
                peer is None
                or not peer.signing_key_b64
                or not peer.encryption_key_b64
            ):
                await self.events_queue.put(
                    "Message rejeté: identité TOFU "
                    f"inconnue pour {sender_login}",
                )
                return
            verdict = await self.trust.observe_peer(
                sender_login,
                peer.signing_key_b64,
                peer.encryption_key_b64,
            )
            if verdict == "mismatch":
                await self.events_queue.put(
                    "Alerte TOFU: tentative d'usurpation "
                    f"pour {sender_login}",
                )
                return
            identity = await self.trust.get_identity(sender_login)
            if identity is None:
                return

        if not verify_frame_signature(frame, identity.signing_pubkey):
            await self.events_queue.put(
                f"Message rejeté: signature invalide de {sender_login}",
            )
            return

        try:
            clear = decrypt(
                frame["payload"],
                self.enc_private_key,
            ).decode("utf-8", errors="replace")
        except Exception:
            await self.events_queue.put(
                f"Message rejeté: payload invalide de {sender_login}",
            )
            return

        await self.incoming_queue.put((sender_login, clear))

    async def _trust_peer(self, peer: Peer) -> str:
        if not peer.signing_key_b64 or not peer.encryption_key_b64:
            return "missing_keys"
        verdict = await self.trust.observe_peer(
            peer.login,
            peer.signing_key_b64,
            peer.encryption_key_b64,
        )
        if verdict == "mismatch":
            await self.events_queue.put(
                "Alerte TOFU: tentative d'usurpation "
                f"détectée pour {peer.login}",
            )
        return verdict

    def _on_peer_online_sync(self, peer: Peer) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._on_peer_online(peer),
            self._loop,
        )

    async def _on_peer_online(self, peer: Peer) -> None:
        verdict = await self._trust_peer(peer)
        if verdict == "mismatch":
            return
        await self.events_queue.put(
            f"{peer.login} en ligne ({peer.ip}:{peer.port})",
        )
        flushed = await self.forwarding.on_peer_online(peer)
        if flushed:
            await self.events_queue.put(
                f"{flushed} message(s) pending envoyé(s) à {peer.login}",
            )

    def _on_peer_offline_sync(self, login: str) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.events_queue.put(f"{login} est hors ligne"),
            self._loop,
        )


def default_login() -> str:
    return os.environ.get("USER", "unknown")
