from __future__ import annotations

import random
from typing import Any, Callable

from textual.app import ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Static

from .base import BaseGame, BaseGameSession, GameInvite, register_game


class SnakeSession(BaseGameSession):
    """A solo snake game session rendered as a grid."""

    GRID_W = 40
    GRID_H = 24

    PHASE_STARTING = 0
    PHASE_PLAYING = 1
    PHASE_GAME_OVER = 2

    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.phase = self.PHASE_STARTING
        self.start_timer = 30  # 30 ticks for countdown
        self.snake: list[tuple[int, int]] = [(10, 12), (9, 12), (8, 12), (7, 12)]
        self.direction: tuple[int, int] = (1, 0)
        self.move_queue: list[tuple[int, int]] = []
        self.food = self._spawn_food()
        self.score = 0
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
            "phase": self.phase,
            "start_timer": self.start_timer,
            "tick": self.tick_count,
        }

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if action == "restart" and self.phase == self.PHASE_GAME_OVER:
            self.phase = self.PHASE_STARTING
            self.start_timer = 30
            self.snake = [(10, 12), (9, 12), (8, 12), (7, 12)]
            self.direction = (1, 0)
            self.move_queue = []
            self.food = self._spawn_food()
            self.score = 0
            self.is_active = True
            self.winner = None
            self._update_state()
            self.broadcast_state()
            return

        if self.phase != self.PHASE_PLAYING:
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
            last_dir = self.move_queue[-1] if self.move_queue else self.direction
            # Prevent reversing directly and ignore redundant moves
            if (nd[0] * -1, nd[1] * -1) != last_dir and nd != last_dir:
                if len(self.move_queue) < 3:
                    self.move_queue.append(nd)

    def tick(self) -> None:
        self.tick_count += 1
        
        if self.phase == self.PHASE_STARTING:
            self.start_timer -= 1
            if self.start_timer <= 0:
                self.phase = self.PHASE_PLAYING
            self._update_state()
            self.broadcast_state()
            return
            
        if self.phase == self.PHASE_GAME_OVER:
            return

        if self.move_queue:
            self.direction = self.move_queue.pop(0)
            
        head = (self.snake[0][0] + self.direction[0], self.snake[0][1] + self.direction[1])

        # Self collision or wall collision (let's do wall collision for extra challenge!)
        # Wait, previous was wrap around. Let's keep wrap around but smaller pixels.
        head = (head[0] % self.GRID_W, head[1] % self.GRID_H)

        if head in self.snake:
            self.phase = self.PHASE_GAME_OVER
            self.end_game(winner=None)
            self._update_state()
            self.broadcast_state()
            return

        self.snake.insert(0, head)
        if head == self.food:
            self.score += 10
            if len(self.snake) == self.GRID_W * self.GRID_H:
                self.phase = self.PHASE_GAME_OVER
                self.end_game(winner=None)
            else:
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
class SnakeWidget(Container):
    """Textual widget that renders a SnakeSession state using Canvas."""

    state = reactive(dict)

    DEFAULT_CSS = """
    SnakeWidget {
        width: auto;
        height: auto;
        align: center middle;
        layout: vertical;
        padding: 1 2;
    }
    #snake_canvas {
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="snake_header")
        from textual_canvas import Canvas
        yield Canvas(80, 48, id="snake_canvas")
        yield Static("", id="snake_footer")

    def watch_state(self, new_state: dict[str, Any]) -> None:
        if not new_state:
            return
        
        score = new_state.get("score", 0)
        phase = new_state.get("phase", 0)
        
        self.query_one("#snake_header", Static).update(f"[bold yellow]S N A K E     Score {score}[/bold yellow]")
        
        footer = self.query_one("#snake_footer", Static)
        if phase == 2: # PHASE_GAME_OVER
            footer.update("[bold red]G A M E   O V E R[/bold red]\n[dim]Appuie sur R pour recommencer[/dim]")
        elif phase == 0: # PHASE_STARTING
            footer.update("[dim]Prépare-toi...[/dim]")
        else:
            footer.update("[dim]Flèches pour bouger[/dim]")

        from textual_canvas import Canvas
        from textual.color import Color
        from .canvas_utils import draw_text
        
        canvas = self.query_one("#snake_canvas", Canvas)
        canvas.clear()
        
        snake_raw = new_state.get("snake", [])
        if not snake_raw:
            return
            
        snake = [tuple(p) for p in snake_raw]
        food = tuple(new_state.get("food", (0, 0)))
        head = snake[0]

        # Draw food (2x2 pixel)
        fx, fy = food
        canvas.draw_rectangle(fx * 2, fy * 2, 2, 2, Color.parse("red"))

        # Draw snake body with gradient effect
        # The head is bright lime, the tail is darker green
        snake_len = len(snake)
        for i, pos in enumerate(snake):
            sx, sy = pos
            if i == 0:
                color = Color.parse("lime")
            else:
                # Calculate a fade factor from 0.0 to 0.7
                fade = 0.7 * (i / snake_len)
                color = Color.parse("lime").darken(fade)
            
            # Draw connecting segments for a smoother look
            # Instead of just a 2x2 box, we draw a 2x2 box for each coordinate
            # We can also draw a line to the next segment to cover gaps if we had any,
            # but since they move cell by cell, 2x2 boxes are contiguous.
            canvas.draw_rectangle(sx * 2, sy * 2, 2, 2, color)

        # Draw Overlay (Screens)
        if phase == 0: # PHASE_STARTING
            timer = new_state.get("start_timer", 30)
            # 30 ticks = 3 seconds. Show 3, 2, 1, GO
            if timer > 20:
                text = "3"
            elif timer > 10:
                text = "2"
            elif timer > 0:
                text = "1"
            else:
                text = "GO"
            
            # Center the text approximately (each letter is ~4px wide, 5px high)
            text_w = len(text) * 4
            text_x = (80 - text_w) // 2
            text_y = (48 - 5) // 2
            
            # Draw background box for text to make it readable
            canvas.draw_rectangle(text_x - 4, text_y - 4, text_w + 6, 13, Color.parse("black").with_alpha(0.8))
            draw_text(canvas, text_x, text_y, text, Color.parse("white"))

        elif phase == 2: # PHASE_GAME_OVER
            text1 = "GAME"
            text2 = "OVER"
            
            w1 = len(text1) * 4
            w2 = len(text2) * 4
            
            x1 = (80 - w1) // 2
            x2 = (80 - w2) // 2
            
            y1 = (48 - 15) // 2
            y2 = y1 + 7
            
            # Darken the whole screen
            for y in range(48):
                canvas.draw_line(0, y, 79, y, Color.parse("black").with_alpha(0.5))
                
            draw_text(canvas, x1, y1, text1, Color.parse("red"))
            draw_text(canvas, x2, y2, text2, Color.parse("red"))
