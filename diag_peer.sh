#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: ./diag_peer.sh <peer_ip> <peer_port> [my_listen_port]"
  exit 1
fi

PEER_IP="$1"
PEER_PORT="$2"
MY_LISTEN_PORT="${3:-42424}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${PROJECT_DIR}/.venv/bin/python"
OUT_DIR="${PROJECT_DIR}/diagnostics"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="${OUT_DIR}/peer_diag_${HOSTNAME}_${TS}.txt"

mkdir -p "${OUT_DIR}"
exec > >(tee "${OUT_FILE}") 2>&1

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

echo "============================================================"
echo "42msg Peer Connectivity Diagnostic"
echo "Date: $(date -Is)"
echo "Host: ${HOSTNAME}"
echo "Peer: ${PEER_IP}:${PEER_PORT}"
echo "============================================================"

echo
echo "[1] Local addressing"
if has_cmd ip; then
  ip -brief addr || true
  echo
  ip route get "${PEER_IP}" || true
else
  echo "ip command missing"
fi

echo
echo "[2] ICMP reachability"
if has_cmd ping; then
  ping -c 3 -W 1 "${PEER_IP}" || true
else
  echo "ping command missing"
fi

echo
echo "[3] ARP/neighbor visibility"
if has_cmd ip; then
  ip neigh show "${PEER_IP}" || true
elif has_cmd arp; then
  arp -n | grep -F "${PEER_IP}" || true
else
  echo "No ip/arp command"
fi

echo
echo "[4] TCP connectivity to peer app port"
if has_cmd nc; then
  nc -vz -w 2 "${PEER_IP}" "${PEER_PORT}" || true
else
  echo "nc missing, using python socket connect test"
  "${VENV_PY}" - <<PY
import socket
ip = "${PEER_IP}"
port = int("${PEER_PORT}")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect((ip, port))
    print("connect ok")
except Exception as e:
    print("connect failed:", e)
finally:
    s.close()
PY
fi

echo
echo "[5] Local TCP listener sanity check"
if has_cmd nc; then
  echo "Starting temporary listener on ${MY_LISTEN_PORT} for 5s"
  (timeout 5 nc -l -p "${MY_LISTEN_PORT}" >/dev/null 2>&1 || true) &
  sleep 1
  if has_cmd ss; then
    ss -ltn | grep ":${MY_LISTEN_PORT}" || true
  fi
else
  echo "nc missing, skip listener check"
fi

echo
echo "[6] Summary"
echo "If ping + TCP connect fail between both hosts, the network blocks host-to-host traffic."
echo "In that case, 42msg cannot work directly without an allowed relay/tunnel."

echo
echo "Report: ${OUT_FILE}"
echo "============================================================"
