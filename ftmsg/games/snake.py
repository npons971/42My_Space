from __future__ import annotations

import random
from typing import Any, Callable

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Button, Static

from .base import BaseGame, BaseGameSession, GameInvite, register_game


class SnakeSession(BaseGameSession):
    """A solo snake game session rendered as a grid."""

    GRID_W = 20
    GRID_H = 12

    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.snake: list[tuple[int, int]] = [(5, 5), (4, 5), (3, 5)]
        self.direction: tuple[int, int] = (1, 0)
        self.next_direction: tuple[int, int] = (1, 0)
        self.food = self._spawn_food()
        self.score = 0
        self.game_over = False
        self.tick_count = 0
        self._update_state()

    def _spawn_food(self) -> tuple[int, int]:
        while True:
            x = random.randint(0, self.GRID_W - 1)
            y = random.randint(0, self.GRID_H - 1)
            if (x, y) not in self.snake:
                return (x, y)

    def _update_state(self) -> None:
        self.state = {
            "grid_w": self.GRID_W,
            "grid_h": self.GRID_H,
            "snake": self.snake,
            "food": self.food,
            "score": self.score,
            "game_over": self.game_over,
            "tick": self.tick_count,
        }

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if self.game_over:
            return
        # Actions: up, down, left, right
        dirs = {
            "up": (0, -1),
            "down": (0, 1),
            "left": (-1, 0),
            "right": (1, 0),
        }
        if action in dirs:
            nd = dirs[action]
            # Prevent reversing directly
            if (nd[0] * -1, nd[1] * -1) != self.direction:
                self.next_direction = nd

    def tick(self) -> None:
        if self.game_over:
            return
        self.tick_count += 1
        self.direction = self.next_direction
        head = (self.snake[0][0] + self.direction[0], self.snake[0][1] + self.direction[1])

        # Wall collision
        if head[0] < 0 or head[0] >= self.GRID_W or head[1] < 0 or head[1] >= self.GRID_H:
            self.game_over = True
            self.end_game(winner=None)
            self._update_state()
            return

        # Self collision
        if head in self.snake:
            self.game_over = True
            self.end_game(winner=None)
            self._update_state()
            return

        self.snake.insert(0, head)
        if head == self.food:
            self.score += 10
            self.food = self._spawn_food()
        else:
            self.snake.pop()

        self._update_state()
        self.broadcast_state()

    def get_final_score(self) -> dict[str, Any]:
        return {"score": self.score, "length": len(self.snake), "best_score": self.score}

    def get_render_state(self) -> dict[str, Any]:
        return {"active": self.is_active, "winner": self.winner, **self.state}


@register_game
class SnakeGame(BaseGame):
    game_id = "snake"
    name = "Snake"
    description = "Eat, grow, don't crash"
    min_players = 1
    max_players = 1
    is_solo = True
    score_schema = {"score": "Points", "length": "Taille", "best_score": "Meilleur score"}

    @classmethod
    def create_session(
        cls, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> SnakeSession:
        return SnakeSession(invite, on_state_change, on_score)


# --------------------------------------------------------------------------- #
# UI Widget
# --------------------------------------------------------------------------- #
class SnakeWidget(Static):
    """Textual widget that renders a SnakeSession state with polished visuals.

    Each game cell is drawn as 2 character columns wide to compensate for
    the fact that terminal cells are roughly 2x taller than they are wide,
    ensuring the snake moves at the same visual speed in both directions.
    """

    state = reactive(dict)
    CELL_W = 2  # character columns per logical game cell

    DEFAULT_CSS = """
    SnakeWidget {
        width: auto;
        height: auto;
        content-align: center middle;
        color: $text;
        padding: 1 2;
    }
    """

    def watch_state(self, new_state: dict[str, Any]) -> None:
        # Fix the widget size explicitly so Textual can centre it correctly
        gw = new_state.get("grid_w", 20)
        gh = new_state.get("grid_h", 12)
        grid_width = gw * self.CELL_W
        # +2 for left/right borders; +4 horizontal padding (1+1 from CSS + 2 extra)
        self.styles.width = grid_width + 2 + 4
        self.styles.height = gh + 6  # borders + header + footer
        self.update(self._format_state(new_state))

    def _cell(self, char: str, width: int = 0) -> str:
        """Repeat a char to fill the cell width."""
        w = width or self.CELL_W
        return char * w

    def _format_state(self, st: dict[str, Any]) -> str:
        if not st:
            return ""
        gw = st.get("grid_w", 20)
        gh = st.get("grid_h", 12)
        snake_raw = st.get("snake", [])
        snake = [tuple(p) for p in snake_raw]
        food = tuple(st.get("food", (0, 0)))
        score = st.get("score", 0)
        game_over = st.get("game_over", False)
        head = tuple(snake_raw[0]) if snake_raw else None

        lines: list[str] = []
        grid_width = gw * self.CELL_W

        # Header
        header_text = f"S N A K E     Score {score}".center(grid_width)
        lines.append(f"[bold yellow]{header_text}[/bold yellow]")
        lines.append("")

        # Top border (simple box-drawing, doubled horizontally)
        lines.append(f"[dim]┌{self._cell('─', grid_width)}┐[/dim]")

        for y in range(gh):
            row = "[dim]│[/dim]"
            for x in range(gw):
                pos = (x, y)
                if pos == food:
                    row += f"[bold red]{self._cell('◉')}[/bold red]"
                elif pos == head:
                    row += f"[bold green]{self._cell('▣')}[/bold green]"
                elif pos in snake:
                    row += f"[green]{self._cell('█')}[/green]"
                else:
                    row += self._cell(" ")
            row += "[dim]│[/dim]"
            lines.append(row)

        # Bottom border
        lines.append(f"[dim]└{self._cell('─', grid_width)}┘[/dim]")
        lines.append("")

        if game_over:
            over_text = "G A M E   O V E R".center(grid_width)
            restart_text = "Appuie sur R pour recommencer".center(grid_width)
            lines.append(f"[bold red]{over_text}[/bold red]")
            lines.append(f"[dim]{restart_text}[/dim]")
        else:
            hint = "Fleches pour bouger".center(grid_width)
            lines.append(f"[dim]{hint}[/dim]")

        return "\n".join(lines)
