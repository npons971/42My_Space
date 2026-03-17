#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/tunnel_session.sh \
    --pivot user@pivot.host \
    --my-port 35051 \
    --publish-port 45002 \
    --peer-publish-port 45001 \
    --peer-local-port 55001 \
    --peer-login login_ami

Description:
  - Ouvre un tunnel SSH reverse (-R) pour publier TON port local sur le pivot
  - Ouvre un tunnel SSH local   (-L) pour atteindre le port publié par TON AMI
  - Affiche la commande /link à utiliser dans 42msg

Options:
  --pivot              user@host SSH du serveur pivot (obligatoire)
  --my-port            port local de TON 42msg (obligatoire)
  --publish-port       port exposé côté pivot pour ton port (obligatoire)
  --peer-publish-port  port exposé côté pivot par ton ami (obligatoire)
  --peer-local-port    port local vers le tunnel ami (obligatoire)
  --peer-login         login 42 de ton ami pour la commande /link (obligatoire)

Notes:
  - Ne nécessite pas sudo
  - Garde ce script actif pendant la session chat
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Commande manquante: $1" >&2
    exit 1
  fi
}

PIVOT=""
MY_PORT=""
PUBLISH_PORT=""
PEER_PUBLISH_PORT=""
PEER_LOCAL_PORT=""
PEER_LOGIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pivot)
      PIVOT="${2:-}"
      shift 2
      ;;
    --my-port)
      MY_PORT="${2:-}"
      shift 2
      ;;
    --publish-port)
      PUBLISH_PORT="${2:-}"
      shift 2
      ;;
    --peer-publish-port)
      PEER_PUBLISH_PORT="${2:-}"
      shift 2
      ;;
    --peer-local-port)
      PEER_LOCAL_PORT="${2:-}"
      shift 2
      ;;
    --peer-login)
      PEER_LOGIN="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Option inconnue: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$PIVOT" || -z "$MY_PORT" || -z "$PUBLISH_PORT" || -z "$PEER_PUBLISH_PORT" || -z "$PEER_LOCAL_PORT" || -z "$PEER_LOGIN" ]]; then
  echo "Arguments manquants" >&2
  usage
  exit 1
fi

require_cmd ssh

SSH_OPTS=(
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
)

cleanup() {
  if [[ -n "${PID_R:-}" ]] && kill -0 "$PID_R" 2>/dev/null; then
    kill "$PID_R" 2>/dev/null || true
  fi
  if [[ -n "${PID_L:-}" ]] && kill -0 "$PID_L" 2>/dev/null; then
    kill "$PID_L" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

echo "[tunnel] publication de ton port local ${MY_PORT} vers ${PIVOT}:${PUBLISH_PORT}"
ssh -N "${SSH_OPTS[@]}" -R "${PUBLISH_PORT}:127.0.0.1:${MY_PORT}" "$PIVOT" &
PID_R=$!
sleep 1
if ! kill -0 "$PID_R" 2>/dev/null; then
  echo "[tunnel] échec du tunnel reverse (-R)" >&2
  exit 1
fi

echo "[tunnel] ouverture locale 127.0.0.1:${PEER_LOCAL_PORT} -> ${PIVOT}:${PEER_PUBLISH_PORT}"
ssh -N "${SSH_OPTS[@]}" -L "${PEER_LOCAL_PORT}:127.0.0.1:${PEER_PUBLISH_PORT}" "$PIVOT" &
PID_L=$!
sleep 1
if ! kill -0 "$PID_L" 2>/dev/null; then
  echo "[tunnel] échec du tunnel local (-L)" >&2
  exit 1
fi

echo
echo "[tunnel] tunnels actifs"
echo "[tunnel] commande à coller dans 42msg:"
echo "  /link ${PEER_LOGIN} 127.0.0.1 ${PEER_LOCAL_PORT}"
echo
echo "[tunnel] laisse ce terminal ouvert. Ctrl+C pour fermer les tunnels."

wait
