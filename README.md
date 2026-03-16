# 42msg — Messagerie P2P terminal (LAN)

## Lancer le projet

```bash
./install.sh
source ~/.zshrc
```

Ensuite:

```bash
42msg
```

Optionnel (forcer un login affiché):

```bash
42msg --login ton_login_42
```

## Utilisation dans l'interface

- Afficher les pairs en ligne:

```text
/peers
```

- Envoyer un message:

```text
/to login_cible Bonjour !
```

- Quitter:

```text
/quit
```

## Le passer à une autre personne

### Option A — via Git (recommandé)

1. Pousser ton repo sur un remote (GitHub/GitLab/42 intra).
2. L'autre personne clone le repo.
3. Chacun lance `./install.sh` sur sa machine.
4. Chacun démarre `42msg` (ou `42msg --login ...`).
5. Vérifier que vous êtes sur le même réseau local/VLAN.
6. Utiliser `/peers`, puis `/to login message`.

### Option B — sans Git

Tu peux envoyer un archive:

```bash
tar -czf 42msg.tar.gz .
```

L'autre personne décompresse, puis lance `./install.sh`.

## Notes réseau

- mDNS/zeroconf doit être autorisé sur le réseau local.
- Le pare-feu local doit autoriser les connexions TCP entrantes sur le port dynamique choisi.
- L'envoi peut passer en `pending` si la cible est hors ligne, puis partir automatiquement à son retour.

## Sécurité implémentée

- Chiffrement E2EE par message (PyNaCl/Curve25519).
- Signature cryptographique de chaque trame.
- TOFU en SQLite: `login -> clés publiques` verrouillé après première rencontre.
- Alerte si une clé change pour un login déjà connu.
