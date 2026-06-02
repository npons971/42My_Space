from __future__ import annotations

import random
import string
from typing import Any, Callable

from .base import BaseGame, BaseGameSession, GameInvite, register_game


class WordRaceSession(BaseGameSession):
    """Multiplayer word race: first to type the displayed word wins the round.
    Best of N rounds (default 5). 2–4 players.
    """

    WORDS = [
        "python", "terminal", "network", "socket", "async", "buffer",
        "packet", "server", "client", "peer", "relay", "crypto",
        "channel", "message", "signal", "thread", "process",
        "rendezvous", "discovery", "broadcast", "campus", "login",
        "password", "encrypt", "decrypt", "handshake", "protocol",
    ]
    ROUNDS = 5

    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.scores: dict[str, int] = {p: 0 for p in invite.players}
        self.round = 0
        self.current_word = ""
        self.round_winner: str | None = None
        self._next_round()

    def _next_round(self) -> None:
        self.round += 1
        self.current_word = random.choice(self.WORDS)
        self.round_winner = None
        self._update_state()
        self.broadcast_state()

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active:
            return
        if action != "type":
            return
        if self.round_winner:
            return  # Round already won
        typed = data.get("word", "").strip().lower()
        if typed == self.current_word.lower():
            self.round_winner = player
            self.scores[player] = self.scores.get(player, 0) + 1
            # Check for game winner
            max_score = max(self.scores.values()) if self.scores else 0
            rounds_needed = (self.ROUNDS // 2) + 1
            if max_score >= rounds_needed or self.round >= self.ROUNDS:
                winner = max(self.scores, key=lambda k: self.scores[k]) if self.scores else None
                # handle tie
                top_score = self.scores.get(winner, 0) if winner else 0
                tied = [p for p, s in self.scores.items() if s == top_score]
                final_winner = winner if len(tied) == 1 else None
                self._update_state()
                self.end_game(winner=final_winner)
                return
            # Start next round after brief moment
            self._update_state()
            self.broadcast_state()
            # In real impl, delay could be handled by TUI; here we just broadcast and let host trigger next round
            # via an action or auto after state change. We'll let the UI handle a small delay.

    def next_round_action(self) -> None:
        if self.is_active:
            self._next_round()

    def get_final_score(self) -> dict[str, Any]:
        return {"scores": dict(self.scores), "winner": self.winner, "rounds_played": self.round}

    def _update_state(self) -> None:
        self.state = {
            "round": self.round,
            "total_rounds": self.ROUNDS,
            "current_word": self.current_word,
            "scores": self.scores,
            "round_winner": self.round_winner,
            "players": self.invite.players,
        }

    def get_render_state(self) -> dict[str, Any]:
        return {"active": self.is_active, "winner": self.winner, **self.state}


@register_game
class WordRaceGame(BaseGame):
    game_id = "wordrace"
    name = "Word Race"
    description = "Type the word faster!"
    min_players = 2
    max_players = 4
    is_solo = False
    score_schema = {"wins": "Victoires", "rounds_won": "Rounds gagnés", "games_played": "Parties jouées"}

    @classmethod
    def create_session(
        cls, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> WordRaceSession:
        return WordRaceSession(invite, on_state_change, on_score)
