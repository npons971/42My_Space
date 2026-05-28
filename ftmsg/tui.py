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
                placeholder="Tape un message ou une commande…",
                id="message_input",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#messages", RichLog).write(
            "[bold green]42msg prêt[/bold green] — "
            "[bold]/create[/bold] [italic]nom max password[/italic] "
            "[bold]/list[/bold] [bold]/join[/bold] "
            "[bold]/leave[/bold] [bold]/help[/bold]"
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
                sender, message, ts = self.client.incoming_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            ts_str = time.strftime("%H:%M:%S", time.localtime(ts))
            prefix = "moi" if sender == self.login else sender
            log.write(f"[green][{ts_str}] {prefix}:[/green] {message}")

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

        if content == "/help":
            log.write(
                "[bold magenta]Commandes:[/bold magenta]\n"
                "  [bold]/create <nom> <max> [password][/bold]  — créer un salon\n"
                "  [bold]/list[/bold]                          — lister les salons\n"
                "  [bold]/join <ip> <port> <password>[/bold]   — rejoindre un salon\n"
                "  [bold]/join <index> <password>[/bold]       — rejoindre depuis /list\n"
                "  [bold]/leave[/bold]                         — quitter le salon\n"
                "  [bold]/peers[/bold]                         — membres du salon\n"
                "  [bold]/name <login>[/bold]                  — changer pseudo\n"
                "  [bold]/help[/bold]                          — cette aide\n"
                "  [bold]/quit[/bold]                          — quitter\n"
                "  Tape un message puis Entrée pour l'envoyer dans le salon.",
            )
            event.input.value = ""
            return

        if content == "/list":
            channels = self.client.list_channels()
            if not channels:
                log.write(f"[magenta][{now}] Aucun salon trouvé sur le réseau[/magenta]")
            else:
                lines = [f"[magenta][{now}] Salons disponibles:[/magenta]"]
                for i, ch in enumerate(channels):
                    vis = "public" if ch.is_public else "privé"
                    lines.append(
                        f"  {i}. [bold]{ch.name}[/bold] — "
                        f"{ch.user_count}/{ch.max_users} — {vis} "
                        f"({ch.host_ip}:{ch.host_port})"
                    )
                log.write("\n".join(lines))
            event.input.value = ""
            return

        if content.startswith("/join "):
            parts = content.split(" ", 3)
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /join <ip> <port> <password> ou /join <index> <password>[/red]")
                event.input.value = ""
                return

            if parts[1].isdigit():
                idx = int(parts[1])
                password = parts[2] if len(parts) > 2 else ""
                channels = self.client.list_channels()
                if idx < 0 or idx >= len(channels):
                    log.write(f"[red][{now}] Index invalide[/red]")
                    event.input.value = ""
                    return
                ch = channels[idx]
                status = await self.client.join_channel(ch.host_ip, ch.host_port, password)
            else:
                if len(parts) < 4:
                    log.write(f"[red][{now}] usage: /join <ip> <port> <password>[/red]")
                    event.input.value = ""
                    return
                host_ip = parts[1]
                try:
                    host_port = int(parts[2])
                except ValueError:
                    log.write(f"[red][{now}] port invalide[/red]")
                    event.input.value = ""
                    return
                password = parts[3]
                status = await self.client.join_channel(host_ip, host_port, password)

            if status == "connected":
                log.write(f"[green][{now}] Connecté au salon ![/green]")
            elif status == "rejected":
                log.write(f"[red][{now}] Rejoint: {password}[/red]" if password else f"[red][{now}] Mot de passe requis[/red]")
            elif status == "connect_failed":
                log.write(f"[red][{now}] Impossible de joindre le serveur[/red]")
            elif status == "already_in_channel":
                log.write(f"[red][{now}] Déjà dans un salon[/red]")
            else:
                log.write(f"[red][{now}] Échec: {status}[/red]")
            event.input.value = ""
            return

        if content == "/leave":
            await self.client.leave_channel()
            event.input.value = ""
            return

        if content == "/peers":
            members = self.client.list_members()
            cname = self.client.current_channel_name()
            if not cname:
                log.write(f"[magenta][{now}] Tu n'es dans aucun salon[/magenta]")
            else:
                summary = ", ".join(members) if members else "(aucun)"
                log.write(
                    f"[magenta][{now}] Salon '{cname}' — "
                    f"{len(members)} membre(s):[/magenta] {summary}",
                )
            event.input.value = ""
            return

        if content.startswith("/create "):
            parts = content.split(" ", 3)
            if len(parts) < 3:
                log.write(f"[red][{now}] usage: /create <nom> <max> [password][/red]")
                event.input.value = ""
                return
            name = parts[1]
            try:
                max_users = int(parts[2])
            except ValueError:
                log.write(f"[red][{now}] max doit être un nombre[/red]")
                event.input.value = ""
                return
            password = parts[3] if len(parts) > 3 else ""
            is_public = (password == "")
            status = await self.client.create_channel(name, password, max_users, is_public)
            if status == "created":
                log.write(f"[green][{now}] Salon '{name}' créé ![/green]")
            elif status == "already_in_channel":
                log.write(f"[red][{now}] Déjà dans un salon, quitte-le d'abord[/red]")
            else:
                log.write(f"[red][{now}] Échec création: {status}[/red]")
            event.input.value = ""
            return

        if content.startswith("/name "):
            parts = content.split(" ", 1)
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /name <login>[/red]")
                event.input.value = ""
                return
            self.login = parts[1]
            self.client.login = parts[1]
            log.write(f"[yellow][{now}] Pseudo changé: {self.login}[/yellow]")
            event.input.value = ""
            return

        cname = self.client.current_channel_name()
        if not cname:
            log.write(
                f"[red][{now}][/red] Tu n'es dans aucun salon. "
                "Utilise [bold]/create[/bold] ou [bold]/join[/bold].",
            )
            event.input.value = ""
            return

        status = await self.client.send_channel_message(content)
        if status == "sent":
            pass
        elif status == "not_in_channel":
            log.write(f"[red][{now}] Tu n'es plus dans le salon[/red]")
        else:
            log.write(f"[red][{now}] Envoi impossible ({status})[/red]")
        event.input.value = ""


def run_tui(login: str | None = None) -> None:
    FtMsgApp(login=login).run()
