from __future__ import annotations

from typing import Any, Callable
import chess

from .base import BaseGame, BaseGameSession, GameInvite, register_game


class ChessSession(BaseGameSession):
    """Multiplayer Chess. 2 players required."""

    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.board = chess.Board()
        # Ensure we have exactly 2 players
        self.players = self.invite.players
        self.white_player = self.players[0] if len(self.players) > 0 else ""
        self.black_player = self.players[1] if len(self.players) > 1 else ""
        self._update_state()

    @property
    def current_player(self) -> str:
        if self.board.turn == chess.WHITE:
            return self.white_player
        else:
            return self.black_player

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active:
            return
        if action != "move":
            return
        if player != self.current_player:
            return

        move_uci = data.get("move")
        if not move_uci:
            return

        try:
            move = chess.Move.from_uci(move_uci)
            if move in self.board.legal_moves:
                self.board.push(move)
            else:
                move_q = chess.Move.from_uci(move_uci + "q")
                if move_q in self.board.legal_moves:
                    self.board.push(move_q)
                else:
                    return
        except ValueError:
            return

        outcome = self.board.outcome()
        if outcome is not None:
            if outcome.winner == chess.WHITE:
                self.end_game(winner=self.white_player)
            elif outcome.winner == chess.BLACK:
                self.end_game(winner=self.black_player)
            else:
                self.end_game(winner=None) # Draw
        else:
            self._update_state()
            self.broadcast_state()

    def get_final_score(self) -> dict[str, Any]:
        if self.winner:
            return {"winner": self.winner, "draw": False, "players": self.invite.players}
        return {"winner": None, "draw": True, "players": self.invite.players}

    def _update_state(self) -> None:
        self.state = {
            "fen": self.board.fen(),
            "current_player": self.current_player,
            "players": self.players,
            "white_player": self.white_player,
            "black_player": self.black_player,
        }

    def get_render_state(self) -> dict[str, Any]:
        return {"active": self.is_active, "winner": self.winner, **self.state}


@register_game
class ChessGame(BaseGame):
    game_id = "chess"
    name = "Chess"
    description = "Classic chess game"
    min_players = 2
    max_players = 2
    is_solo = False
    score_schema = {"wins": "Victoires", "losses": "Défaites", "draws": "Matchs nuls"}

    @classmethod
    def create_session(
        cls, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> ChessSession:
        return ChessSession(invite, on_state_change, on_score)
