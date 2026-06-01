from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import time

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, RichLog, Static

from .client import FTMessageClient, default_login

_COMMANDS = [
    "/create ", "/join ", "/list", "/leave", "/peers",
    "/msg ", "/kick ", "/ban ", "/help", "/quit",
]

_USER_COLORS = [
    "#e57373", "#81c784", "#64b5f6", "#fff176", "#ba68c8",
    "#4db6ac", "#ffb74d", "#90a4ae", "#f06292", "#7986cb",
    "#a1887f", "#ff8a65", "#4dd0e1", "#aed581", "#ffd54f",
]


def _user_color(login: str) -> str:
    idx = int(hashlib.md5(login.encode()).hexdigest(), 16) % len(_USER_COLORS)
    return _USER_COLORS[idx]


def _format_message_text(text: str) -> str:
    """Auto-format URLs and inline code in messages."""
    # Escape literal brackets so user text doesn't interfere with markup
    text = text.replace("[", "\\[").replace("]", "\\]")
    # URLs -> clickable links  (Rich uses [link=URL]text[/link])
    text = re.sub(r"(https?://[^\s<]+)", r"[link=\1]\1[/link]", text)
    # Inline code `...`
    text = re.sub(r"`([^`]+)`", r"[dim italic]`\1`[/dim italic]", text)
    return text


def os_notify(title: str, msg: str) -> None:
    try:
        subprocess.Popen(["notify-send", title, msg])
    except Exception:
        pass


class DragHandle(Static):
    """Visual handle between sidebar and chat; click to toggle sidebar."""

    DEFAULT_CSS = """
    DragHandle {
        width: 1;
        height: 1fr;
        color: $text-muted;
        background: $surface-darken-2;
        content-align: center middle;
    }
    DragHandle:hover {
        background: $primary-darken-1;
        color: $text;
    }
    """

    def __init__(self, app_ref: FtMsgApp, **kwargs) -> None:
        super().__init__("│", **kwargs)
        self.app_ref = app_ref
        self._dragging = False

    def on_mouse_down(self, event) -> None:
        self._dragging = True
        self._last_x = event.screen_x if hasattr(event, "screen_x") else event.x
        self.capture_mouse()
        event.stop()

    def on_mouse_up(self, event) -> None:
        self._dragging = False
        self.release_mouse()
        event.stop()

    def on_mouse_move(self, event) -> None:
        if self._dragging:
            cur = event.screen_x if hasattr(event, "screen_x") else event.x
            delta = cur - getattr(self, "_last_x", cur)
            if delta != 0:
                self.app_ref.sidebar_width = max(20, min(60, self.app_ref.sidebar_width + delta))
            self._last_x = cur
        event.stop()

    def on_click(self, event) -> None:
        self.app_ref.action_toggle_sidebar()
        event.stop()


