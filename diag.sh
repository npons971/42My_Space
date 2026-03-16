#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${PROJECT_DIR}/.venv/bin/python"
OUT_DIR="${PROJECT_DIR}/diagnostics"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="${OUT_DIR}/diag_${HOSTNAME}_${TS}.txt"

mkdir -p "${OUT_DIR}"

exec > >(tee "${OUT_FILE}") 2>&1

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

echo "============================================================"
echo "42msg Diagnostic Report"
echo "Date: $(date -Is)"
echo "Host: ${HOSTNAME}"
echo "User: ${USER:-unknown}"
echo "PWD : ${PROJECT_DIR}"
echo "============================================================"

echo
echo "[1] Versions"
echo "- uname:" && uname -a || true
echo "- python (venv):"
if [[ -x "${VENV_PY}" ]]; then
  "${VENV_PY}" -V || true
else
  echo "  .venv python introuvable: ${VENV_PY}"
fi
echo "- python (system):" && python3 -V || true

echo
echo "[2] Réseau local (IP / routes / interfaces)"
if has_cmd ip; then
  echo "- ip addr:" && ip -brief addr || true
elif has_cmd ifconfig; then
  echo "- ifconfig:" && ifconfig || true
else
  echo "- Aucun outil ip/ifconfig trouvé"
fi
echo
if has_cmd ip; then
  echo "- ip route:" && ip route || true
elif has_cmd route; then
  echo "- route -n:" && route -n || true
else
  echo "- Aucun outil ip/route trouvé"
fi
echo
if has_cmd ip; then
  echo "- default route details:" && ip route get 8.8.8.8 || true
else
  echo "- default route details: indisponible (ip absent)"
fi

echo
echo "[3] Multicast / mDNS prérequis"
if has_cmd ip; then
  echo "- route multicast (224.0.0.0/4):" && ip route show 224.0.0.0/4 || true
elif has_cmd route; then
  echo "- route multicast (route -n | grep 224):" && route -n | grep 224 || true
else
  echo "- route multicast: indisponible (ip/route absents)"
fi

if has_cmd ss; then
  echo "- socket 5353 listeners (ss):" && ss -ulnp | grep ':5353' || true
elif has_cmd netstat; then
  echo "- socket 5353 listeners (netstat):" && netstat -ulnp 2>/dev/null | grep ':5353' || true
else
  echo "- socket 5353 listeners: indisponible (ss/netstat absents)"
fi

echo "- avahi-daemon status (if available):"
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-active avahi-daemon || true
elif has_cmd service; then
  service avahi-daemon status || true
else
  echo "  systemctl/service non disponibles"
fi

echo
echo "[4] Firewall (si disponible)"
if command -v ufw >/dev/null 2>&1; then
  echo "- ufw status:" && ufw status verbose || true
fi
if command -v nft >/dev/null 2>&1; then
  echo "- nft ruleset (first 120 lines):"
  nft list ruleset 2>/dev/null | sed -n '1,120p' || true
fi
if command -v iptables >/dev/null 2>&1; then
  echo "- iptables -S (first 120 lines):"
  iptables -S 2>/dev/null | sed -n '1,120p' || true
fi

echo
echo "[5] Inspection Python zeroconf"
if [[ -x "${VENV_PY}" ]]; then
  "${VENV_PY}" - <<'PY'
import inspect
import socket
from zeroconf import ServiceBrowser, Zeroconf

print("ServiceBrowser signature:")
print(inspect.signature(ServiceBrowser.__init__))

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.connect(("8.8.8.8", 80))
    print("Detected local IP:", sock.getsockname()[0])
except Exception as exc:
    print("IP detect failed:", exc)
finally:
    sock.close()

zc = Zeroconf()
print("Zeroconf started OK")
zc.close()
print("Zeroconf closed OK")
PY
else
  echo "Skip python zeroconf check: venv python absent"
fi

echo
echo "[6] Mini self-test mDNS (annonce + découverte locale)"
if [[ -x "${VENV_PY}" ]]; then
  "${VENV_PY}" - <<'PY'
import time
from nacl.public import PrivateKey

from ftmsg.discovery import MdnsDiscovery

login = "diag_self"
enc_pub = PrivateKey.generate().public_key
sign_pub = PrivateKey.generate().public_key

import base64
discovery = MdnsDiscovery(
    login=login,
    listen_port=42424,
    signing_key_b64=base64.b64encode(bytes(sign_pub)).decode(),
    encryption_key_b64=base64.b64encode(bytes(enc_pub)).decode(),
)

print("Starting MdnsDiscovery self-test...")
discovery.start()
time.sleep(2.0)
peers = discovery.online_peers()
print("Self-test discovered peers count:", len(peers))
print("Peers:", sorted(peers.keys()))
discovery.stop()
print("MdnsDiscovery self-test stopped")
PY
else
  echo "Skip mDNS self-test: venv python absent"
fi

echo
echo "[7] ftmsg runtime smoke (without TUI, 6s)"
if [[ -x "${VENV_PY}" ]]; then
  "${VENV_PY}" - <<'PY'
import asyncio

from ftmsg.client import FTMessageClient

async def main():
    c = FTMessageClient(login="diag_runtime")
    await c.start()
    await asyncio.sleep(6)
    peers = c.list_online_peers()
    print("Runtime peers count:", len(peers))
    print("Runtime peers:", sorted(peers.keys()))
    while not c.events_queue.empty():
        evt = await c.events_queue.get()
        print("EVENT:", evt)
    await c.stop()

asyncio.run(main())
PY
else
  echo "Skip runtime smoke: venv python absent"
fi

echo
echo "[8] Quick ping gateway"
GW=""
if has_cmd ip; then
  GW="$(ip route 2>/dev/null | awk '/default/ {print $3; exit}' || true)"
elif has_cmd route; then
  GW="$(route -n 2>/dev/null | awk '$4 ~ /G/ {print $2; exit}' || true)"
fi

if [[ -n "${GW}" ]]; then
  echo "Gateway: ${GW}"
  ping -c 2 -W 1 "${GW}" || true
else
  echo "No default gateway detected"
fi

echo
echo "============================================================"
echo "Diagnostic terminé"
echo "Rapport: ${OUT_FILE}"
echo "Partage ce fichier pour analyse"
echo "============================================================"
