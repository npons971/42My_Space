from __future__ import annotations

import asyncio
import time

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Input, RichLog, Static

from .client import FTMessageClient, default_login

_COMMANDS = [
    "/create ", "/join ", "/list", "/leave", "/peers",
    "/msg ", "/kick ", "/ban ", "/help", "/quit",
]


class FtMsgApp(App[None]):
    TITLE = "42msg"

    CSS = """
    Screen {
        layout: vertical;
    }

    #main_area {
        layout: horizontal;
        height: 1fr;
    }

    #sidebar {
        width: 35;
        height: 1fr;
        dock: left;
        border-right: solid $primary;
        padding: 0 1;
        layout: vertical;
    }

    #chat_area {
        height: 1fr;
        layout: vertical;
    }

    #chat {
        height: 1fr;
        padding: 0 1;
    }

    #compose {
        dock: bottom;
        height: auto;
        padding: 0 1;
    }

    #suggestions {
        height: auto;
        display: none;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, login: str | None = None) -> None:
        super().__init__()
        self.login = login or default_login()
        self.client = FTMessageClient(self.login)
        self._has_unread = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main_area"):
            with Container(id="sidebar"):
                yield Static("Chargement...", id="status_box")
                yield Static("", id="channels_box")
                yield Static("", id="members_box")
            with Container(id="chat_area"):
                with Container(id="chat"):
                    yield RichLog(id="messages", wrap=True, markup=True)
                with Container(id="compose"):
                    yield Static("", id="suggestions")
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
        self.set_interval(1.0, self._update_sidebar)

    def _update_sidebar(self) -> None:
        cname = self.client.current_channel_name()
        net_mode = "Relais" if self.client.relay_url else "Direct (P2P)"
        role = "Hôte" if self.client.is_hosting else "Invité" if cname else "-"
        
        status_text = (
            f"[bold cyan]👤 {self.login}[/bold cyan]\n"
            f"[dim]Réseau:[/dim] {net_mode}\n"
            f"[dim]Rôle:[/dim] {role}\n"
            f"[dim]Salon:[/dim] [bold]{cname or 'Aucun'}[/bold]"
        )
        self.query_one("#status_box", Static).update(status_text)
        
        channels = self.client.list_channels()
        ch_text = "\n[bold magenta]🌍 Salons actifs[/bold magenta]\n"
        if not channels:
            ch_text += "  (Aucun salon)"
        else:
            for i, ch in enumerate(channels):
                vis = "🔓" if ch.is_public else "🔒"
                ch_text += f"  [bold]{ch.name}[/bold] {vis}\n"
                ch_text += f"  └ {ch.user_count}/{ch.max_users} | /join {i}\n"
        self.query_one("#channels_box", Static).update(ch_text)
        
        mb_text = "\n[bold yellow]👥 Membres du salon[/bold yellow]\n"
        if not cname:
            mb_text += "  (Non connecté)"
        else:
            members = self.client.list_members()
            if not members:
                mb_text += "  (Vide?)"
            for m in members:
                mb_text += f"  • {m}\n"
        self.query_one("#members_box", Static).update(mb_text)

    async def _startup(self) -> None:
        await self.client.start()

    def _is_scrolled_to_bottom(self, log: RichLog) -> bool:
        # RichLog scroll area check
        try:
            return log.scroll_offset.y >= log.max_scroll_y
        except Exception:
            return True

    async def _drain_queues(self) -> None:
        log = self.query_one("#messages", RichLog)
        at_bottom = self._is_scrolled_to_bottom(log)
        new_messages = False

        while True:
            try:
                sender, message, ts = self.client.incoming_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            new_messages = True
            ts_str = time.strftime("%H:%M:%S", time.localtime(ts))
            prefix = "moi" if sender == self.login else sender
            log.write(f"[green][{ts_str}] {prefix}:[/green] {message}")

        while True:
            try:
                event = self.client.events_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            new_messages = True
            now = time.strftime("%H:%M:%S")
            log.write(f"[yellow][{now}] {event}[/yellow]")

        if new_messages:
            if at_bottom:
                log.scroll_end()
            else:
                self._has_unread = True
                self.notify("Nouveaux messages", title="42msg", severity="information")

    async def on_unmount(self) -> None:
        await self.client.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        suggestions = self.query_one("#suggestions", Static)
        val = event.value
        if val.startswith("/"):
            matches = [c for c in _COMMANDS if c.startswith(val)]
            if matches:
                suggestions.update("Suggestions: " + "  ".join(matches))
                suggestions.styles.display = "block"
                return
        suggestions.styles.display = "none"

    def on_key(self, event) -> None:
        if event.key == "tab":
            inp = self.query_one("#message_input", Input)
            val = inp.value
            if val.startswith("/"):
                matches = [c for c in _COMMANDS if c.startswith(val)]
                if matches:
                    inp.value = matches[0]
                    inp.cursor_position = len(inp.value)
                    event.stop()
                    event.prevent_default()
            self.query_one("#suggestions", Static).styles.display = "none"

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()
        if not content:
            return

        now = time.strftime("%H:%M:%S")
        log = self.query_one("#messages", RichLog)

        cmd = content.split(" ", 1)[0]

        if cmd == "/quit":
            await self.client.stop()
            self.exit()
            return

        if cmd == "/help":
            log.write(
                "[bold magenta]Commandes:[/bold magenta]\n"
                "  [bold]/create <nom> <max> [password][/bold]  — créer un salon\n"
                "  [bold]/list[/bold]                          — lister les salons\n"
                "  [bold]/join <ip> <port> <password>[/bold]   — rejoindre un salon\n"
                "  [bold]/join <index> <password>[/bold]       — rejoindre depuis /list\n"
                "  [bold]/leave[/bold]                         — quitter le salon\n"
                "  [bold]/peers[/bold]                         — membres du salon\n"
                "  [bold]/msg <login> <text>[/bold]            — message privé\n"
                "  [bold]/kick <login>[/bold]                  — expulser (hôte)\n"
                "  [bold]/ban <login>[/bold]                   — bannir (hôte)\n"
                "  [bold]/help[/bold]                          — cette aide\n"
                "  [bold]/quit[/bold]                          — quitter\n"
                "  Tape un message puis Entrée pour l'envoyer dans le salon.",
            )
            event.input.value = ""
            return

        if cmd == "/list":
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

        if cmd == "/join":
            parts = content.split(" ")
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /join <nom|ip> [port] [password] ou /join <index> [password][/red]")
                event.input.value = ""
                return

            if parts[1].isdigit() and len(parts[1]) < 4:
                idx = int(parts[1])
                password = parts[2] if len(parts) > 2 else ""
                channels = self.client.list_channels()
                if idx < 0 or idx >= len(channels):
                    log.write(f"[red][{now}] Index invalide[/red]")
                    event.input.value = ""
                    return
                ch = channels[idx]
                status, detail = await self.client.join_channel(ch.host_ip if ch.host_ip != "relay" else ch.name, ch.host_port, password)
            else:
                if self.client.relay_url:
                    # Mode relais: /join <nom_salon> [password]
                    channel_name = parts[1]
                    password = parts[2] if len(parts) > 2 else ""
                    status, detail = await self.client.join_channel(channel_name, 0, password)
                else:
                    # Mode direct: /join <ip> <port> [password]
                    if len(parts) < 3:
                        log.write(f"[red][{now}] usage: /join <ip> <port> [password][/red]")
                        event.input.value = ""
                        return
                    host_ip = parts[1]
                    try:
                        host_port = int(parts[2])
                    except ValueError:
                        log.write(f"[red][{now}] port invalide[/red]")
                        event.input.value = ""
                        return
                    password = parts[3] if len(parts) > 3 else ""
                    status, detail = await self.client.join_channel(host_ip, host_port, password)

            if status == "connected":
                log.write(f"[green][{now}] Connecté au salon ![/green]")
            elif status == "rejected":
                log.write(f"[red][{now}] Rejoint refusé: {detail}[/red]")
            elif status == "connect_failed":
                log.write(f"[red][{now}] Impossible de joindre le serveur: {detail}[/red]")
            elif status == "already_in_channel":
                log.write(f"[red][{now}] Déjà dans un salon[/red]")
            else:
                log.write(f"[red][{now}] Échec: {status} ({detail})[/red]")
            event.input.value = ""
            return

        if cmd == "/leave":
            await self.client.leave_channel()
            event.input.value = ""
            return

        if cmd == "/peers":
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

        if cmd == "/kick":
            parts = content.split(" ", 1)
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /kick <login>[/red]")
            else:
                target = parts[1]
                status = await self.client.kick_member(target)
                if status == "kicked":
                    log.write(f"[yellow][{now}] {target} expulsé[/yellow]")
                elif status == "not_hosting":
                    log.write(f"[red][{now}] Tu n'es pas l'hôte[/red]")
                else:
                    log.write(f"[red][{now}] {target} non trouvé[/red]")
            event.input.value = ""
            return

        if cmd == "/ban":
            parts = content.split(" ", 1)
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /ban <login>[/red]")
            else:
                target = parts[1]
                status = await self.client.ban_member(target)
                if status == "banned":
                    log.write(f"[yellow][{now}] {target} banni[/yellow]")
                elif status == "not_hosting":
                    log.write(f"[red][{now}] Tu n'es pas l'hôte[/red]")
                else:
                    log.write(f"[red][{now}] {target} non trouvé[/red]")
            event.input.value = ""
            return

        if cmd == "/msg":
            parts = content.split(" ", 2)
            if len(parts) < 3:
                log.write(f"[red][{now}] usage: /msg <login> <message>[/red]")
            else:
                target = parts[1]
                msg = parts[2]
                status = await self.client.send_private_message(target, msg)
                if status == "sent":
                    log.write(f"[cyan][{now}] MP à {target}: {msg}[/cyan]")
                elif status == "not_in_channel":
                    log.write(f"[red][{now}] Tu n'es dans aucun salon[/red]")
                elif status == "not_found":
                    log.write(f"[red][{now}] {target} n'est pas dans le salon[/red]")
                else:
                    log.write(f"[red][{now}] Envoi MP échoué[/red]")
            event.input.value = ""
            return

        if cmd == "/create":
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

        if cmd.startswith("/"):
            log.write(f"[red][{now}] Commande inconnue: {cmd}[/red]")
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
        elif status == "rate_limited":
            log.write(f"[yellow][{now}] Doucement ! Tu envoies des messages trop vite.[/yellow]")
        else:
            log.write(f"[red][{now}] Envoi impossible ({status})[/red]")
        event.input.value = ""


def run_tui(login: str | None = None) -> None:
    FtMsgApp(login=login).run()
