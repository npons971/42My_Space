from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .channel import ChannelInfo


BROADCAST_PORT = 42069
BEACON_INTERVAL = 3.0


def resolve_broadcast_addr() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
        sock.close()
        parts = local_ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    except OSError:
        return "255.255.255.255"


def resolve_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


@dataclass
class DiscoveredChannel:
    name: str
    host_ip: str
    host_port: int
    is_public: bool
    user_count: int
    max_users: int
    last_seen: float = 0.0


class BroadcastDiscovery:
    def __init__(
        self,
        on_channel: Callable[[DiscoveredChannel], None] | None = None,
        on_channel_lost: Callable[[str], None] | None = None,
    ) -> None:
        self.local_ip = resolve_local_ip()
        self.broadcast_addr = resolve_broadcast_addr()
        self.on_channel = on_channel
        self.on_channel_lost = on_channel_lost

        self._channels: dict[str, DiscoveredChannel] = {}
        self._lock = threading.Lock()
        self._running = False
        self._listen_thread: threading.Thread | None = None
        self._beacon_thread: threading.Thread | None = None
        self._beacon_info: ChannelInfo | None = None
        self._own_channel_key: str | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._listen_thread:
            self._listen_thread.join(timeout=2.0)
            self._listen_thread = None
        self._stop_beaconing()

    def start_beaconing(self, info: ChannelInfo) -> None:
        self._beacon_info = info
        self._own_channel_key = f"{info.host_ip}:{info.host_port}"
        if self._beacon_thread and self._beacon_thread.is_alive():
            return
        self._beacon_thread = threading.Thread(target=self._beacon_loop, daemon=True)
        self._beacon_thread.start()

    def _stop_beaconing(self) -> None:
        self._beacon_info = None
        self._own_channel_key = None
        self._beacon_thread = None

    def update_beacon(self, info: ChannelInfo) -> None:
        self._beacon_info = info

    def get_channels(self) -> list[DiscoveredChannel]:
        now = time.time()
        with self._lock:
            active = {}
            for key, ch in self._channels.items():
                if now - ch.last_seen < 10.0:
                    active[key] = ch
                elif self.on_channel_lost:
                    self.on_channel_lost(ch.name)
            self._channels = active
            return list(active.values())

    def _send_beacon(self, sock: socket.socket) -> None:
        if not self._beacon_info:
            return
        info = self._beacon_info
        packet = json.dumps({
            "type": "42MSG_BEACON",
            "channel_name": info.name,
            "host_ip": info.host_ip,
            "host_port": info.host_port,
            "is_public": info.is_public,
            "user_count": info.user_count,
            "max_users": info.max_users,
            "version": 2,
        }, separators=(",", ":"))

        try:
            sock.sendto(packet.encode("utf-8"), (self.broadcast_addr, BROADCAST_PORT))
        except OSError:
            pass

        if self.local_ip != self.broadcast_addr:
            try:
                sock.sendto(packet.encode("utf-8"), (self.local_ip, BROADCAST_PORT))
            except OSError:
                pass

    def _beacon_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)

        try:
            while self._running and self._beacon_info:
                self._send_beacon(sock)
                time.sleep(BEACON_INTERVAL)
        finally:
            sock.close()

    def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)

        try:
            sock.bind(("", BROADCAST_PORT))
        except OSError:
            try:
                sock.bind(("", 0))
            except OSError:
                sock.close()
                return

        try:
            while self._running:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break

                try:
                    msg = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                if not isinstance(msg, dict) or msg.get("type") != "42MSG_BEACON":
                    continue

                if msg.get("version") != 2:
                    continue

                host_ip = str(msg.get("host_ip", addr[0]))
                host_port = int(msg.get("host_port", 0))
                if host_port == 0:
                    continue

                if host_ip == self.local_ip:
                    continue

                key = f"{host_ip}:{host_port}"
                if key == self._own_channel_key:
                    continue

                ch = DiscoveredChannel(
                    name=str(msg.get("channel_name", "")),
                    host_ip=host_ip,
                    host_port=host_port,
                    is_public=bool(msg.get("is_public", False)),
                    user_count=int(msg.get("user_count", 0)),
                    max_users=int(msg.get("max_users", 0)),
                    last_seen=time.time(),
                )

                with self._lock:
                    self._channels[key] = ch

                if self.on_channel:
                    self.on_channel(ch)

        finally:
            sock.close()
