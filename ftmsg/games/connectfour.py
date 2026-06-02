from __future__ import annotations

from typing import Any, Callable

from .base import BaseGame, BaseGameSession, GameInvite, register_game

ROWS = 6
COLS = 7
WIN_LEN = 4


class ConnectFourSession(BaseGameSession):
    """Multiplayer Connect Four. 2 players required."""

    SYMBOLS = ["🔴", "🟡"]

    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.board: list[list[str | None]] = [
            [None for _ in range(COLS)] for _ in range(ROWS)
        ]
        self.current_turn_idx = 0
        self.last_drop: dict[str, int] | None = None
        self._update_state()

    # ---------------------------------------------------------------------- #
    # Turn management
    # ---------------------------------------------------------------------- #
    @property
    def current_player(self) -> str:
        players = self.invite.players
        if not players:
            return ""
        return players[self.current_turn_idx % len(players)]

    # ---------------------------------------------------------------------- #
    # Action handler
    # ---------------------------------------------------------------------- #
    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active:
            return
        if action != "drop":
            return
        if player != self.current_player:
            return

        col = data.get("col", -1)
        if not (0 <= col < COLS):
            return

        # Find lowest empty row in the column (row 5 is bottom)
        row = self._lowest_empty_row(col)
        if row is None:
            return  # column full

        # Place the token (store player login, not symbol)
        self.board[row][col] = player
        self.last_drop = {"col": col, "row": row}

        # Check win
        if self._check_winner(player, row, col):
            self._update_state()
            self.end_game(winner=player)
            return

        # Check draw
        if self._is_full():
            self._update_state()
            self.end_game(winner=None)
            return

        # Next turn
        self.current_turn_idx += 1
        self._update_state()
        self.broadcast_state()

    # ---------------------------------------------------------------------- #
    # Board helpers
    # ---------------------------------------------------------------------- #
    def _lowest_empty_row(self, col: int) -> int | None:
        """Return the lowest (highest index) empty row in *col*, or None."""
        for row in range(ROWS - 1, -1, -1):
            if self.board[row][col] is None:
                return row
        return None

    def _is_full(self) -> bool:
        return all(cell is not None for row in self.board for cell in row)

    # ---------------------------------------------------------------------- #
    # Win detection
    # ---------------------------------------------------------------------- #
    _DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]  # horiz, vert, diag ↘, diag ↗

    def _check_winner(self, player: str, row: int, col: int) -> bool:
        """Check whether the last drop at (row, col) creates 4-in-a-row."""
        for dr, dc in self._DIRECTIONS:
            count = 1
            # Forward
            r, c = row + dr, col + dc
            while 0 <= r < ROWS and 0 <= c < COLS and self.board[r][c] == player:
                count += 1
                r += dr
                c += dc
            # Backward
            r, c = row - dr, col - dc
            while 0 <= r < ROWS and 0 <= c < COLS and self.board[r][c] == player:
                count += 1
                r -= dr
                c -= dc
            if count >= WIN_LEN:
                return True
        return False

    # ---------------------------------------------------------------------- #
    # State
    # ---------------------------------------------------------------------- #
    def _update_state(self) -> None:
        self.state = {
            "board": self.board,
            "current_player": self.current_player,
            "players": self.invite.players,
            "symbols": self.SYMBOLS,
            "last_drop": self.last_drop,
        }

    def get_render_state(self) -> dict[str, Any]:
        return {"active": self.is_active, "winner": self.winner, **self.state}

    def get_final_score(self) -> dict[str, Any]:
        if self.winner:
            return {"winner": self.winner, "draw": False, "players": self.invite.players}
        return {"winner": None, "draw": True, "players": self.invite.players}


@register_game
class ConnectFourGame(BaseGame):
    game_id = "connectfour"
    name = "Connect Four"
    description = "Drop tokens to align 4 in a row"
    min_players = 2
    max_players = 2
    is_solo = False
    score_schema = {"wins": "Victoires", "losses": "Défaites", "draws": "Matchs nuls"}

    @classmethod
    def create_session(
        cls, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> ConnectFourSession:
        return ConnectFourSession(invite, on_state_change, on_score)
