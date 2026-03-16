from __future__ import annotations

from .discovery import Peer
from .protocol import Frame
from .store import MessageStore
from .transport import AsyncTransport


class StoreAndForward:
    def __init__(self, store: MessageStore, transport: AsyncTransport) -> None:
        self.store = store
        self.transport = transport

    async def send_or_queue(self, target_login: str, frame: Frame, peer: Peer | None) -> str:
        if peer is None:
            await self.store.add_pending(target_login=target_login, frame=frame, last_error="offline")
            return "pending"

        sent = await self.transport.send_frame(peer.ip, peer.port, frame)
        if sent:
            return "sent"

        await self.store.add_pending(
            target_login=target_login,
            frame=frame,
            target_ip=peer.ip,
            target_port=peer.port,
            last_error="send_failed",
        )
        return "pending"

    async def on_peer_online(self, peer: Peer) -> int:
        pending = await self.store.list_pending_for_login(peer.login)
        sent_count = 0
        for message in pending:
            sent = await self.transport.send_frame(peer.ip, peer.port, message.frame)
            if sent:
                await self.store.mark_sent(message.id)
                sent_count += 1
            else:
                await self.store.set_error(message.id, "retry_failed")
                break
        return sent_count
