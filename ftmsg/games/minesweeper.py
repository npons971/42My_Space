from __future__ import annotations

import random
from typing import Any, Callable

from .base import BaseGame, BaseGameSession, GameInvite, register_game

BOARD_SIZE = 10
MINE_COUNT = 15
TOTAL_SAFE = BOARD_SIZE * BOARD_SIZE - MINE_COUNT


class MinesweeperSession(BaseGameSession):
    """Competitive Minesweeper Race – two players, shared board, turn-based."""

    def __init__(
        self,
        invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        # Internal mine map: True = mine. Filled after first reveal.
        self._mine_map: list[list[bool]] = [
            [False] * BOARD_SIZE for _ in range(BOARD_SIZE)
        ]
        self._mines_placed = False
        # Precomputed neighbor counts (filled after mine placement).
        self._counts: list[list[int]] = [
            [0] * BOARD_SIZE for _ in range(BOARD_SIZE)
        ]
        # Per-cell reveal state: None = hidden, player login = revealed by whom.
        self._revealed_by: list[list[str | None]] = [
            [None] * BOARD_SIZE for _ in range(BOARD_SIZE)
        ]
        # Per-cell flag state.
        self._flags: list[list[bool]] = [
            [False] * BOARD_SIZE for _ in range(BOARD_SIZE)
        ]
        self._scores: dict[str, int] = {p: 0 for p in invite.players}
        self.current_turn_idx = 0
        self._game_over = False
        self._loser: str | None = None
        self._update_state()

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #
    @property
    def current_player(self) -> str:
        players = self.invite.players
        if not players:
            return ""
        return players[self.current_turn_idx % len(players)]

    # ------------------------------------------------------------------ #
    # Mine placement (deferred until first reveal)
    # ------------------------------------------------------------------ #
    def _place_mines(self, safe_x: int, safe_y: int) -> None:
        """Place MINE_COUNT mines, excluding (safe_x, safe_y) and its neighbors."""
        excluded: set[tuple[int, int]] = set()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = safe_x + dx, safe_y + dy
                if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE:
                    excluded.add((nx, ny))

        candidates = [
            (x, y)
            for x in range(BOARD_SIZE)
            for y in range(BOARD_SIZE)
            if (x, y) not in excluded
        ]
        mines = random.sample(candidates, min(MINE_COUNT, len(candidates)))
        for x, y in mines:
            self._mine_map[y][x] = True

        # Precompute neighbor counts.
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                self._counts[y][x] = self._count_adjacent(x, y)

        self._mines_placed = True

    def _count_adjacent(self, x: int, y: int) -> int:
        total = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE:
                    if self._mine_map[ny][nx]:
                        total += 1
        return total

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active:
            return
        if action == "flag":
            self._handle_flag(player, data)
            return
        if action != "reveal":
            return
        if player != self.current_player:
            return

        x = data.get("x", -1)
        y = data.get("y", -1)
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return
        if self._revealed_by[y][x] is not None:
            return
        if self._flags[y][x]:
            return  # must unflag first

        # Place mines on first reveal.
        if not self._mines_placed:
            self._place_mines(x, y)

        # Check for mine hit.
        if self._mine_map[y][x]:
            self._game_over = True
            self._loser = player
            # Determine winner: the OTHER player.
            others = [p for p in self.invite.players if p != player]
            winner = others[0] if others else None
            self._update_state()
            self.end_game(winner=winner)
            return

        # Safe reveal (possibly flood-fill).
        self._flood_reveal(x, y, player)

        # Check if all safe cells have been revealed.
        total_revealed = sum(
            1
            for row in self._revealed_by
            for cell in row
            if cell is not None
        )
        if total_revealed >= TOTAL_SAFE:
            self._game_over = True
            # Winner is whoever revealed more cells.
            p1, p2 = self.invite.players
            if self._scores[p1] > self._scores[p2]:
                winner = p1
            elif self._scores[p2] > self._scores[p1]:
                winner = p2
            else:
                winner = None  # draw
            self._update_state()
            self.end_game(winner=winner)
            return

        self.current_turn_idx += 1
        self._update_state()
        self.broadcast_state()

    def _handle_flag(self, player: str, data: dict[str, Any]) -> None:
        """Toggle a flag (cosmetic). Any player can flag on anyone's turn."""
        x = data.get("x", -1)
        y = data.get("y", -1)
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return
        if self._revealed_by[y][x] is not None:
            return  # can't flag a revealed cell
        self._flags[y][x] = not self._flags[y][x]
        self._update_state()
        self.broadcast_state()

    def _flood_reveal(self, x: int, y: int, player: str) -> None:
        """Reveal (x, y) and flood-fill if count is 0."""
        stack = [(x, y)]
        while stack:
            cx, cy = stack.pop()
            if not (0 <= cx < BOARD_SIZE and 0 <= cy < BOARD_SIZE):
                continue
            if self._revealed_by[cy][cx] is not None:
                continue
            if self._mine_map[cy][cx]:
                continue
            self._revealed_by[cy][cx] = player
            self._flags[cy][cx] = False  # clear flag on reveal
            self._scores[player] = self._scores.get(player, 0) + 1
            if self._counts[cy][cx] == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((cx + dx, cy + dy))

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #
    def _build_visible_board(self) -> list[list[Any]]:
        """Build the board as seen by clients."""
        board: list[list[Any]] = [[None] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                if self._revealed_by[y][x] is not None:
                    board[y][x] = self._counts[y][x]
                elif self._flags[y][x]:
                    board[y][x] = "F"
                else:
                    board[y][x] = None
        # When game is over, show mines.
        if self._game_over:
            for y in range(BOARD_SIZE):
                for x in range(BOARD_SIZE):
                    if self._mine_map[y][x] and self._revealed_by[y][x] is None:
                        board[y][x] = "M"
        return board

    def _get_mine_positions(self) -> list[list[int]]:
        return [
            [x, y]
            for y in range(BOARD_SIZE)
            for x in range(BOARD_SIZE)
            if self._mine_map[y][x]
        ]

    def _update_state(self) -> None:
        self.state = {
            "board": self._build_visible_board(),
            "revealed_by": [row[:] for row in self._revealed_by],
            "current_player": self.current_player,
            "players": self.invite.players,
            "scores": dict(self._scores),
            "game_phase": "finished" if self._game_over else "playing",
        }
        # Only expose mine positions after game over.
        if self._game_over:
            self.state["mines"] = self._get_mine_positions()

    def get_render_state(self) -> dict[str, Any]:
        return {"active": self.is_active, "winner": self.winner, **self.state}

    def get_final_score(self) -> dict[str, Any]:
        return {
            "winner": self.winner,
            "loser": self._loser,
            "scores": dict(self._scores),
            "players": self.invite.players,
        }


@register_game
class MinesweeperGame(BaseGame):
    game_id = "minesweeper"
    name = "Minesweeper Race"
    description = "Competitive minesweeper – reveal cells without hitting a mine!"
    min_players = 2
    max_players = 2
    is_solo = False
    score_schema = {"wins": "Victoires", "losses": "Défaites"}

    @classmethod
    def create_session(
        cls,
        invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> MinesweeperSession:
        return MinesweeperSession(invite, on_state_change, on_score)
