from __future__ import annotations

import argparse
import base64
import random
import signal
import threading
import time

from nacl.public import PrivateKey

from ftmsg.discovery import MdnsDiscovery


def now() -> str:
    return time.strftime("%H:%M:%S")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live mDNS check for 42msg")
    parser.add_argument("--login", required=True, help="Login visible on mDNS")
    parser.add_argument(
        "--duration",
        type=int,
        default=45,
        help="How many seconds to run (default: 45)",
    )
    args = parser.parse_args()

    enc_pub = PrivateKey.generate().public_key
    sign_pub = PrivateKey.generate().public_key

    stop_event = threading.Event()

    def on_online(peer) -> None:
        print(
            f"[{now()}] ONLINE  login={peer.login} ip={peer.ip} "
            f"port={peer.port} sign_key={bool(peer.signing_key_b64)} "
            f"enc_key={bool(peer.encryption_key_b64)}"
        )

    def on_offline(login: str) -> None:
        print(f"[{now()}] OFFLINE login={login}")

    discovery = MdnsDiscovery(
        login=args.login,
        listen_port=random.randint(30000, 50000),
        signing_key_b64=base64.b64encode(bytes(sign_pub)).decode("utf-8"),
        encryption_key_b64=base64.b64encode(bytes(enc_pub)).decode("utf-8"),
        on_peer_online=on_online,
        on_peer_offline=on_offline,
    )

    def _signal_handler(_sig, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print(f"[{now()}] Starting mDNS check as login={args.login}")
    discovery.start()
    print(
        f"[{now()}] Local IP={discovery.local_ip} "
        f"listen_port={discovery.listen_port}"
    )
    print(f"[{now()}] Waiting {args.duration}s for peers... (Ctrl+C to stop)")

    deadline = time.time() + args.duration
    seen_non_self = False

    try:
        while time.time() < deadline and not stop_event.is_set():
            peers = discovery.online_peers()
            others = [name for name in peers.keys() if name != args.login]
            if others:
                seen_non_self = True
            print(f"[{now()}] Snapshot peers={sorted(peers.keys())}")
            time.sleep(5)
    finally:
        discovery.stop()
        print(f"[{now()}] mDNS check stopped")
        if seen_non_self:
            print(f"[{now()}] RESULT: at least one peer discovered")
        else:
            print(f"[{now()}] RESULT: no peer discovered")


if __name__ == "__main__":
    main()
