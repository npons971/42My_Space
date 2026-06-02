# Guide de contribution — Ajouter un jeu a 42msg

Ce document explique comment ajouter un nouveau jeu au systeme de salons 42msg.

## Architecture des jeux

Les jeux se trouvent dans `ftmsg/games/`. Chaque fichier represente un jeu complet (logique + rendu).

## Fichiers existants

- `base.py` — classes de base et registre
- `snake.py` — exemple **solo** (Snake)
- `tictactoe.py` — exemple **multijoueur** (Morpion)
- `wordrace.py` — exemple **multijoueur** (Course de mots)

## Regles obligatoires

Pour qu'un jeu soit reconnu par le systeme, il doit respecter **tous** les points suivants.

### 1. Heriter des classes de base

```python
from __future__ import annotations

from typing import Any, Callable
from .base import BaseGame, BaseGameSession, GameInvite, register_game
```

### 2. Implementer une `Session`

La session gere **l'etat** et la **logique** du jeu.

```python
class MonJeuSession(BaseGameSession):
    def __init__(self, invite: GameInvite, on_state_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__(invite, on_state_change)
        # Initialiser l'etat ici
        self._update_state()

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        """Traite une action envoyee par un joueur.

        - `player` : login du joueur
        - `action` : nom de l'action (ex: "move", "type", "up")
        - `data`   : dict avec les donnees supplementaires
        """
        if not self.is_active:
            return
        # ... logique du jeu ...
        self._update_state()
        self.broadcast_state()

    def _update_state(self) -> None:
        """Met a jour self.state (dict serialisable)."""
        self.state = {
            "score": 0,
            "players": self.invite.players,
        }

    def get_render_state(self) -> dict[str, Any]:
        """Retourne l'etat complet pour le rendu TUI."""
        return {"active": self.is_active, "winner": self.winner, **self.state}
```

**Methodes obligatoires a implementer :**

| Methode | Role |
|---------|------|
| `handle_action(player, action, data)` | Traiter les actions des joueurs |
| `get_render_state()` | Retourner l'etat serialisable pour l'UI |

**Methodes heritees utiles :**

| Methode | Role |
|---------|------|
| `broadcast_state()` | Envoie l'etat a tous les joueurs via `on_state_change` |
| `end_game(winner=None)` | Termine la partie, definit le gagnant |

### 3. Enregistrer le jeu avec `@register_game`

```python
@register_game
class MonJeuGame(BaseGame):
    game_id     = "monjeu"      # ID unique (snake, tictactoe, wordrace...)
    name        = "Mon Jeu"     # Nom affiche dans l'UI
    description = "Une phrase courte"
    min_players = 1
    max_players = 4
    is_solo     = False         # True = solo, False = multijoueur

    @classmethod
    def create_session(cls, invite, on_state_change=None):
        return MonJeuSession(invite, on_state_change)
```

**Champs obligatoires du descripteur :**

| Champ | Type | Description |
|-------|------|-------------|
| `game_id` | `str` | Identifiant unique (sans espace, minuscules) |
| `name` | `str` | Nom affiche dans la liste des jeux |
| `description` | `str` | Courte description (1 ligne) |
| `min_players` | `int` | Nombre minimum de joueurs |
| `max_players` | `int` | Nombre maximum de joueurs |
| `is_solo` | `bool` | `True` si le jeu est solo, `False` si multijoueur |

### 4. Importer dans le TUI

Le fichier `ftmsg/tui.py` doit importer ton jeu pour qu'il soit connu de l'interface.

Ajoute une ligne dans `ftmsg/tui.py` :

```python
from .games.monjeu import MonJeuGame
```

> **Note :** Si ton jeu a un widget Textual personnalise (comme `SnakeWidget`), importe-le aussi :
> ```python
> from .games.monjeu import MonJeuGame, MonJeuWidget
> ```

## Exemple minimal complet

```python
from __future__ import annotations
from typing import Any, Callable
from .base import BaseGame, BaseGameSession, GameInvite, register_game


class DevineNombreSession(BaseGameSession):
    """Jeu solo : deviner un nombre entre 1 et 100."""

    def __init__(self, invite, on_state_change=None):
        super().__init__(invite, on_state_change)
        self.secret = 42
        self.attempts = 0
        self._update_state()

    def handle_action(self, player, action, data):
        if not self.is_active or action != "guess":
            return
        guess = data.get("number", 0)
        self.attempts += 1
        if guess == self.secret:
            self.end_game(winner=player)
        self._update_state()
        self.broadcast_state()

    def _update_state(self):
        self.state = {"attempts": self.attempts, "secret": self.secret}

    def get_render_state(self):
        return {"active": self.is_active, "winner": self.winner, **self.state}


@register_game
class DevineNombreGame(BaseGame):
    game_id = "devinenombre"
    name = "Devine le Nombre"
    description = "Trouve le nombre secret"
    min_players = 1
    max_players = 1
    is_solo = True

    @classmethod
    def create_session(cls, invite, on_state_change=None):
        return DevineNombreSession(invite, on_state_change)
```

## Rendu TUI (optionnel mais recommandé)

Si tu veux un affichage custom dans le terminal, cree un widget Textual :

```python
from textual.widgets import Static
from textual.reactive import reactive

class MonJeuWidget(Static):
    state = reactive(dict)

    DEFAULT_CSS = """
    MonJeuWidget {
        width: auto;
        height: auto;
    }
    """

    def watch_state(self, new_state: dict[str, Any]) -> None:
        self.update(self._render(new_state))

    def _render(self, st: dict[str, Any]) -> str:
        # Retourne une string avec markup Textual ([bold], [red], etc.)
        return f"Score: {st.get('score', 0)}"
```

Puis importe ce widget dans `ftmsg/tui.py`.

## Checklist avant de proposer ton jeu

- [ ] Le fichier est dans `ftmsg/games/<nom>.py`
- [ ] La session herite de `BaseGameSession`
- [ ] Le descripteur herite de `BaseGame` et utilise `@register_game`
- [ ] Tous les champs (`game_id`, `name`, `description`, `min_players`, `max_players`, `is_solo`) sont definis
- [ ] `handle_action()` et `get_render_state()` sont implementes
- [ ] Le fichier est importe dans `ftmsg/tui.py`
- [ ] Le `game_id` est unique (verifie les jeux existants)
- [ ] Les valeurs `min_players` et `max_players` sont coherentes avec `is_solo`

## Conventions

- Noms de fichiers : `snake_case.py`
- `game_id` : minuscules, sans espace, sans accent
- Actions : minuscules (`move`, `attack`, `guess`)
- Etat : tout doit etre serialisable (pas d'objets custom dans `self.state`)
