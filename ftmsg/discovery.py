from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address

from zeroconf import (
    IPVersion,
    ServiceBrowser,
    ServiceInfo,
    ServiceListener,
    Zeroconf,
)


SERVICE_TYPE = "_42msg._tcp.local."


@dataclass(slots=True)
class Peer:
    login: str
    ip: str
    port: int
    signing_key_b64: str | None = None
    encryption_key_b64: str | None = None
    last_seen: float = 0.0


class PeerDirectory:
    def __init__(self) -> None:
        self._peers: dict[str, Peer] = {}
        self._lock = threading.Lock()

    def upsert(self, peer: Peer) -> None:
        with self._lock:
            self._peers[peer.login] = peer

    def remove(self, login: str) -> None:
        with self._lock:
            self._peers.pop(login, None)

    def get(self, login: str) -> Peer | None:
        with self._lock:
            return self._peers.get(login)

    def snapshot(self) -> dict[str, Peer]:
        with self._lock:
            return dict(self._peers)


def resolve_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _decode_txt_value(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def build_txt_properties(
    login: str,
    ip: str,
    port: int,
    signing_key_b64: str | None = None,
    encryption_key_b64: str | None = None,
) -> dict[bytes, bytes]:
    properties: dict[bytes, bytes] = {
        b"login": login.encode("utf-8"),
        b"ip": ip.encode("utf-8"),
        b"port": str(port).encode("utf-8"),
    }
    if signing_key_b64:
        properties[b"sign_pubkey"] = signing_key_b64.encode("utf-8")
        properties[b"pubkey"] = signing_key_b64.encode("utf-8")
    if encryption_key_b64:
        properties[b"enc_pubkey"] = encryption_key_b64.encode("utf-8")
    return properties


def parse_peer_from_service_info(info: ServiceInfo) -> Peer | None:
    props = info.properties or {}
    login = _decode_txt_value(props.get(b"login"))
    advertised_ip = _decode_txt_value(props.get(b"ip"))
    port_str = _decode_txt_value(props.get(b"port"))
    signing_key = (
        _decode_txt_value(props.get(b"sign_pubkey"))
        or _decode_txt_value(props.get(b"pubkey"))
    )
    encryption_key = _decode_txt_value(props.get(b"enc_pubkey"))

    if not login or not port_str:
        return None

    parsed_addresses = info.parsed_addresses()
    ip = parsed_addresses[0] if parsed_addresses else advertised_ip
    if not ip:
        return None

    try:
        ip_address(ip)
        port = int(port_str)
    except ValueError:
        return None

    return Peer(
        login=login,
        ip=ip,
        port=port,
        signing_key_b64=signing_key,
        encryption_key_b64=encryption_key,
        last_seen=time.time(),
    )


class _PeerListener(ServiceListener):
    def __init__(
        self,
        zeroconf: Zeroconf,
        peers: PeerDirectory,
        own_login: str,
        on_peer_online: Callable[[Peer], None] | None = None,
        on_peer_offline: Callable[[str], None] | None = None,
    ) -> None:
        self._zeroconf = zeroconf
        self._peers = peers
        self._own_login = own_login
        self._on_peer_online = on_peer_online
        self._on_peer_offline = on_peer_offline

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self._refresh(service_type, name)

    def update_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        self._refresh(service_type, name)

    def remove_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        login = name.split(".", 1)[0]
        if login != self._own_login:
            self._peers.remove(login)
            if self._on_peer_offline is not None:
                self._on_peer_offline(login)

    def _refresh(self, service_type: str, name: str) -> None:
        info = self._zeroconf.get_service_info(service_type, name)
        if info is None:
            return

        peer = parse_peer_from_service_info(info)
        if peer is None or peer.login == self._own_login:
            return

        self._peers.upsert(peer)
        if self._on_peer_online is not None:
            self._on_peer_online(peer)


class MdnsDiscovery:
    def __init__(
        self,
        login: str,
        listen_port: int,
        signing_key_b64: str | None = None,
        encryption_key_b64: str | None = None,
        local_ip: str | None = None,
        on_peer_online: Callable[[Peer], None] | None = None,
        on_peer_offline: Callable[[str], None] | None = None,
    ) -> None:
        self.login = login
        self.listen_port = listen_port
        self.signing_key_b64 = signing_key_b64
        self.encryption_key_b64 = encryption_key_b64
        self.local_ip = local_ip or resolve_local_ip()
        self.on_peer_online = on_peer_online
        self.on_peer_offline = on_peer_offline

        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._listener: _PeerListener | None = None
        self._registered_info: ServiceInfo | None = None
        self.peers = PeerDirectory()

    def start(self) -> None:
        if self._zeroconf is not None:
            return

        try:
            self._zeroconf = Zeroconf(
                interfaces=[self.local_ip],
                ip_version=IPVersion.V4Only,
            )
        except Exception:
            self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        properties = build_txt_properties(
            login=self.login,
            ip=self.local_ip,
            port=self.listen_port,
            signing_key_b64=self.signing_key_b64,
            encryption_key_b64=self.encryption_key_b64,
        )

        service_name = f"{self.login}.{SERVICE_TYPE}"
        info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=service_name,
            addresses=[ip_address(self.local_ip).packed],
            port=self.listen_port,
            properties=properties,
        )

        self._zeroconf.register_service(info)
        self._registered_info = info
        self._listener = _PeerListener(
            self._zeroconf,
            self.peers,
            own_login=self.login,
            on_peer_online=self.on_peer_online,
            on_peer_offline=self.on_peer_offline,
        )
        self._browser = ServiceBrowser(
            self._zeroconf,
            SERVICE_TYPE,
            listener=self._listener,
        )

    def stop(self) -> None:
        if self._zeroconf is None:
            return

        if self._registered_info is not None:
            self._zeroconf.unregister_service(self._registered_info)
            self._registered_info = None

        self._zeroconf.close()
        self._zeroconf = None
        self._browser = None
        self._listener = None

    def online_peers(self) -> dict[str, Peer]:
        return self.peers.snapshot()
