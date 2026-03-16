from __future__ import annotations

import asyncio
import base64
import json
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
        self.listen_port: int | None = None
        self._manual_peers: dict[str, Peer] = {}

        self.discovery: MdnsDiscovery | None = None
        self.incoming_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.events_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.store.init()
        await self.trust.init()

        listen_port = await self.transport.start()
        self.listen_port = listen_port
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
        peer = self._known_peer(target_login)

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
        peers = dict(self._manual_peers)
        if self.discovery is not None:
            peers.update(self.discovery.online_peers())
        return peers

    async def link_peer(
        self,
        target_login: str,
        target_ip: str,
        target_port: int,
    ) -> str:
        if self.listen_port is None:
            return "not_started"

        frame = self._make_hello_frame("HELLO")
        sent = await self.transport.send_frame(target_ip, target_port, frame)
        if not sent:
            return "connect_failed"

        self._manual_peers[target_login] = Peer(
            login=target_login,
            ip=target_ip,
            port=target_port,
            last_seen=time.time(),
        )
        return "link_sent"

    async def _on_frame(self, frame: dict, sender_ip: str) -> None:
        frame_type = frame.get("type")
        if frame_type in {"HELLO", "HELLO_ACK"}:
            await self._handle_hello(frame, sender_ip)
            return

        if frame_type != "MESSAGE":
            return

        sender_login = frame.get("sender_login")
        if not sender_login or not isinstance(sender_login, str):
            return

        identity = await self.trust.get_identity(sender_login)
        if identity is None:
            peer = self._known_peer(sender_login)
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

    def _known_peer(self, login: str) -> Peer | None:
        if login in self._manual_peers:
            return self._manual_peers[login]
        if self.discovery is None:
            return None
        return self.discovery.online_peers().get(login)

    def _make_hello_frame(self, frame_type: str) -> dict:
        payload = {
            "listen_port": self.listen_port,
            "signing_pubkey": base64.b64encode(
                bytes(self.sign_public_key),
            ).decode("utf-8"),
            "encryption_pubkey": base64.b64encode(
                bytes(self.enc_public_key),
            ).decode("utf-8"),
        }
        frame = {
            "sender_login": self.login,
            "timestamp": int(time.time()),
            "type": frame_type,
            "payload": json.dumps(payload, separators=(",", ":")),
        }
        frame["signature"] = sign_frame(frame, self.sign_private_key)
        return frame

    async def _handle_hello(self, frame: dict, sender_ip: str) -> None:
        sender_login = frame.get("sender_login")
        if not sender_login or not isinstance(sender_login, str):
            return

        try:
            payload = json.loads(frame.get("payload", "{}"))
            listen_port = int(payload["listen_port"])
            signing_pubkey = str(payload["signing_pubkey"])
            encryption_pubkey = str(payload["encryption_pubkey"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            await self.events_queue.put(
                f"HELLO rejeté: payload invalide de {sender_login}",
            )
            return

        if not verify_frame_signature(frame, signing_pubkey):
            await self.events_queue.put(
                f"HELLO rejeté: signature invalide de {sender_login}",
            )
            return

        verdict = await self.trust.observe_peer(
            sender_login,
            signing_pubkey,
            encryption_pubkey,
        )
        if verdict == "mismatch":
            await self.events_queue.put(
                "Alerte TOFU: tentative d'usurpation "
                f"détectée pour {sender_login}",
            )
            return

        self._manual_peers[sender_login] = Peer(
            login=sender_login,
            ip=sender_ip,
            port=listen_port,
            signing_key_b64=signing_pubkey,
            encryption_key_b64=encryption_pubkey,
            last_seen=time.time(),
        )
        await self.events_queue.put(
            "Lien manuel établi avec "
            f"{sender_login} ({sender_ip}:{listen_port})",
        )

        if frame.get("type") == "HELLO" and self.listen_port is not None:
            ack = self._make_hello_frame("HELLO_ACK")
            await self.transport.send_frame(sender_ip, listen_port, ack)


def default_login() -> str:
    return os.environ.get("USER", "unknown")
