# 42msg — Messagerie par salons (LAN)

Chat en réseau local par salons. Découverte par broadcast UDP, messages
sur TCP. Pas de serveur central — chaque salon est hébergé par son créateur.

## Quick start

```bash
git clone <url_du_repo>
cd 42My_Space
make install   # crée le venv, installe les deps localement, configure l'alias
source ~/.zshrc
42msg
```

Ou avec un pseudo spécifique:

```bash
42msg --login mon_login
```

## Installation & venv

Les dépendances s'installent **exclusivement** dans un venv local (`.venv/`).
Aucun package n'est installé globalement sur le système.

| Commande | Description |
|---|---|
| `make install` | Crée le venv, installe les dépendances et ajoute l'alias dans `~/.zshrc` |
| `make run` | Lance l'application avec le Python du venv |
| `make run-login LOGIN=...` | Lance l'application avec un login spécifique |
| `make clean` | Supprime le venv et les fichiers Python compilés |
| `make fclean` | `make clean` + suppression des données utilisateur (`~/.42msg`) |
| `make re` | `make clean` + `make install` |

## Commandes

| Commande | Description |
|---|---|
| `/create <nom> <max> [password]` | Créer un salon (sans password = public) |
| `/list` | Lister les salons disponibles |
| `/join <ip> <port> <password>` | Rejoindre un salon |
| `/join <index> <password>` | Rejoindre depuis l'index `/list` |
| `/leave` | Quitter le salon |
| `/peers` | Voir les membres du salon |
| `/name <login>` | Changer son pseudo |
| `/help` | Aide |
| `/quit` | Quitter |

Tape simplement un message puis Entrée pour l'envoyer dans le salon actif.

## Principe

- N'importe qui sur le même réseau peut trouver ton salon via `/list`.
- **Salon privé** : protégé par mot de passe (par défaut).
- **Salon public** : pas de mot de passe, accessible à tous.
- Le créateur héberge le salon : s'il quitte, le salon est fermé.
- Chiffrement des clés (NaCl/Curve25519) pour l'identité.

## Prérequis réseau

- Broadcast UDP autorisé sur le réseau (pour la découverte).
- Connexion TCP entrante autorisée vers le port du créateur.
- Si le réseau bloque le direct, utilise un tunnel SSH (voir `scripts/`).
