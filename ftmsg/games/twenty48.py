from __future__ import annotations

import random
from typing import Any, Callable

from textual.app import ComposeResult
from textual.containers import Container, Center
from textual.reactive import reactive
from textual.widgets import Static

from .base import BaseGame, BaseGameSession, GameInvite, register_game

class Twenty48Session(BaseGameSession):
    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.grid = [[0] * 4 for _ in range(4)]
        self.score = 0
        self.is_active = True
        self.winner = None
        self._spawn_tile()
        self._spawn_tile()
        self._update_state()

    def _spawn_tile(self) -> bool:
        empty = [(r, c) for r in range(4) for c in range(4) if self.grid[r][c] == 0]
        if not empty:
            return False
        r, c = random.choice(empty)
        self.grid[r][c] = 2 if random.random() < 0.9 else 4
        return True

    def _update_state(self) -> None:
        self.state = {
            "grid": self.grid,
            "score": self.score,
        }

    def _check_game_over(self) -> bool:
        for r in range(4):
            for c in range(4):
                if self.grid[r][c] == 0:
                    return False
                if c < 3 and self.grid[r][c] == self.grid[r][c+1]:
                    return False
                if r < 3 and self.grid[r][c] == self.grid[r+1][c]:
                    return False
        return True

    def _move(self, direction: str) -> bool:
        # up, down, left, right
        moved = False
        def slide_and_merge(row: list[int]) -> list[int]:
            nonlocal moved
            # remove zeros
            new_row = [x for x in row if x != 0]
            # merge
            merged_row = []
            skip = False
            for i in range(len(new_row)):
                if skip:
                    skip = False
                    continue
                if i < len(new_row) - 1 and new_row[i] == new_row[i+1]:
                    merged_row.append(new_row[i] * 2)
                    self.score += new_row[i] * 2
                    skip = True
                    moved = True
                else:
                    merged_row.append(new_row[i])
            # pad with zeros
            while len(merged_row) < 4:
                merged_row.append(0)
            if merged_row != row:
                moved = True
            return merged_row

        if direction == "left":
            for r in range(4):
                self.grid[r] = slide_and_merge(self.grid[r])
        elif direction == "right":
            for r in range(4):
                self.grid[r] = slide_and_merge(self.grid[r][::-1])[::-1]
        elif direction == "up":
            for c in range(4):
                col = [self.grid[r][c] for r in range(4)]
                new_col = slide_and_merge(col)
                for r in range(4):
                    self.grid[r][c] = new_col[r]
        elif direction == "down":
            for c in range(4):
                col = [self.grid[r][c] for r in range(4)][::-1]
                new_col = slide_and_merge(col)[::-1]
                for r in range(4):
                    self.grid[r][c] = new_col[r]
        return moved

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if action == "restart" and not self.is_active:
            self.__init__(self.invite, self.on_state_change, self.on_score)
            self.broadcast_state()
            return
            
        if not self.is_active:
            return

        if action in ["up", "down", "left", "right"]:
            moved = self._move(action)
            if moved:
                self._spawn_tile()
                if self._check_game_over():
                    self.end_game(winner=None)
                self._update_state()
                self.broadcast_state()

    def get_final_score(self) -> dict[str, Any]:
        return {"best_score": self.score, "games_played": 1}

@register_game
class Twenty48Game(BaseGame):
    game_id = "twenty48"
    name = "2048"
    description = "Join the numbers and get to the 2048 tile!"
    min_players = 1
    max_players = 1
    is_solo = True
    score_schema = {"best_score": "Meilleur score", "games_played": "Parties jouées"}

    @classmethod
    def create_session(
        cls, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> Twenty48Session:
        return Twenty48Session(invite, on_state_change, on_score)

class Twenty48Widget(Container):
    state = reactive(dict)

    DEFAULT_CSS = """
    Twenty48Widget {
        width: 100%;
        height: auto;
        padding: 1 2;
        align: center middle;
    }
    #t48_header, #t48_footer {
        content-align: center middle;
        width: 100%;
    }
    #t48_grid {
        grid-size: 4 4;
        grid-rows: 4;
        grid-columns: 8;
        grid-gutter: 1 2;
        width: 40;
        height: 21;
        margin: 1 0;
        padding: 0;
        border: thick $primary;
        background: $surface;
    }
    .t48_cell {
        width: 100%;
        height: 100%;
        content-align: center middle;
        border: solid $surface-lighten-3;
        background: $panel;
        color: $text;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="t48_header")
        with Center():
            from textual.containers import Grid
            with Grid(id="t48_grid"):
                for r in range(4):
                    for c in range(4):
                        yield Static("", id=f"t48_cell_{r}_{c}", classes="t48_cell")
        yield Static("", id="t48_footer")

    def watch_state(self, new_state: dict[str, Any]) -> None:
        if not new_state:
            return
            
        score = new_state.get("score", 0)
        grid = new_state.get("grid", [[0]*4 for _ in range(4)])
        active = new_state.get("active", True)
        
        self.query_one("#t48_header", Static).update(f"[bold yellow]2048     Score: {score}[/bold yellow]")
        
        footer = self.query_one("#t48_footer", Static)
        if not active:
            footer.update("[bold red]G A M E   O V E R[/bold red]\\n[dim]Appuie sur R pour recommencer[/dim]")
        else:
            footer.update("[dim]Flèches pour bouger[/dim]")
            
        colors = {
            0: ("", ""),
            2: ("[bold #776e65]", "#eee4da"),
            4: ("[bold #776e65]", "#ede0c8"),
            8: ("[bold #f9f6f2]", "#f2b179"),
            16: ("[bold #f9f6f2]", "#f59563"),
            32: ("[bold #f9f6f2]", "#f67c5f"),
            64: ("[bold #f9f6f2]", "#f65e3b"),
            128: ("[bold #f9f6f2]", "#edcf72"),
            256: ("[bold #f9f6f2]", "#edcc61"),
            512: ("[bold #f9f6f2]", "#edc850"),
            1024: ("[bold #f9f6f2]", "#edc53f"),
            2048: ("[bold #f9f6f2]", "#edc22e"),
        }
            
        for r in range(4):
            for c in range(4):
                val = grid[r][c]
                cell = self.query_one(f"#t48_cell_{r}_{c}", Static)
                if val == 0:
                    cell.update("")
                    cell.styles.background = "#cdc1b4"
                else:
                    style, bg = colors.get(val, ("[bold #f9f6f2]", "#3c3a32"))
                    cell.update(f"{style}{val}[/]")
                    cell.styles.background = bg
