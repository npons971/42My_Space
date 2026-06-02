from __future__ import annotations

from typing import Any, Callable

from .base import BaseGame, BaseGameSession, GameInvite, register_game

# Directions: all 8 adjacencies (dx, dy)
_DIRECTIONS = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]


class ReversiSession(BaseGameSession):
    """Multiplayer Reversi (Othello). 2 players, 8×8 board."""

    SYMBOLS = ["⚫", "⚪"]
    COLORS = ["B", "W"]

    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.board: list[list[str | None]] = [
            [None for _ in range(8)] for _ in range(8)
        ]
        # Standard initial position: centre 4 squares
        self.board[3][3] = "W"
        self.board[3][4] = "B"
        self.board[4][3] = "B"
        self.board[4][4] = "W"
        self.current_turn_idx = 0  # Black (player 0) goes first
        self.passed_last = False
        self._update_state()

    # -- helpers ------------------------------------------------------------- #

    @property
    def current_player(self) -> str:
        players = self.invite.players
        if not players:
            return ""
        return players[self.current_turn_idx % len(players)]

    def _color_of(self, player: str) -> str:
        idx = self.invite.players.index(player)
        return self.COLORS[idx % len(self.COLORS)]

    @staticmethod
    def _opponent_color(color: str) -> str:
        return "W" if color == "B" else "B"

    def _get_flips(self, x: int, y: int, color: str) -> list[list[int]]:
        """Return positions that would be flipped by placing *color* at (x, y)."""
        if self.board[y][x] is not None:
            return []
        opp = self._opponent_color(color)
        flips: list[list[int]] = []
        for dx, dy in _DIRECTIONS:
            path: list[list[int]] = []
            nx, ny = x + dx, y + dy
            while 0 <= nx < 8 and 0 <= ny < 8 and self.board[ny][nx] == opp:
                path.append([nx, ny])
                nx += dx
                ny += dy
            # Valid only if the path ends on our own colour
            if path and 0 <= nx < 8 and 0 <= ny < 8 and self.board[ny][nx] == color:
                flips.extend(path)
        return flips

    def _get_valid_moves(self, color: str) -> list[list[int]]:
        """Return all [x, y] positions where *color* can legally play."""
        moves: list[list[int]] = []
        for y in range(8):
            for x in range(8):
                if self._get_flips(x, y, color):
                    moves.append([x, y])
        return moves

    def _apply_place(self, x: int, y: int, color: str) -> None:
        """Place a disc and flip all captured opponent pieces."""
        flips = self._get_flips(x, y, color)
        self.board[y][x] = color
        for fx, fy in flips:
            self.board[fy][fx] = color

    def _count_discs(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for player, color in zip(self.invite.players, self.COLORS):
            counts[player] = sum(
                1 for row in self.board for cell in row if cell == color
            )
        return counts

    # -- actions ------------------------------------------------------------- #

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active:
            return
        if player != self.current_player:
            return

        color = self._color_of(player)

        if action == "place":
            x = data.get("x", -1)
            y = data.get("y", -1)
            if not (0 <= x < 8 and 0 <= y < 8):
                return
            if not self._get_flips(x, y, color):
                return  # illegal move
            self._apply_place(x, y, color)
            self.passed_last = False
            self.current_turn_idx += 1
            self._check_end_or_skip()
            return

        if action == "pass":
            # Only allowed when the player truly has no moves
            if self._get_valid_moves(color):
                return
            if self.passed_last:
                # Two consecutive passes → game over
                self._finish()
                return
            self.passed_last = True
            self.current_turn_idx += 1
            self._check_end_or_skip()
            return

    def _check_end_or_skip(self) -> None:
        """After a move or pass, see if the game is over or the next player
        must also pass (which ends the game)."""
        next_color = self._color_of(self.current_player)
        if self._get_valid_moves(next_color):
            # Next player has moves – normal continuation
            self._update_state()
            self.broadcast_state()
            return

        # Next player has no moves
        if self.passed_last:
            # Previous player also passed (or had no moves) → game over
            self._finish()
            return

        # Auto-pass for the next player
        self.passed_last = True
        self.current_turn_idx += 1

        next_color = self._color_of(self.current_player)
        if not self._get_valid_moves(next_color):
            # Neither player can move → game over
            self._finish()
            return

        self._update_state()
        self.broadcast_state()

    def _finish(self) -> None:
        scores = self._count_discs()
        self._update_state()
        players = self.invite.players
        s0, s1 = scores[players[0]], scores[players[1]]
        if s0 > s1:
            self.end_game(winner=players[0])
        elif s1 > s0:
            self.end_game(winner=players[1])
        else:
            self.end_game(winner=None)

    # -- state --------------------------------------------------------------- #

    def _update_state(self) -> None:
        color = self._color_of(self.current_player) if self.invite.players else "B"
        self.state = {
            "board": self.board,
            "current_player": self.current_player,
            "players": self.invite.players,
            "symbols": self.SYMBOLS,
            "valid_moves": self._get_valid_moves(color),
            "scores": self._count_discs(),
            "passed_last": self.passed_last,
        }

    def get_render_state(self) -> dict[str, Any]:
        return {"active": self.is_active, "winner": self.winner, **self.state}

    def get_final_score(self) -> dict[str, Any]:
        if self.winner:
            return {"winner": self.winner, "draw": False, "players": self.invite.players}
        return {"winner": None, "draw": True, "players": self.invite.players}


@register_game
class ReversiGame(BaseGame):
    game_id = "reversi"
    name = "Reversi"
    description = "Flip your opponent's discs to dominate the board"
    min_players = 2
    max_players = 2
    is_solo = False
    score_schema = {"wins": "Victoires", "losses": "Défaites", "draws": "Matchs nuls"}

    @classmethod
    def create_session(
        cls, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> ReversiSession:
        return ReversiSession(invite, on_state_change, on_score)
