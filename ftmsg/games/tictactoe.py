from __future__ import annotations

from typing import Any, Callable

from .base import BaseGame, BaseGameSession, GameInvite, register_game


class TicTacToeSession(BaseGameSession):
    """Multiplayer tic-tac-toe. 2 players required."""

    SYMBOLS = ["X", "O"]

    def __init__(self, invite: GameInvite, on_state_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__(invite, on_state_change)
        self.board: list[list[str | None]] = [[None for _ in range(3)] for _ in range(3)]
        self.current_turn_idx = 0
        self._update_state()

    @property
    def current_player(self) -> str:
        players = self.invite.players
        if not players:
            return ""
        return players[self.current_turn_idx % len(players)]

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active:
            return
        if action != "move":
            return
        if player != self.current_player:
            return
        x = data.get("x", -1)
        y = data.get("y", -1)
        if not (0 <= x < 3 and 0 <= y < 3):
            return
        if self.board[y][x] is not None:
            return
        sym = self.SYMBOLS[self.invite.players.index(player) % len(self.SYMBOLS)]
        self.board[y][x] = sym
        if self._check_winner(sym):
            self._update_state()
            self.end_game(winner=player)
            return
        if self._is_draw():
            self._update_state()
            self.end_game(winner=None)
            return
        self.current_turn_idx += 1
        self._update_state()
        self.broadcast_state()

    def _check_winner(self, sym: str) -> bool:
        b = self.board
        for i in range(3):
            if all(b[i][j] == sym for j in range(3)):
                return True
            if all(b[j][i] == sym for j in range(3)):
                return True
        if all(b[i][i] == sym for i in range(3)):
            return True
        if all(b[i][2 - i] == sym for i in range(3)):
            return True
        return False

    def _is_draw(self) -> bool:
        return all(cell is not None for row in self.board for cell in row)

    def _update_state(self) -> None:
        self.state = {
            "board": self.board,
            "current_player": self.current_player,
            "players": self.invite.players,
            "symbols": self.SYMBOLS,
        }

    def get_render_state(self) -> dict[str, Any]:
        return {"active": self.is_active, "winner": self.winner, **self.state}


@register_game
class TicTacToeGame(BaseGame):
    game_id = "tictactoe"
    name = "Tic-Tac-Toe"
    description = "Align 3 symbols to win"
    min_players = 2
    max_players = 2
    is_solo = False

    @classmethod
    def create_session(cls, invite: GameInvite, on_state_change: Callable[[dict[str, Any]], None] | None = None) -> TicTacToeSession:
        return TicTacToeSession(invite, on_state_change)
