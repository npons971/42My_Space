# Contexte 42msg — Système de salons en réseau local

## Objectif
Messagerie terminal par salons (channels) en réseau local inter-postes.
Contrainte : postes isolés (pas de ping, mDNS bloqué).
Solution : découverte par broadcast UDP, transport TCP.

## Architecture
- **Découverte** (`discovery.py`): Broadcast UDP port 42069, beacon JSON toutes les 3s.
- **Serveur salon** (`channel.py`): TCP `ChannelServer` — auth password, broadcast messages, gestion membres.
- **Client salon** (`channel.py`): TCP `ChannelClient` — connexion, envoi/réception frames.
- **Client app** (`client.py`): `FTMessageClient` — orchestration create/join/leave, queues async.
- **TUI** (`tui.py`): Textual — commandes `/create`, `/join`, `/list`, `/leave`, `/peers`, `/help`, `/quit`.
- **Crypto** (`crypto.py`, `security.py`): NaCl Curve25519 — chiffrement clés persistantes (TOFU).
- **Store** (`store.py`, `trust.py`): SQLite via aiosqlite — identités et messages (héritage P2P).

## Protocole
- **Beacon UDP**: `{"type":"42MSG_BEACON", "channel_name":"...", "host_ip":"...", "host_port":N, "is_public":bool, "user_count":N, "max_users":N, "version":2}`
- **Frames TCP** (length-prefixed JSON):
  - Client→Serveur: `JOIN`, `MESSAGE`, `LEAVE`
  - Serveur→Client: `CHANNEL_INFO`, `JOIN_ACCEPTED`, `JOIN_REJECTED`, `MESSAGE`, `USER_JOINED`, `USER_LEFT`, `CHANNEL_CLOSED`

## Flux utilisation
1. User lance `42msg` (install via `make install`, alias dans `.zshrc`)
2. `/create <nom> <max> [password]` → héberge salon, broadcast UDP
3. Autres users `/list` → voient le salon, `/join <ip> <port> <password>`
4. Tape message → broadcast à tous les membres

## Stack
Python 3.12, pynacl, aiosqlite, textual. Plus de zeroconf.

## Fichiers actifs
- `ftmsg/channel.py` — serveur/client salon (nouveau)
- `ftmsg/discovery.py` — broadcast UDP (réécrit)
- `ftmsg/client.py` — orchestration salons (réécrit)
- `ftmsg/tui.py` — interface terminal (réécrit)
- `ftmsg/protocol.py`, `crypto.py`, `security.py`, `store.py`, `trust.py` — héritage (inchangés fonctionnellement)
- `Makefile`, `install.sh`, `requirements.txt`, `README.md` — build/doc

## Tests validés
- Création salon public/privé
- Auth par mot de passe (rejet si mauvais)
- Rejet si salon plein
- Rejet si login dupliqué
- Messaging 3 utilisateurs (tous reçoivent tous les messages)
- Départ/reconnexion
