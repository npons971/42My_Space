from __future__ import annotations

import asyncio
import time

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Input, RichLog

from .client import FTMessageClient, default_login


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

    def __init__(self, login: str | None = None) -> None:
        super().__init__()
        self.login = login or default_login()
        self.client = FTMessageClient(self.login)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="chat"):
            yield RichLog(id="messages", wrap=True, markup=True)
        with Container(id="compose"):
            yield Input(
                placeholder="Écris un message puis Entrée…",
                id="message_input",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#messages", RichLog).write(
            "[bold green]42msg prêt[/bold green] — commande: "
            "[bold]/to login message[/bold], "
            "[bold]/peers[/bold], [bold]/link login ip port[/bold], "
            "[bold]/quit[/bold]"
        )
        self.query_one("#message_input", Input).focus()
        self.run_worker(self._startup())
        self.set_interval(0.2, self._drain_queues)

    async def _startup(self) -> None:
        await self.client.start()

    async def _drain_queues(self) -> None:
        log = self.query_one("#messages", RichLog)

        while True:
            try:
                sender, message = self.client.incoming_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            now = time.strftime("%H:%M:%S")
            log.write(f"[green][{now}] {sender}:[/green] {message}")

        while True:
            try:
                event = self.client.events_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            now = time.strftime("%H:%M:%S")
            log.write(f"[yellow][{now}] {event}[/yellow]")

    async def on_unmount(self) -> None:
        await self.client.stop()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()
        if not content:
            return

        now = time.strftime("%H:%M:%S")
        log = self.query_one("#messages", RichLog)
        if content == "/quit":
            await self.client.stop()
            self.exit()
            return

        if content == "/peers":
            peers = self.client.list_online_peers()
            if peers:
                summary = ", ".join(sorted(peers.keys()))
                log.write(
                    f"[magenta][{now}] peers online:[/magenta] {summary}",
                )
            else:
                log.write(f"[magenta][{now}] peers online:[/magenta] aucun")
            event.input.value = ""
            return

        if content.startswith("/to "):
            parts = content.split(" ", 2)
            if len(parts) < 3:
                log.write(f"[red][{now}] usage:[/red] /to login message")
                event.input.value = ""
                return
            target_login, message = parts[1], parts[2]
            status = await self.client.send_message(target_login, message)
            if status == "sent":
                log.write(
                    f"[cyan][{now}] moi -> {target_login}:[/cyan] {message}",
                )
            elif status == "pending":
                log.write(
                    f"[cyan][{now}] moi -> {target_login}:[/cyan] "
                    f"{message} [yellow](pending)[/yellow]",
                )
            else:
                log.write(
                    "[red]"
                    f"[{now}] impossible d'envoyer à {target_login} "
                    "(clé inconnue)[/red]",
                )
            event.input.value = ""
            return

        if content.startswith("/link "):
            parts = content.split(" ")
            if len(parts) != 4:
                log.write(
                    f"[red][{now}] usage:[/red] /link login ip port",
                )
                event.input.value = ""
                return
            target_login, target_ip = parts[1], parts[2]
            try:
                target_port = int(parts[3])
            except ValueError:
                log.write(
                    f"[red][{now}] port invalide[/red]",
                )
                event.input.value = ""
                return

            status = await self.client.link_peer(
                target_login,
                target_ip,
                target_port,
            )
            if status == "link_sent":
                log.write(
                    f"[yellow][{now}] HELLO envoyé à "
                    f"{target_login} ({target_ip}:{target_port})[/yellow]",
                )
            elif status == "connect_failed":
                log.write(
                    f"[red][{now}] impossible de joindre "
                    f"{target_ip}:{target_port}[/red]",
                )
            else:
                log.write(
                    f"[red][{now}] client non prêt[/red]",
                )
            event.input.value = ""
            return

        log.write(
            f"[red][{now}] commande invalide[/red] "
            "utilise /to, /peers, /link ou /quit",
        )
        event.input.value = ""


def run_tui(login: str | None = None) -> None:
    FtMsgApp(login=login).run()