class FtMsgApp(App[None]):
    TITLE = "42msg"

    BINDINGS = [
        ("ctrl+q", "quit", "Quitter"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
    ]

    sidebar_width = reactive(35)

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #main_area {
        layout: horizontal;
        height: 1fr;
    }

    #sidebar {
        width: 35;
        height: 1fr;
        border-right: solid $primary-darken-1;
        padding: 0 1;
        background: $surface-darken-1;
        layout: vertical;
    }

    #resize_handle {
        width: 1;
        height: 1fr;
        background: $surface-darken-2;
        color: $text-muted;
        content-align: center middle;
    }

    #resize_handle:hover {
        background: $primary-darken-1;
        color: $text;
    }

    #chat_area {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        background: $surface;
    }

    #chat {
        height: 1fr;
        padding: 0 1;
    }

    #compose {
        dock: bottom;
        height: auto;
        padding: 0 1;
        background: $surface-darken-1;
        border-top: solid $primary-darken-2;
    }

    #suggestions {
        height: auto;
        display: none;
        color: $text-muted;
        padding: 0 1;
        background: $surface-darken-1;
    }

    #status_box {
        padding: 1 0;
        border-bottom: solid $primary-darken-2;
    }

    #channels_box {
        padding: 1 0;
        border-bottom: solid $primary-darken-2;
    }

    #members_box {
        padding: 1 0;
    }
    """

    def __init__(self, login: str | None = None) -> None:
        super().__init__()
        self.login = login or default_login()
        self.client = FTMessageClient(self.login)
        self._has_unread = False

    # ------------------------------------------------------------------ #
    # Reactive watchers
    # ------------------------------------------------------------------ #

    def watch_sidebar_width(self, width: int) -> None:
        sidebar = self.query_one("#sidebar", Container)
        sidebar.styles.width = width

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Container)
        handle = self.query_one("#resize_handle", Static)
        if sidebar.styles.display == "none":
            sidebar.styles.display = "block"
            handle.update("│")
            self.sidebar_width = getattr(self, "_prev_sidebar_width", 35)
        else:
            self._prev_sidebar_width = self.sidebar_width
            sidebar.styles.display = "none"
            handle.update("▶")

    # ------------------------------------------------------------------ #
    # Compose & mount
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main_area"):
            with Container(id="sidebar"):
                yield Static("Chargement...", id="status_box")
                yield Static("", id="channels_box")
                yield Static("", id="members_box")
            yield DragHandle(self, id="resize_handle")
            with Container(id="chat_area"):
                with Container(id="chat"):
                    yield RichLog(id="messages", wrap=True, markup=True, highlight=True)
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

    # ------------------------------------------------------------------ #
    # Sidebar updates
    # ------------------------------------------------------------------ #

    def _update_sidebar(self) -> None:
        cname = self.client.current_channel_name()
        net_mode = "Relais" if self.client.relay_url else "Direct (P2P)"
        role = "Hote" if self.client.is_hosting else "Invite" if cname else "-"

        status_text = (
            f"[bold cyan]👤 {self.login}[/bold cyan]\n"
            f"[dim]Réseau:[/dim] {net_mode}\n"
            f"[dim]Rôle:[/dim] {role}\n"
            f"[dim]Salon:[/dim] [bold]{cname or 'Aucun'}[/bold]"
        )
        self.query_one("#status_box", Static).update(status_text)

        channels = self.client.list_channels()
        ch_text = "\n[bold magenta] Salons actifs[/bold magenta]\n"
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
                color = _user_color(m)
                marker = "●" if m == self.login else "•"
                mb_text += f"  [bold {color}]{marker} {m}[/bold {color}]\n"

        typing_users = self.client.get_typing_users()
        typing_users = [u for u in typing_users if u != self.login]
        if typing_users:
            if len(typing_users) == 1:
                mb_text += f"\n[dim italic]  {typing_users[0]} écrit...[/dim italic]"
            else:
                mb_text += f"\n[dim italic]  Plusieurs écrivent...[/dim italic]"

        self.query_one("#members_box", Static).update(mb_text)

    # ------------------------------------------------------------------ #
    # Networking / queues
    # ------------------------------------------------------------------ #

    async def _startup(self) -> None:
        await self.client.start()

    def _is_scrolled_to_bottom(self, log: RichLog) -> bool:
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
            user_col = _user_color(prefix)
            formatted = _format_message_text(message)

            if sender != self.login and message.startswith("[MP] "):
                log.write(f"[cyan][{ts_str}] [bold]{prefix}[/bold]:[/cyan] {formatted}")
                os_notify("Nouveau MP", f"{sender} t'a envoye un message prive.")
            elif sender != self.login and f"@{self.login}" in message:
                highlighted = formatted.replace(
                    f"@{self.login}",
                    f"[bold red underline]@{self.login}[/bold red underline]",
                )
                log.write(f"[green][{ts_str}] [bold]{prefix}[/bold]:[/green] {highlighted}")
                os_notify("Mention 42msg", f"{sender} t'a mentionne.")
            else:
                log.write(f"[{user_col}][{ts_str}] [bold]{prefix}[/bold]:[/{user_col}] {formatted}")

        while True:
            try:
                event = self.client.events_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            new_messages = True
            now = time.strftime("%H:%M:%S")
            log.write(f"[dim yellow][{now}] {event}[/dim yellow]")

        if new_messages:
            if at_bottom:
                log.scroll_end()
            else:
                self._has_unread = True
                self.notify("Nouveaux messages", title="42msg", severity="information")

    async def on_unmount(self) -> None:
        await self.client.stop()

    # ------------------------------------------------------------------ #
    # Input handling
    # ------------------------------------------------------------------ #

    def on_input_changed(self, event: Input.Changed) -> None:
        suggestions = self.query_one("#suggestions", Static)
        val = event.value

        if val and not val.startswith("/"):
            if self.client.current_channel_name():
                self.run_worker(self.client.send_typing_indicator())

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
