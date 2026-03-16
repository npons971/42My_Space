from __future__ import annotations

import time

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Input, RichLog


class FtMsgApp(App[None]):
    TITLE = "42msg"

    CSS = """
    Screen {
        layout: vertical;
    }

    #chat {
        height: 1fr;
        padding: 0 1;
    }

    #compose {
        dock: bottom;
        height: 3;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="chat"):
            yield RichLog(id="messages", wrap=True, markup=True)
        with Container(id="compose"):
            yield Input(placeholder="Écris un message puis Entrée…", id="message_input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#messages", RichLog).write("[bold green]42msg prêt[/bold green] — transport en cours d'intégration.")
        self.query_one("#message_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()
        if not content:
            return

        now = time.strftime("%H:%M:%S")
        log = self.query_one("#messages", RichLog)
        log.write(f"[cyan][{now}] moi:[/cyan] {content}")
        event.input.value = ""


def run_tui() -> None:
    FtMsgApp().run()
