import re

new_class = """class HangmanWidget(Container):
    state = reactive(dict)

    DEFAULT_CSS = \"\"\"
    HangmanWidget { width: 100%; height: auto; align: center middle; content-align: center middle; padding: 1; }
    #hm_art { color: $primary; margin-bottom: 1; text-align: center; }
    #hm_word { text-style: bold; margin-bottom: 1; text-align: center; }
    #hm_keyboard { width: auto; height: auto; margin-top: 1; }
    #hm_input { width: 40; }
    \"\"\"

    STAGES = [
        "  ┌───┐\\n  │    \\n  │    \\n  │    \\n ─┴─   ",
        "  ┌───┐\\n  │   O\\n  │    \\n  │    \\n ─┴─   ",
        "  ┌───┐\\n  │   O\\n  │   |\\n  │    \\n ─┴─   ",
        "  ┌───┐\\n  │   O\\n  │  /|\\n  │    \\n ─┴─   ",
        "  ┌───┐\\n  │   O\\n  │  /|\\\\\\n  │    \\n ─┴─   ",
        "  ┌───┐\\n  │   O\\n  │  /|\\\\\\n  │  / \\n ─┴─   ",
        "  ┌───┐\\n  │   O\\n  │  /|\\\\\\n  │  / \\\\\\n ─┴─ DEAD"
    ]

    def __init__(self, app_ref: "FtMsgApp", **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Static("", id="hm_status")
        yield Static("", id="hm_art")
        yield Static("", id="hm_word")
        yield Static("Guessed: ", id="hm_guessed")
        from textual.widgets import Input
        with Center(id="hm_keyboard"):
            yield Input(placeholder="Type a letter and press Enter...", id="hm_input")
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
            from textual.widgets import Input
            inp = self.query_one("#hm_input", Input)
            inp.disabled = my_role != "guesser"
            if my_role == "guesser":
                inp.focus()
        else:
            status.update(f"🎉 {winner} wins! 🎉")
            self.query_one("#hm_keyboard").display = False
            self.query_one("#hm_auto_word").display = False

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "hm_auto_word":
            import random
            word = random.choice(["python", "terminal", "network", "socket", "async", "buffer", "packet", "server", "client"])
            self.app_ref.run_worker(self.app_ref.client.send_game_action("set_word", {"word": word}))

    from textual import on
    from textual.widgets import Input
    @on(Input.Submitted, "#hm_input")
    def on_hm_input_submitted(self, event: Input.Submitted):
        char = event.value.strip()
        if char and len(char) > 0:
            char = char[0].lower()
            self.app_ref.run_worker(self.app_ref.client.send_game_action("guess", {"letter": char}))
        event.input.value = ""
"""

with open("ftmsg/games/widgets.py", "r") as f:
    content = f.read()

match = re.search(r"^class HangmanWidget\(Container\):.*?(?=^class |\Z)", content, re.MULTILINE | re.DOTALL)
if match:
    content = content[:match.start()] + new_class + content[match.end():]
    with open("ftmsg/games/widgets.py", "w") as f:
        f.write(content)
