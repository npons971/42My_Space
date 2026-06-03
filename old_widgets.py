from __future__ import annotations
import math
from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical, Center
from textual.reactive import reactive
from textual.widgets import Button, Static, Label
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ftmsg.tui import FtMsgApp

class ConnectFourWidget(Container):
    state = reactive(dict)
    
    DEFAULT_CSS = """
    ConnectFourWidget { width: auto; height: auto; align: center middle; content-align: center middle; padding: 1; }
    #cf_grid { grid-size: 7 6; grid-rows: 3; grid-columns: 5; grid-gutter: 0; width: 35; height: auto; }
    #cf_grid Button { width: 5; height: 3; min-width: 5; }
    """
    
    def __init__(self, app_ref: FtMsgApp, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Static("", id="cf_status", classes="game_status")
        with Center():
            with Grid(id="cf_grid"):
                for y in range(6):
                    for x in range(7):
                        yield Button("", id=f"cf_cell_{x}_{y}", variant="default")

    def watch_state(self, st: dict):
        board = st.get("board", [[None]*7 for _ in range(6)])
        active = st.get("active", True)
        winner = st.get("winner")
        current = st.get("current_player", "")
        my_turn = current == self.app_ref.login and active and not winner
        
        status = self.query_one("#cf_status", Static)
        if winner:
            status.update(f"🎉 {winner} wins! 🎉" if winner == self.app_ref.login else f"😞 {winner} wins 😞")
        elif not active:
            status.update("🤝 Draw 🤝")
        else:
            status.update("👉 Your turn!" if my_turn else f"⏳ {current}'s turn")

        for y in range(6):
            for x in range(7):
                btn = self.query_one(f"#cf_cell_{x}_{y}", Button)
                cell = board[y][x]
                if cell:
                    player_idx = st.get("players", []).index(cell) if cell in st.get("players", []) else 0
                    symbol = st.get("symbols", ["🔴", "🟡"])[player_idx % 2]
                    btn.label = symbol
                else:
                    btn.label = ""
                btn.disabled = not my_turn

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id and event.button.id.startswith("cf_cell_"):
            parts = event.button.id.split("_")
            x = int(parts[2])
            self.app_ref.run_worker(
                self.app_ref.client.send_game_action("drop", {"col": x})
            )

class ReversiWidget(Container):
    state = reactive(dict)
    
    DEFAULT_CSS = """
    ReversiWidget { width: auto; height: auto; align: center middle; content-align: center middle; padding: 1; }
    #rev_grid { grid-size: 8 8; grid-rows: 3; grid-columns: 5; grid-gutter: 0; width: 40; height: auto; }
    #rev_grid Button { width: 5; height: 3; min-width: 5; background: green; color: white; border: solid darkgreen; }
    #rev_grid Button.valid { background: lightgreen; }
    """
    
    def __init__(self, app_ref: FtMsgApp, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Static("", id="rev_status", classes="game_status")
        yield Button("Pass Turn", id="rev_pass", variant="warning")
        with Center():
            with Grid(id="rev_grid"):
                for y in range(8):
                    for x in range(8):
                        yield Button("", id=f"rev_{x}_{y}")

    def watch_state(self, st: dict):
        board = st.get("board", [[None]*8 for _ in range(8)])
        active = st.get("active", True)
        winner = st.get("winner")
        current = st.get("current_player", "")
        valid_moves = st.get("valid_moves", [])
        my_turn = current == self.app_ref.login and active and not winner
        
        status = self.query_one("#rev_status", Static)
        scores = st.get("scores", {})
        score_txt = " - ".join([f"{p}: {s}" for p, s in scores.items()])
        
        if winner:
            status.update(f"🎉 {winner} wins! 🎉\n{score_txt}")
        elif not active:
            status.update(f"🤝 Draw 🤝\n{score_txt}")
        else:
            status.update(f"{'👉 Your turn!' if my_turn else f'⏳ {current}s turn'}\n{score_txt}")

        pass_btn = self.query_one("#rev_pass", Button)
        pass_btn.display = my_turn and len(valid_moves) == 0

        for y in range(8):
            for x in range(8):
                btn = self.query_one(f"#rev_{x}_{y}", Button)
                cell = board[y][x]
                if cell == 'B': btn.label = "⚫"
                elif cell == 'W': btn.label = "⚪"
                else: btn.label = "·" if [x, y] in valid_moves and my_turn else ""
                
                btn.disabled = not (my_turn and [x, y] in valid_moves)
                if [x,y] in valid_moves and my_turn:
                    btn.add_class("valid")
                else:
                    btn.remove_class("valid")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "rev_pass":
            self.app_ref.run_worker(self.app_ref.client.send_game_action("pass", {}))
        elif event.button.id and event.button.id.startswith("rev_"):
            parts = event.button.id.split("_")
            x, y = int(parts[1]), int(parts[2])
            self.app_ref.run_worker(
                self.app_ref.client.send_game_action("place", {"x": x, "y": y})
            )

class HangmanWidget(Container):
    state = reactive(dict)
    
    DEFAULT_CSS = """
    HangmanWidget { width: 100%; height: auto; align: center middle; content-align: center middle; padding: 1; }
    #hm_art { color: $primary; margin-bottom: 1; text-align: center; }
    #hm_word { text-style: bold; margin-bottom: 1; text-align: center; }
    #hm_keyboard { grid-size: 7 4; grid-rows: 3; grid-columns: 5; grid-gutter: 1; width: 41; height: auto; }
    #hm_keyboard Button { width: 5; height: 3; min-width: 5; }
    """
    
    STAGES = [
        "  ┌───┐\n  │    \n  │    \n  │    \n ─┴─   ",
        "  ┌───┐\n  │   O\n  │    \n  │    \n ─┴─   ",
        "  ┌───┐\n  │   O\n  │   |\n  │    \n ─┴─   ",
        "  ┌───┐\n  │   O\n  │  /|\n  │    \n ─┴─   ",
        "  ┌───┐\n  │   O\n  │  /|\\\n  │    \n ─┴─   ",
        "  ┌───┐\n  │   O\n  │  /|\\\n  │  / \n ─┴─   ",
        "  ┌───┐\n  │   O\n  │  /|\\\n  │  / \\\n ─┴─ DEAD"
    ]
    
    def __init__(self, app_ref: FtMsgApp, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Static("", id="hm_status")
        yield Static("", id="hm_art")
        yield Static("", id="hm_word")
        yield Static("Guessed: ", id="hm_guessed")
        with Center():
            with Grid(id="hm_keyboard"):
                for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                    yield Button(char, id=f"hm_key_{char}", variant="default")
        yield Button("Auto Pick Word", id="hm_auto_word")

    def watch_state(self, st: dict):
        phase = st.get("phase", "picking")
        picker = st.get("picker", "")
        guesser = st.get("guesser", "")
        word_display = st.get("word_display", [])
        guessed = st.get("guessed_letters", [])
        wrong = st.get("wrong_count", 0)
        winner = st.get("winner")
        
        art = self.query_one("#hm_art", Static)
        art.update(self.STAGES[min(wrong, 6)])
        
        word = self.query_one("#hm_word", Static)
        if winner and st.get("revealed_word"):
            word.update(" ".join(list(st.get("revealed_word").upper())))
        else:
            word.update(" ".join(word_display).upper())
            
        self.query_one("#hm_guessed", Static).update("Guessed: " + ", ".join(guessed).upper())
        
        status = self.query_one("#hm_status", Static)
        
        my_role = "picker" if picker == self.app_ref.login else "guesser" if guesser == self.app_ref.login else "spectator"
        
        if phase == "picking":
            status.update(f"⏳ Waiting for {picker} to pick a word..." if my_role != "picker" else "👉 Use /game_action set_word <word> or click Auto Pick")
            self.query_one("#hm_keyboard").display = False
            self.query_one("#hm_auto_word").display = (my_role == "picker")
        elif phase == "guessing":
            status.update(f"⏳ {guesser} is guessing..." if my_role != "guesser" else "👉 Your turn to guess!")
            self.query_one("#hm_keyboard").display = True
            self.query_one("#hm_auto_word").display = False
            for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                btn = self.query_one(f"#hm_key_{char}", Button)
                btn.disabled = char.lower() in guessed or my_role != "guesser"
        else:
            status.update(f"🎉 {winner} wins! 🎉")
            self.query_one("#hm_keyboard").display = False
            self.query_one("#hm_auto_word").display = False

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "hm_auto_word":
            import random
            word = random.choice(["python", "terminal", "network", "socket", "async", "buffer", "packet", "server", "client"])
            self.app_ref.run_worker(self.app_ref.client.send_game_action("set_word", {"word": word}))
        elif event.button.id and event.button.id.startswith("hm_key_"):
            char = event.button.id.split("_")[2].lower()
            self.app_ref.run_worker(self.app_ref.client.send_game_action("guess", {"letter": char}))

class MinesweeperWidget(Container):
    state = reactive(dict)
    
    DEFAULT_CSS = """
    MinesweeperWidget { width: auto; height: auto; align: center middle; content-align: center middle; padding: 1; }
    #ms_grid { grid-size: 10 10; grid-rows: 3; grid-columns: 4; grid-gutter: 0; width: 40; height: auto; }
    #ms_grid Button { width: 4; height: 3; min-width: 4; }
    #ms_grid Button.revealed { background: $surface-darken-1; color: $text; border: none; }
    """
    
    def __init__(self, app_ref: FtMsgApp, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref
        self.flag_mode = False

    def compose(self) -> ComposeResult:
        yield Static("", id="ms_status")
        yield Button("Mode: ⛏️ Reveal", id="ms_toggle_mode", variant="primary")
        with Center():
            with Grid(id="ms_grid"):
                for y in range(10):
                    for x in range(10):
                        yield Button("", id=f"ms_{x}_{y}")

    def watch_state(self, st: dict):
        board = st.get("display_board", [[None]*10 for _ in range(10)])
        active = st.get("active", True)
        winner = st.get("winner")
        current = st.get("current_player", "")
        my_turn = current == self.app_ref.login and active and not winner
        scores = st.get("scores", {})
        
        status = self.query_one("#ms_status", Static)
        score_str = " - ".join([f"{p}: {s}" for p,s in scores.items()])
        
        if winner:
            status.update(f"🎉 {winner} wins! 🎉\n{score_str}")
        elif not active:
            status.update(f"🤝 Draw 🤝\n{score_str}")
        else:
            status.update(f"{'👉 Your turn!' if my_turn else f'⏳ {current}s turn'}\n{score_str}")

        for y in range(10):
            for x in range(10):
                btn = self.query_one(f"#ms_{x}_{y}", Button)
                cell = board[y][x]
                if cell is None:
                    btn.label = ""
                    btn.remove_class("revealed")
                    btn.disabled = not my_turn
                elif cell == 'F':
                    btn.label = "🚩"
                    btn.remove_class("revealed")
                    btn.disabled = not my_turn
                elif cell == 'M':
                    btn.label = "💣"
                    btn.add_class("revealed")
                    btn.disabled = True
                else:
                    btn.label = str(cell) if cell > 0 else " "
                    btn.add_class("revealed")
                    btn.disabled = True

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "ms_toggle_mode":
            self.flag_mode = not self.flag_mode
            event.button.label = "Mode: 🚩 Flag" if self.flag_mode else "Mode: ⛏️ Reveal"
            event.button.variant = "warning" if self.flag_mode else "primary"
        elif event.button.id and event.button.id.startswith("ms_"):
            parts = event.button.id.split("_")
            x, y = int(parts[1]), int(parts[2])
            action = "flag" if self.flag_mode else "reveal"
            self.app_ref.run_worker(
                self.app_ref.client.send_game_action(action, {"x": x, "y": y})
            )

class BattleshipWidget(Container):
    state = reactive(dict)
    
    DEFAULT_CSS = """
    BattleshipWidget { width: auto; height: auto; align: center middle; }
    .bs_boards { layout: horizontal; align: center middle; height: auto; width: auto; }
    .bs_board_container { margin: 1; align: center middle; height: auto; width: auto; }
    .bs_grid { grid-size: 10 10; grid-rows: 3; grid-columns: 4; grid-gutter: 0; width: 40; height: auto; }
    .bs_grid Button { width: 4; height: 3; min-width: 4; }
    """
    
    def __init__(self, app_ref: FtMsgApp, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Static("Bataille Navale", id="bs_status", classes="game_status")
        with Horizontal(id="bs_setup_controls"):
            yield Button("Auto Place", id="bs_auto")
            yield Button("Ready", id="bs_ready", variant="success")
        with Center():
            with Horizontal(classes="bs_boards"):
                with Vertical(classes="bs_board_container"):
                    yield Static("Votre flotte", classes="bs_label")
                    with Grid(id="bs_grid_own", classes="bs_grid"):
                        for y in range(10):
                            for x in range(10):
                                yield Button("", id=f"bso_{x}_{y}", variant="default", classes="bs_cell")
                
                with Vertical(classes="bs_board_container"):
                    yield Static("Tirs ennemis", classes="bs_label")
                    with Grid(id="bs_grid_attack", classes="bs_grid"):
                        for y in range(10):
                            for x in range(10):
                                yield Button("", id=f"bse_{x}_{y}", variant="default", classes="bs_cell")

    def watch_state(self, st: dict):
        phase = st.get("phase", "setup")
        winner = st.get("winner")
        current = st.get("current_player", "")
        my_turn = current == self.app_ref.login and phase == "playing"
        
        status = self.query_one("#bs_status", Static)
        if winner:
            status.update(f"🎉 {winner} wins! 🎉")
        elif phase == "setup":
            status.update("⚙️ Setup Phase - Place your ships!")
        elif phase == "playing":
            status.update("👉 Your turn to shoot!" if my_turn else f"⏳ {current} is shooting...")
            
        setup_controls = self.query_one("#bs_setup_controls")
        setup_controls.display = (phase == "setup")
        
        boards = st.get("boards", {}).get(self.app_ref.login, {})
        my_board = boards.get("own", [[None]*10 for _ in range(10)])
        op_board = boards.get("opponent", [[None]*10 for _ in range(10)])
        
        for y in range(10):
            for x in range(10):
                my_btn = self.query_one(f"#bso_{x}_{y}", Button)
                cell = my_board[y][x]
                my_btn.label = "🚢" if cell == "S" else "💥" if cell == "H" else "🌊" if cell == "M" else "💀" if cell == "K" else ""
                my_btn.disabled = True # Can't click own board right now, auto-place only
                
                op_btn = self.query_one(f"#bse_{x}_{y}", Button)
                cell = op_board[y][x]
                op_btn.label = "💥" if cell == "H" else "🌊" if cell == "M" else "💀" if cell == "K" else ""
                op_btn.disabled = not (my_turn and cell is None)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "bs_auto":
            self.app_ref.run_worker(self.app_ref.client.send_game_action("auto_place", {}))
        elif event.button.id == "bs_ready":
            self.app_ref.run_worker(self.app_ref.client.send_game_action("ready", {}))
        elif event.button.id and event.button.id.startswith("bse_"):
            parts = event.button.id.split("_")
            x, y = int(parts[1]), int(parts[2])
            self.app_ref.run_worker(self.app_ref.client.send_game_action("shoot", {"x": x, "y": y}))
