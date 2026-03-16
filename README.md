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

## Debug mDNS en direct (2 machines)

Si vous ne vous voyez pas dans `/peers`, lancez ce test en même temps sur les 2 machines:

Machine A:

```bash
.venv/bin/python live_mdns_check.py --login login_A --duration 60
```

Machine B:

```bash
.venv/bin/python live_mdns_check.py --login login_B --duration 60
```

Résultat attendu: chaque machine doit afficher des lignes `ONLINE login=...` et des snapshots avec l'autre login.
Si les 2 scripts affichent `RESULT: no peer discovered`, c'est presque toujours un filtrage multicast/mDNS sur le réseau (VLAN/switch/ACL).

## Fallback sans mDNS: liaison manuelle

Si le réseau bloque mDNS, vous pouvez forcer la liaison pair à pair.

1. Lancez `42msg --login login_A` et `42msg --login login_B`.
2. Dans chaque TUI, notez la ligne `Node prêt: <login> écoute sur <port>`.
3. Faites un lien initial depuis A vers B:

```text
/link login_B 10.12.x.y PORT_B
```

4. Puis (optionnel mais conseillé) depuis B vers A:

```text
/link login_A 10.12.x.z PORT_A
```

5. Vérifiez avec `/peers`, puis envoyez:

```text
/to login_B Salut
```

Le handshake `/link` est signé, met à jour le TOFU local, et permet ensuite l'envoi chiffré normal.

## Sécurité implémentée

- Chiffrement E2EE par message (PyNaCl/Curve25519).
- Signature cryptographique de chaque trame.
- TOFU en SQLite: `login -> clés publiques` verrouillé après première rencontre.
- Alerte si une clé change pour un login déjà connu.
