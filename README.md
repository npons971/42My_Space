# 42msg — Messagerie par salons (LAN)

Chat en réseau local par salons. Découverte par broadcast UDP, messages
sur TCP. Pas de serveur central — chaque salon est hébergé par son créateur.

## Installation en une ligne (curl)

```bash
curl -fsSL https://raw.githubusercontent.com/npons971/42My_Space/master/install.sh | bash
```

> Si le curl échoue (repo privé, pas d'accès Internet, etc.), utilise l'[installation manuelle](#installation-manuelle) ci-dessous.

Puis relance ton shell ou source ton rc :

```bash
source ~/.zshrc   # ou ~/.bashrc
```

Lance l'application :

```bash
42msg
```

Avec un pseudo spécifique :

```bash
42msg --login mon_login
```

---

## Installation manuelle

Si le curl ne fonctionne pas, clone le dépôt et installe localement :

```bash
git clone https://github.com/npons971/42My_Space.git ~/.local/share/42msg
cd ~/.local/share/42msg
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p ~/.local/bin
cat > ~/.local/bin/42msg <<'EOF'
#!/usr/bin/env bash
exec "$HOME/.local/share/42msg/.venv/bin/python" -m ftmsg "$@"
EOF
chmod +x ~/.local/bin/42msg
export PATH="$HOME/.local/bin:$PATH"
```

---

## Installation manuelle (git + make)

```bash
git clone https://github.com/npons971/42My_Space.git
cd 42My_Space
make install   # crée le venv, installe les deps localement, configure l'alias
source ~/.zshrc
42msg
```

## Gestion du venv

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
| `make uninstall` | Supprime le venv, les données utilisateur et retire l'alias de `~/.zshrc` |

## Commandes

| Commande | Description |
|---|---|
| `/create <nom> <max> [password] [campus]` | Créer un salon (sans password = public). Ajoute `campus` pour restreindre au même réseau WiFi. |
| `/list` | Lister les salons disponibles |
| `/join <ip> <port> <password>` | Rejoindre un salon |
| `/join <index> <password>` | Rejoindre depuis l'index `/list` |
| `/leave` | Quitter le salon |
| `/peers` | Voir les membres du salon |
| `/msg <login> <text>` | Message privé |
| `/kick <login>` | Expulser (hôte) |
| `/ban <login>` | Bannir (hôte) |
| `/settings` | Paramètres (Ctrl+S) |
| `/help` | Aide |
| `/quit` | Quitter |

Tape simplement un message puis Entrée pour l'envoyer dans le salon actif.

## Raccourcis clavier

| Raccourci | Action |
|---|---|
| `Ctrl+Q` | Quitter |
| `Ctrl+B` | Afficher/masquer la sidebar |
| `Ctrl+S` | Ouvrir les paramètres |
| `Tab` | Autocomplétion des commandes |

## Principe

- N'importe qui sur le même réseau peut trouver ton salon via `/list`.
- **Salon privé** : protégé par mot de passe (par défaut).
- **Salon public** : pas de mot de passe, accessible à tous.
- **Salon campus** : restreint au même sous-réseau IP (/24) que le créateur. Même s'il connaît l'IP et le port, quelqu'un en dehors du WiFi ne peut pas joindre.
- Le créateur héberge le salon : s'il quitte, le salon est fermé.
- Chiffrement de bout en bout des messages via clés NaCl/Curve25519 (mode Direct & Relais).

## Prérequis réseau

- Broadcast UDP autorisé sur le réseau (pour la découverte en mode Direct).
- Connexion TCP entrante autorisée vers le port du créateur (mode Direct).
- Si le réseau bloque le direct, utilise le mode relais (`--relay`).
