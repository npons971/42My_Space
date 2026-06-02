from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class GameInvite:
    invite_id: str
    game_id: str
    game_name: str
    host_login: str
    max_players: int
    players: list[str] = field(default_factory=list)

    @property
    def player_count(self) -> int:
        return len(self.players)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invite_id": self.invite_id,
            "game_id": self.game_id,
            "game_name": self.game_name,
            "host_login": self.host_login,
            "max_players": self.max_players,
            "players": self.players,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameInvite:
        return cls(
            invite_id=data["invite_id"],
            game_id=data["game_id"],
            game_name=data["game_name"],
            host_login=data["host_login"],
            max_players=data["max_players"],
            players=list(data.get("players", [])),
        )


class BaseGameSession:
    """Base class for a game session (one running instance of a game)."""

    def __init__(self, invite: GameInvite, on_state_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.invite = invite
        self.on_state_change = on_state_change
        self.state: dict[str, Any] = {}
        self.is_active = True
        self.winner: str | None = None

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        """Process an action from a player."""
        raise NotImplementedError

    def get_render_state(self) -> dict[str, Any]:
        """Return a serializable state for UI rendering."""
        return {"active": self.is_active, "winner": self.winner, **self.state}

    def broadcast_state(self) -> None:
        if self.on_state_change:
            self.on_state_change(self.get_render_state())

    def end_game(self, winner: str | None = None) -> None:
        self.is_active = False
        self.winner = winner
        self.broadcast_state()


class BaseGame:
    """Descriptor for a game type."""

    game_id: str = ""
    name: str = ""
    description: str = ""
    min_players: int = 1
    max_players: int = 1
    is_solo: bool = True

    @classmethod
    def create_session(cls, invite: GameInvite, on_state_change: Callable[[dict[str, Any]], None] | None = None) -> BaseGameSession:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
_registry: dict[str, BaseGame] = {}


def register_game(game_class: type[BaseGame]) -> type[BaseGame]:
    _registry[game_class.game_id] = game_class()
    return game_class


def get_game(game_id: str) -> BaseGame | None:
    return _registry.get(game_id)


def list_games(solo_only: bool = False) -> list[BaseGame]:
    games = list(_registry.values())
    if solo_only:
        games = [g for g in games if g.is_solo]
    return games


def list_multiplayer_games() -> list[BaseGame]:
    return [g for g in _registry.values() if not g.is_solo]
