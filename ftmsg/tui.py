from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import subprocess
import time
from typing import Any, Coroutine, Callable

try:
    import pyperclip
except ImportError:
    pyperclip = None

from textual.app import App, ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll, ScrollableContainer, Center
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Header, Input, RichLog, Static, TextArea, Label, ListItem, ListView, OptionList, TabbedContent, TabPane
from textual.widgets import DirectoryTree
from pathlib import Path
from rich.text import Text as RichText

from .client import FTMessageClient, default_login
from .games.base import GameInvite, list_games, list_multiplayer_games, get_game
from .games.snake import SnakeWidget, SnakeGame
from .games.tictactoe import TicTacToeGame
from .games.wordrace import WordRaceGame
from .games.chess import ChessGame
from .games.connectfour import ConnectFourGame

from .games.battleship import BattleshipGame
from .games.hangman import HangmanGame
from .games.twenty48 import Twenty48Game, Twenty48Widget

from .games.widgets import ConnectFourWidget, BattleshipWidget, HangmanWidget

_COMMANDS = [
    "/create ", "/join ", "/list", "/leave", "/peers",
    "/msg ", "/kick ", "/ban ", "/settings", "/help", "/quit",
    "/games", "/game_start ", "/game_join ", "/game_leave",
    "/score", "/score ", "/leaderboard ", "/profile", "/profile ",
    "/sendfile ",
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


class DragHandle(Static):
    """Visual handle between sidebar and chat; click to toggle sidebar."""


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


class SettingsScreen(ModalScreen):
    BINDINGS = [
        ("ctrl+s", "close", "Fermer"),
    ]


    def compose(self) -> ComposeResult:
        with Container(id="settings_container"):
            yield Static("⚙️ Paramètres", classes="settings-title")
            yield Static("", id="settings_identity", classes="settings-row")
            yield Static("", id="settings_network", classes="settings-row")
            yield Static("", id="settings_channel", classes="settings-row")
            yield Static("", id="settings_storage", classes="settings-row")
            yield Static("", id="settings_prefs", classes="settings-row")
            yield Static("", id="settings_shortcuts", classes="settings-row")
            yield Button("Fermer", id="settings_close", variant="primary")
            yield Button("", id="settings_toggle_notif", variant="default")

    def on_mount(self) -> None:
        self.update_content()

    def update_content(self) -> None:
        app = self.app
        assert isinstance(app, FtMsgApp)
        client = app.client

        enc_fp = hashlib.sha256(bytes(client.enc_public_key)).hexdigest()[:16] if client.enc_public_key else "N/A"
        sign_fp = hashlib.sha256(bytes(client.sign_public_key)).hexdigest()[:16] if client.sign_public_key else "N/A"
        identity = (
            f"[bold cyan]Identité[/bold cyan]\n"
            f"  Login: [bold]{client.login}[/bold]\n"
            f"  Clé chiffrement: {enc_fp}\n"
            f"  Clé signature: {sign_fp}"
        )
        self.query_one("#settings_identity", Static).update(identity)

        mode = "Relais" if client.relay_url else "Direct (P2P)"
        local_ip = client.local_ip or client._resolve_local_ip()
        discovery_active = client.discovery is not None and getattr(client.discovery, "_running", False)
        network = (
            f"[bold cyan]Réseau[/bold cyan]\n"
            f"  Mode: [bold]{mode}[/bold]\n"
            f"  IP locale: {local_ip}\n"
            f"  Découverte: {'Active' if discovery_active else 'Inactive'}\n"
            f"  Relay URL: {client.relay_url or 'Non configuré'}"
        )
        self.query_one("#settings_network", Static).update(network)

        cname = client.current_channel_name()
        role = "Hôte" if client.is_hosting else "Invité" if cname else "-"
        if not cname:
            encryption = "Aucun salon actif"
        elif client.room_key:
            encryption = "Actif 🔒 (clé de salon établie)"
        else:
            encryption = "En attente de clé..."
        members = len(client.list_members())
        # Show campus-only flag from the server if we are hosting
        campus_flag = ""
        if client.is_hosting and client.channel_server and client.channel_server.campus_only:
            campus_flag = "\n  Campus: 🏫 Oui"
        channel = (
            f"[bold cyan]Salon actif[/bold cyan]\n"
            f"  Nom: [bold]{cname or 'Aucun'}[/bold]\n"
            f"  Rôle: {role}\n"
            f"  Chiffrement: {encryption}\n"
            f"  Membres: {members}{campus_flag}"
        )
        self.query_one("#settings_channel", Static).update(channel)

        db_path = str(client.db_path)
        db_size = 0
        try:
            db_size = os.path.getsize(db_path)
        except OSError:
            pass
        size_str = f"{db_size} o"
        if db_size > 1024:
            size_str = f"{db_size / 1024:.1f} Ko"
        if db_size > 1024 * 1024:
            size_str = f"{db_size / (1024 * 1024):.1f} Mo"
        storage = (
            f"[bold cyan]Stockage[/bold cyan]\n"
            f"  DB: {db_path}\n"
            f"  Taille: {size_str}"
        )
        self.query_one("#settings_storage", Static).update(storage)

        notif_state = "ON ✅" if app.desktop_notifications else "OFF ❌"
        prefs = (
            f"[bold cyan]Préférences[/bold cyan]\n"
            f"  Notifications desktop: {notif_state}"
        )
        self.query_one("#settings_prefs", Static).update(prefs)
        notif_btn = self.query_one("#settings_toggle_notif", Button)
        notif_btn.label = "Désactiver notifications" if app.desktop_notifications else "Activer notifications"

        shortcuts = (
            f"[bold cyan]Raccourcis[/bold cyan]\n"
            f"  [bold]Ctrl+Q[/bold] Quitter\n"
            f"  [bold]Ctrl+B[/bold] Sidebar\n"
            f"  [bold]Ctrl+S[/bold] Paramètres\n"
            f"  [bold]Ctrl+E[/bold] Copier historique\n"
            f"  [bold]Tab[/bold]    Autocomplétion\n"
        )
        self.query_one("#settings_shortcuts", Static).update(shortcuts)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings_close":
            self.dismiss()
        elif event.button.id == "settings_toggle_notif":
            self.app.desktop_notifications = not self.app.desktop_notifications
            self.update_content()

    def action_close(self) -> None:
        self.dismiss()


class CustomFooter(Horizontal):
    """Stylized footer with centered action buttons."""


    def compose(self) -> ComposeResult:
        yield Static("", id="footer_spacer_left")
        yield Button("Quitter [dim]Ctrl+Q[/dim]", id="footer_quit", variant="error")
        yield Button("Sidebar [dim]Ctrl+B[/dim]", id="footer_sidebar", variant="primary")
        yield Button("Jeux [dim]Ctrl+G[/dim]", id="footer_games", variant="success")
        yield Button("Paramètres [dim]Ctrl+S[/dim]", id="footer_settings", variant="default")
        yield Static("", id="footer_spacer_right")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = self.app
        assert isinstance(app, FtMsgApp)
        if event.button.id == "footer_quit":
            app.exit()
        elif event.button.id == "footer_sidebar":
            app.action_toggle_sidebar()
        elif event.button.id == "footer_games":
            app.action_toggle_games()
        elif event.button.id == "footer_settings":
            app.action_toggle_settings()


class ChatLog(RichLog):
    """RichLog qui garde une copie texte brut de tout ce qui est affiche."""

    def __init__(self, app_ref: FtMsgApp, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def write(self, *args, **kwargs) -> None:
        result = super().write(*args, **kwargs)
        if args:
            content = args[0]
            try:
                if isinstance(content, str):
                    plain = RichText.from_markup(content).plain
                elif isinstance(content, RichText):
                    plain = content.plain
                else:
                    plain = str(content)
                self.app_ref._chat_history.append(plain)
                if len(self.app_ref._chat_history) > 1000:
                    self.app_ref._chat_history = self.app_ref._chat_history[-500:]
            except Exception:
                pass
        return result


class CopyScreen(ModalScreen):
    """Ecran modal pour visualiser et copier l'historique du chat."""

    BINDINGS = [
        ("escape", "close", "Fermer"),
        ("ctrl+e", "close", "Fermer"),
    ]


    def __init__(self, app_ref: FtMsgApp, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        with Container(id="copy_container"):
            yield Static("Historique", classes="copy-title")
            history = "\n".join(self.app_ref._chat_history[-500:])
            yield TextArea(history, read_only=True, id="copy_area")
            with Horizontal(id="copy_buttons"):
                yield Button("Copier selection", id="copy_selection", variant="success")
                yield Button("Copier tout", id="copy_all", variant="primary")
                yield Button("Fermer", id="copy_close", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ta = self.query_one("#copy_area", TextArea)
        if event.button.id == "copy_selection":
            text = ""
            if hasattr(ta, "selected_text"):
                text = ta.selected_text or ""
            if not text:
                self.app_ref.notify("Aucun texte selectionne", severity="warning")
                return
            if self.app_ref._copy_to_clipboard(text):
                self.app_ref.notify("Texte copie dans le presse-papier", title="Copie", severity="information")
            else:
                self.app_ref.notify("Impossible de copier (presse-papier non accessible)", severity="error")
        elif event.button.id == "copy_all":
            if self.app_ref._copy_to_clipboard(ta.text):
                self.app_ref.notify("Historique copie dans le presse-papier", title="Copie", severity="information")
            else:
                self.app_ref.notify("Impossible de copier (presse-papier non accessible)", severity="error")
        elif event.button.id == "copy_close":
            self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


# --------------------------------------------------------------------------- #
# Profile Screen
# --------------------------------------------------------------------------- #
class ProfileScreen(ModalScreen):
    """Ecran modal pour visualiser un profil utilisateur."""

    BINDINGS = [
        ("escape", "close", "Fermer"),
    ]


    def __init__(self, target_user: str, profile_data: dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.target_user = target_user
        self.profile_data = profile_data

    def compose(self) -> ComposeResult:
        bio = self.profile_data.get("bio", "Aucune bio.")
        status = self.profile_data.get("status", "Disponible")
        scores = self.profile_data.get("scores", {})

        with Container(id="profile_container") as container:
            container.border_title = f"✨ Profil de {self.target_user} ✨"
            
            with Vertical(classes="profile-info"):
                yield Static(f"🟢 [b]Statut:[/b] {status}", classes="profile-status")
                yield Static(f"📝 [b]Bio:[/b] {bio}", classes="profile-bio")
            
            if scores:
                with Vertical(classes="profile-scores-container") as scores_container:
                    scores_container.border_title = "🏆 Scores & Statistiques"
                    for game_id, game_scores in scores.items():
                        with Vertical(classes="game-card"):
                            yield Static(f"🎮 {game_id.upper()}", classes="game-card-title")
                            for k, v in game_scores.items():
                                yield Static(f"• {k.replace('_', ' ').capitalize()}: [b white]{v}[/b white]", classes="game-stat")
            else:
                with Vertical(classes="profile-scores-container") as scores_container:
                    scores_container.border_title = "🏆 Scores & Statistiques"
                    yield Static("😴 Aucun score enregistré pour le moment.", classes="profile-bio")

            yield Button("Fermer", variant="primary", id="profile_close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "profile_close":
            self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


# --------------------------------------------------------------------------- #
# File Picker Screen
# --------------------------------------------------------------------------- #
class FilteredDirectoryTree(DirectoryTree):
    """DirectoryTree with optional hidden-files filter."""

    def __init__(self, path: str | Path, show_hidden: bool = False, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self.show_hidden = show_hidden

    def filter_paths(self, paths):
        if self.show_hidden:
            return paths
        return [p for p in paths if not p.name.startswith(".")]

    def toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.reload()


class FilePickerScreen(ModalScreen):
    """Built-in file picker using DirectoryTree."""

    BINDINGS = [
        ("escape", "close", "Fermer"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.selected_path: str | None = None

    def compose(self) -> ComposeResult:
        with Container(id="file_picker_container"):
            yield Static("📁  S É L E C T I O N N E R   U N   F I C H I E R", classes="file-picker-title")
            yield FilteredDirectoryTree(Path.home(), show_hidden=False, id="file_tree")
            yield Static("", id="file_selected")
            with Horizontal(id="file_picker_buttons"):
                yield Button("👁  Cachés", id="file_toggle_hidden", variant="default")
                yield Button("✅  Envoyer", id="file_confirm", variant="success", disabled=True)
                yield Button("✕  Annuler", id="file_cancel", variant="default")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.selected_path = str(event.path)
        self.query_one("#file_selected", Static).update(f"[bold green]{self.selected_path}[/bold green]")
        self.query_one("#file_confirm", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "file_toggle_hidden":
            tree = self.query_one("#file_tree", FilteredDirectoryTree)
            tree.toggle_hidden()
            btn = self.query_one("#file_toggle_hidden", Button)
            btn.label = "👁  Cachés" if tree.show_hidden else "👁  Cachés"
            btn.variant = "primary" if tree.show_hidden else "default"
        elif event.button.id == "file_confirm":
            self.dismiss(self.selected_path)
        elif event.button.id == "file_cancel":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- #
# Game Menu Screen
# --------------------------------------------------------------------------- #
class GameMenuScreen(ModalScreen):
    BINDINGS = [
        ("ctrl+g", "close", "Fermer"),
    ]


    def __init__(self, in_room: bool = False) -> None:
        super().__init__()
        self.in_room = in_room

    def compose(self) -> ComposeResult:
        with Container(id="game_menu_container"):
            yield Static("🎮  C E N T R E   D E   J E U X", classes="game-menu-title")
            yield Static("Choisis un jeu à lancer", classes="game-menu-subtitle")

            solo = list_games(solo_only=True)
            mp = list_multiplayer_games()

            with ScrollableContainer(id="game_list_container"):
                if solo:
                    yield Static("━━━ 🕹️  Solo ━━━", classes="game-category")
                    for g in solo:
                        with Horizontal(classes="game-card"):
                            with Container(classes="game-card-info"):
                                yield Static(f"{g.name}", classes="game-card-name")
                                yield Static(f"{g.description}", classes="game-card-desc")
                                yield Static(f"👤 1 joueur", classes="game-card-meta")
                            yield Button("▶  Jouer", id=f"game_{g.game_id}", variant="primary", classes="game-card-btn")

                if self.in_room and mp:
                    yield Static("━━━ 👥 Multijoueur ━━━", classes="game-category")
                    for g in mp:
                        with Horizontal(classes="game-card"):
                            with Container(classes="game-card-info"):
                                yield Static(f"{g.name}", classes="game-card-name")
                                yield Static(f"{g.description}", classes="game-card-desc")
                                yield Static(f"👥 {g.min_players}-{g.max_players} joueurs", classes="game-card-meta")
                            yield Button("▶  Lancer", id=f"game_{g.game_id}", variant="success", classes="game-card-btn")
                elif mp:
                    yield Static("━━━ 👥 Multijoueur ━━━", classes="game-category")
                    yield Static("  [dim]Rejoins un salon pour jouer en multijoueur[/dim]")
                    for g in mp:
                        with Horizontal(classes="game-card"):
                            with Container(classes="game-card-info"):
                                yield Static(f"{g.name}", classes="game-card-name")
                                yield Static(f"{g.description}", classes="game-card-desc")
                                yield Static(f"👥 {g.min_players}-{g.max_players} joueurs", classes="game-card-meta")
                            yield Button("🔒", id=f"game_{g.game_id}", variant="default", disabled=True, classes="game-card-btn")

            yield Button("✕  Fermer / Quitter", id="game_menu_close", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "game_menu_close":
            self.dismiss()
            return
        if event.button.id and event.button.id.startswith("game_"):
            game_id = event.button.id[len("game_"):]
            self.dismiss(game_id)

    def action_close(self) -> None:
        self.dismiss()


# --------------------------------------------------------------------------- #
# Game Invite Banner
# --------------------------------------------------------------------------- #
class GameInviteBanner(Horizontal):
    """Banner shown when a multiplayer game invite is active."""


    def __init__(self, app_ref: "FtMsgApp", invite: GameInvite, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_ref = app_ref
        self.invite = invite

    def compose(self) -> ComposeResult:
        count = len(self.invite.players)
        yield Static(
            f"🎮  [bold]{self.invite.host_login}[/bold] lance [bold green]{self.invite.game_name}[/bold green]  —  "
            f"{count}/{self.invite.max_players} joueur·euses",
            id="invite_text",
        )
        yield Button("🚀 Rejoindre", id="invite_join", variant="success")
        yield Button("✕ Ignorer", id="invite_dismiss", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "invite_join":
            self.app_ref.run_worker(self.app_ref._do_join_game(self.invite.invite_id))
            self.remove()
        elif event.button.id == "invite_dismiss":
            self.remove()


# --------------------------------------------------------------------------- #
# File Offer Banner
# --------------------------------------------------------------------------- #
class FileOfferBanner(Horizontal):
    """Banner shown when someone offers a file transfer."""

    def __init__(self, app_ref: "FtMsgApp", file_id: str, sender_login: str, file_name: str, file_size: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.app_ref = app_ref
        self.file_id = file_id
        self.sender_login = sender_login
        self.file_name = file_name
        self.file_size = file_size

    def _format_size(self) -> str:
        if self.file_size > 1024 * 1024:
            return f"{self.file_size / (1024 * 1024):.1f} Mo"
        if self.file_size > 1024:
            return f"{self.file_size / 1024:.1f} Ko"
        return f"{self.file_size} o"

    def compose(self) -> ComposeResult:
        yield Static(
            f"📎 {self.sender_login} veut envoyer [bold]{self.file_name}[/bold] ({self._format_size()})",
            id="file_offer_text",
        )
        yield Button("✅ Accepter", id="file_accept", variant="success")
        yield Button("❌ Refuser", id="file_reject", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "file_accept":
            self.app_ref.client.accept_file_offer(self.file_id)
        elif event.button.id == "file_reject":
            self.app_ref.client.reject_file_offer(self.file_id)
        self.remove()


# --------------------------------------------------------------------------- #
# Game Widgets
# --------------------------------------------------------------------------- #
class ChessWidget(Container):
    """Interactive Chess board."""

    state = reactive(dict)
    
    DEFAULT_CSS = """
    ChessWidget { width: auto; height: auto; align: center middle; content-align: center middle; padding: 1; }
    #chess_grid { grid-size: 8 8; grid-rows: 3; grid-columns: 5; grid-gutter: 0; width: 40; height: auto; border: solid white; }
    #chess_grid Button { width: 5; height: 3; min-width: 5; border: none; }
    """

    def __init__(self, app_ref: "FtMsgApp", **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_ref = app_ref
        self.selected_square: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="chess_status")
        with Center():
            with Grid(id="chess_grid"):
                for rank in range(8, 0, -1):
                    for file_idx, f in enumerate("abcdefgh"):
                        sq = f"{f}{rank}"
                        yield Button("", id=f"chess_cell_{sq}")

    def watch_state(self, new_state: dict) -> None:
        self._update_ui(new_state)

    def _update_ui(self, st: dict) -> None:
        import chess
        fen = st.get("fen", chess.STARTING_FEN)
        current = st.get("current_player", "")
        winner = st.get("winner")
        active = st.get("active", True)
        players = st.get("players", [])
        white_player = st.get("white_player", "")
        black_player = st.get("black_player", "")
        
        status = self.query_one("#chess_status", Static)
        lines: list[str] = []
        lines.append("[bold]♟  C h e s s  ♙[/bold]")
        lines.append("")

        if winner:
            if winner == self.app_ref.login:
                lines.append(f"[bold green]🎉  Tu as gagné !  🎉[/bold green]")
            else:
                lines.append(f"[bold red]😞  {winner} a gagné  😞[/bold red]")
        elif not active:
            lines.append("[dim]🤝  Match nul  🤝[/dim]")
        else:
            my_turn = current == self.app_ref.login
            if my_turn:
                lines.append(f"[bold green]👉  C'est ton tour[/bold green]")
            else:
                lines.append(f"[dim]Tour de {current}...[/dim]")
            if self.app_ref.login == white_player:
                lines.append(f"[dim]Tu joues les Blancs[/dim]")
            elif self.app_ref.login == black_player:
                lines.append(f"[dim]Tu joues les Noirs[/dim]")
                
        status.update("\n".join(lines))

        board = chess.Board(fen)
        my_turn = (current == self.app_ref.login)

        for rank in range(8, 0, -1):
            for file_idx, f in enumerate("abcdefgh"):
                is_light = (rank + file_idx) % 2 != 0
                bg = "white" if is_light else "rgb(150,150,150)"
                
                sq = f"{f}{rank}"
                btn = self.query_one(f"#chess_cell_{sq}", Button)
                if self.selected_square == sq:
                    btn.styles.background = "yellow"
                else:
                    btn.styles.background = bg
                
                piece = board.piece_at(chess.parse_square(sq))
                if piece:
                    # letter
                    sym = piece.unicode_symbol()
                    btn.label = f"[black]{sym}[/black]"
                else:
                    btn.label = ""
                
                btn.disabled = not active or not my_turn or winner is not None
                
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("chess_cell_"):
            sq = event.button.id.split("_")[-1]
            st = self.state
            active = st.get("active", True)
            winner = st.get("winner")
            current = st.get("current_player", "")
            my_turn = (current == self.app_ref.login)
            if active and my_turn and not winner:
                if not self.selected_square:
                    self.selected_square = sq
                    self._update_ui(st)
                else:
                    move = f"{self.selected_square}{sq}"
                    self.selected_square = None
                    self.app_ref.run_worker(
                        self.app_ref.client.send_game_action("move", {"move": move})
                    )

# --------------------------------------------------------------------------- #
# Game Widgets
# --------------------------------------------------------------------------- #
class TicTacToeWidget(Container):
    """Interactive Tic-Tac-Toe grid with clickable buttons."""

    state = reactive(dict)


    def __init__(self, app_ref: "FtMsgApp", **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Static("", id="ttt_status")
        with Center():
            with Grid(id="ttt_grid"):
                for i in range(9):
                    yield Button("", id=f"ttt_cell_{i}", variant="default")

    def watch_state(self, new_state: dict) -> None:
        self._update_ui(new_state)

    def _update_ui(self, st: dict) -> None:
        board = st.get("board", [[None]*3 for _ in range(3)])
        current = st.get("current_player", "")
        winner = st.get("winner")
        active = st.get("active", True)
        players = st.get("players", [])
        symbols = st.get("symbols", ["X", "O"])

        my_symbol = ""
        if self.app_ref.login in players:
            my_idx = players.index(self.app_ref.login)
            my_symbol = symbols[my_idx % len(symbols)]

        status = self.query_one("#ttt_status", Static)
        lines: list[str] = []
        lines.append("[bold]⭕ T i c - T a c - T o e[/bold]")
        lines.append("")

        if winner:
            if winner == self.app_ref.login:
                lines.append(f"[bold green]🎉  Tu as gagné !  🎉[/bold green]")
            else:
                lines.append(f"[bold red]😞  {winner} a gagné  😞[/bold red]")
        elif not active:
            lines.append("[dim]🤝  Match nul  🤝[/dim]")
        else:
            my_turn = current == self.app_ref.login
            if my_turn:
                lines.append(f"[bold green]👉  C'est ton tour ({my_symbol})[/bold green]")
            else:
                lines.append(f"[dim]Tour de {current}...[/dim]")
            if my_symbol:
                lines.append(f"[dim]Tu joues {my_symbol}[/dim]")
        status.update("\n".join(lines))

        for y in range(3):
            for x in range(3):
                cell = board[y][x]
                btn = self.query_one(f"#ttt_cell_{y*3+x}", Button)

                if cell == "X":
                    btn.label = "[bold]X[/bold]"
                    btn.variant = "primary"
                    btn.add_class("x_cell")
                    btn.remove_class("o_cell")
                    btn.disabled = True
                elif cell == "O":
                    btn.label = "[bold]O[/bold]"
                    btn.variant = "error"
                    btn.add_class("o_cell")
                    btn.remove_class("x_cell")
                    btn.disabled = True
                else:
                    btn.label = ""
                    btn.variant = "default"
                    btn.remove_class("x_cell")
                    btn.remove_class("o_cell")
                    my_turn = current == self.app_ref.login
                    btn.disabled = not active or not my_turn or winner is not None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("ttt_cell_"):
            idx = int(event.button.id.split("_")[-1])
            x = idx % 3
            y = idx // 3
            self.app_ref.run_worker(
                self.app_ref.client.send_game_action("move", {"x": x, "y": y})
            )


class WordRaceWidget(Container):
    """Visually rich Word Race display with score bars and round progress."""

    state = reactive(dict)


    def __init__(self, app_ref: "FtMsgApp", **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Static("[bold]🏁  W O R D   R A C E[/bold]", id="wr_title")
        yield Static("", id="wr_round")
        yield Static("", id="wr_word")
        from textual.widgets import Input
        yield Input(placeholder="Type the word here...", id="wr_input")
        yield Static("", id="wr_scores")
        yield Static("", id="wr_status")
        yield Button("Next Round ➔", id="wr_next", variant="primary")

    def watch_state(self, new_state: dict) -> None:
        self._update_ui(new_state)

    def _update_ui(self, st: dict) -> None:
        word = st.get("current_word", "")
        scores = st.get("scores", {})
        rnd = st.get("round", 0)
        total = st.get("total_rounds", 5)
        winner = st.get("winner")
        active = st.get("active", True)
        round_winner = st.get("round_winner")

        round_dots = []
        for i in range(1, total + 1):
            if i < rnd:
                round_dots.append("[green]●[/green]")
            elif i == rnd:
                round_dots.append("[bold red]●[/bold red]")
            else:
                round_dots.append("[dim]○[/dim]")

        round_widget = self.query_one("#wr_round", Static)
        round_widget.update(f"Round {rnd}/{total}     " + "  ".join(round_dots))

        word_widget = self.query_one("#wr_word", Static)
        if word:
            word_widget.update(f"[bold red blink]  {word.upper()}  [/bold red blink]")
        else:
            word_widget.update("")

        scores_widget = self.query_one("#wr_scores", Static)
        score_lines: list[str] = []
        if scores:
            max_score = max(scores.values()) if scores else 0
            max_score = max(max_score, 1)
            for player, score in sorted(scores.items(), key=lambda x: -x[1]):
                bar_len = int((score / max_score) * 18)
                bar = "█" * bar_len + "░" * (18 - bar_len)
                color = "green" if player == self.app_ref.login else "cyan"
                marker = "👉" if player == self.app_ref.login else "  "
                score_lines.append(f"{marker} [bold {color}]{player:12}[/bold {color}] {bar} {score}")
        scores_widget.update("\n".join(score_lines) if score_lines else "")

        status_widget = self.query_one("#wr_status", Static)
        next_btn = self.query_one("#wr_next", Button)
        from textual.widgets import Input
        inp = self.query_one("#wr_input", Input)
        
        if winner:
            if winner == self.app_ref.login:
                status_widget.update("[bold green]🎉  Tu as gagné la partie !  🎉[/bold green]")
            else:
                status_widget.update(f"[bold red]😞  {winner} a gagné la partie  😞[/bold red]")
            next_btn.display = False
            inp.disabled = True
        elif not active:
            status_widget.update("[dim]Partie terminée[/dim]")
            next_btn.display = False
            inp.disabled = True
        elif round_winner:
            status_widget.update(f"[yellow]⭐  {round_winner} remporte ce round !  ⭐[/yellow]")
            next_btn.display = (self.app_ref.client.current_game_invite and self.app_ref.client.current_game_invite.host_login == self.app_ref.login)
            inp.disabled = True
        else:
            status_widget.update("[dim]Tape le mot vite![/dim]")
            next_btn.display = False
            inp.disabled = False
            inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "wr_next":
            self.app_ref.run_worker(self.app_ref.client.send_game_action("next_round", {}))

    from textual import on
    from textual.widgets import Input
    @on(Input.Submitted, "#wr_input")
    def on_wr_input_submitted(self, event: Input.Submitted):
        word = event.value.strip()
        if word:
            self.app_ref.run_worker(self.app_ref.client.send_game_action("type", {"word": word}))
        event.input.value = ""

# --------------------------------------------------------------------------- #
# Game Screen
# --------------------------------------------------------------------------- #
class GameScreen(ModalScreen):
    """Overlay screen for active gameplay with themed styling."""

    BINDINGS = [
        ("escape", "close", "Quitter"),
        ("q", "close", "Quitter"),
        ("up", "snake_up", "Haut"),
        ("down", "snake_down", "Bas"),
        ("left", "snake_left", "Gauche"),
        ("right", "snake_right", "Droite"),
        ("r", "snake_restart", "Restart"),
    ]


    def __init__(self, app_ref: "FtMsgApp", game_id: str, invite: GameInvite) -> None:
        super().__init__()
        self.app_ref = app_ref
        self.game_id = game_id
        self.invite = invite
        self._game_widget: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="game_container"):
            with Container(id="game_header"):
                yield Static(f"🎮  {self.invite.game_name}", id="game_title")
                yield Static("En attente...", id="game_status")
            with Container(id="game_area"):
                if self.game_id == "snake":
                    self._game_widget = SnakeWidget(id="game_widget")
                    yield self._game_widget
                elif self.game_id == "twenty48":
                    self._game_widget = Twenty48Widget(id="game_widget")
                    yield self._game_widget
                elif self.game_id == "tictactoe":
                    self._game_widget = TicTacToeWidget(self.app_ref, id="game_widget")
                    yield self._game_widget
                elif self.game_id == "wordrace":
                    self._game_widget = WordRaceWidget(self.app_ref, id="game_widget")
                    yield self._game_widget
                elif self.game_id == "chess":
                    self._game_widget = ChessWidget(self.app_ref, id="game_widget")
                    yield self._game_widget
                elif self.game_id == "connectfour":
                    self._game_widget = ConnectFourWidget(self.app_ref, id="game_widget")
                    yield self._game_widget
                elif self.game_id == "battleship":
                    self._game_widget = BattleshipWidget(self.app_ref, id="game_widget")
                    yield self._game_widget
                elif self.game_id == "hangman":
                    self._game_widget = HangmanWidget(self.app_ref, id="game_widget")
                    yield self._game_widget

            with Container(id="game_controls"):
                yield Button("✕  Quitter la partie", id="game_quit", variant="error")
                if self.game_id == "snake":
                    yield Static("[dim]⬆ ⬇ ⬅ ➡  bouger  |  [bold]R[/bold] restart  |  [bold]Q[/bold] quitter[/dim]", id="snake_hint")

    def on_mount(self) -> None:
        self._update_status()

    def _update_status(self) -> None:
        status = self.query_one("#game_status", Static)
        invite = self.invite
        game = get_game(self.game_id)
        if not game:
            return
        if game.is_solo:
            status.update("[bold green]🕹️ Solo[/bold green]")
            return
        count = len(invite.players)
        if count < game.min_players:
            status.update(
                f"[yellow]⏳  En attente... {count}/{game.min_players}[/yellow]"
            )
        else:
            status.update(f"[green]▶  Partie en cours — {count} joueur·euses[/green]")

    def update_game_state(self, state: dict) -> None:
        self._update_status()
        if self._game_widget:
            self._game_widget.state = state
        if not state.get("active", True):
            winner = state.get("winner")
            status = self.query_one("#game_status", Static)
            if winner:
                status.update(f"[bold green]🎉  {winner} a gagné !  🎉[/bold green]")
            else:
                status.update("[dim]🤝  Partie terminée  🤝[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "game_quit":
            self.app_ref.run_worker(self.app_ref._do_leave_game())
            self.dismiss()

    def action_close(self) -> None:
        self.app_ref.run_worker(self.app_ref._do_leave_game())
        self.dismiss()

    def action_snake_up(self) -> None:
        self._send_snake_action("up")

    def action_snake_down(self) -> None:
        self._send_snake_action("down")

    def action_snake_left(self) -> None:
        self._send_snake_action("left")

    def action_snake_right(self) -> None:
        self._send_snake_action("right")

    def action_snake_restart(self) -> None:
        self._send_snake_action("restart")

    def _send_snake_action(self, action: str) -> None:
        if self.game_id in ("snake", "twenty48") and self.app_ref.client.current_game_session:
            self.app_ref.run_worker(self.app_ref.client.send_game_action(action, {}))



class ChatTextArea(TextArea):
    def on_mount(self) -> None:
        self.text = "Envoyer un message..."
        self.styles.color = "#888888"

    def on_focus(self, event) -> None:
        if self.text == "Envoyer un message...":
            self.text = ""
            self.styles.color = "white"

    def on_blur(self, event) -> None:
        if not self.text.strip():
            self.text = "Envoyer un message..."
            self.styles.color = "#888888"

    def on_key(self, event) -> None:
        if self.text == "Envoyer un message...":
            self.text = ""
            self.styles.color = "white"

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.action_submit()
        elif event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            # Try to insert a newline, fallback to appending if insert is not available
            if hasattr(self, "insert"):
                self.insert("\\n")
            else:
                self.text = self.text + "\\n"
                
    def action_submit(self) -> None:
        content = self.text
        if content.strip() and content != "Envoyer un message...":
            self.app.run_worker(self.app.action_submit_message(content))
        self.text = ""
        self.action_cursor_line_start()




class FtMsgApp(App[None]):
    TITLE = "42msg"

    BINDINGS = [
        ("ctrl+q", "quit", "Quitter"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
        ("ctrl+g", "toggle_games", "Jeux"),
        ("ctrl+s", "toggle_settings", "Parametres"),
        ("ctrl+e", "copy_mode", "Copier"),
    ]

    sidebar_width = reactive(35)

    CSS_PATH = "style.tcss"

    def __init__(self, login: str | None = None) -> None:
        super().__init__()
        self.login = login or default_login()
        self.client = FTMessageClient(self.login)
        self._has_unread = False
        self.desktop_notifications = True
        self._chat_history: list[str] = []
        self._game_screen: GameScreen | None = None

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

    def action_toggle_settings(self) -> None:
        self.push_screen(SettingsScreen())

    def action_copy_mode(self) -> None:
        self.push_screen(CopyScreen(self))

    def action_toggle_games(self) -> None:
        in_room = bool(self.client.current_channel_name())
        self.push_screen(GameMenuScreen(in_room=in_room), callback=self._on_game_menu_closed)

    def _on_game_menu_closed(self, game_id: str | None) -> None:
        if not game_id:
            return
        self.run_worker(self._do_start_game(game_id))

    async def _do_start_game(self, game_id: str) -> None:
        status, invite = await self.client.create_game_invite(game_id)
        if status == "solo_started":
            await self._open_game_screen(game_id, invite)
        elif status == "created":
            if invite:
                await self._open_game_screen(game_id, invite)
        elif status == "not_in_channel":
            log = self.query_one("#messages", ChatLog)
            now = time.strftime("%H:%M:%S")
            log.write(f"[red][{now}] Tu dois être dans un salon pour ce jeu multijoueur[/red]")
        else:
            log = self.query_one("#messages", ChatLog)
            now = time.strftime("%H:%M:%S")
            log.write(f"[red][{now}] Impossible de créer la partie: {status}[/red]")

    async def _do_join_game(self, invite_id: str) -> None:
        status = await self.client.join_game_invite(invite_id)
        if status == "joined":
            invite = self.client.current_game_invite
            if invite:
                await self._open_game_screen(invite.game_id, invite)
        elif status == "full":
            self.notify("Partie pleine", severity="warning")
        elif status == "unknown_invite":
            self.notify("Invitation inconnue", severity="error")
        else:
            self.notify(f"Erreur: {status}", severity="error")

    def _on_file_selected(self, filepath: str | None) -> None:
        if not filepath:
            return
        self.run_worker(self._do_send_file(filepath))

    async def _do_send_file(self, filepath: str) -> None:
        log = self.query_one("#messages", ChatLog)
        now = time.strftime("%H:%M:%S")
        status = await self.client.send_file(filepath)
        if status == "sent":
            log.write(f"[green][{now}] Fichier en cours d'envoi...[/green]")
        elif status == "file_not_found":
            log.write(f"[red][{now}] Fichier introuvable: {filepath}[/red]")
        elif status == "not_a_file":
            log.write(f"[red][{now}] Ce n'est pas un fichier: {filepath}[/red]")
        elif status == "not_in_channel":
            log.write(f"[red][{now}] Tu dois être dans un salon pour envoyer un fichier[/red]")
        elif status == "file_too_large":
            log.write(f"[red][{now}] Fichier trop volumineux (max 10 Mo)[/red]")
        else:
            log.write(f"[red][{now}] Erreur envoi fichier: {status}[/red]")

    async def _do_leave_game(self) -> None:
        await self.client.leave_game()
        self._game_screen = None

    async def _open_game_screen(self, game_id: str, invite: GameInvite) -> None:
        screen = GameScreen(self, game_id, invite)
        self._game_screen = screen
        self.push_screen(screen)

    def _on_game_invite_received(self, invite: GameInvite) -> None:
        try:
            chat_area = self.query_one("#chat_area", Container)
            for existing in chat_area.query(GameInviteBanner):
                existing.remove()
            banner = GameInviteBanner(self, invite)
            chat_area.mount(banner, before=self.query_one("#compose", Container))
        except Exception:
            pass

    def _on_game_state_change(self, state: dict) -> None:
        if self._game_screen:
            self._game_screen.update_game_state(state)

    def _on_game_end(self, winner: str | None) -> None:
        if self._game_screen:
            self._game_screen.update_game_state({"active": False, "winner": winner})
        self._game_screen = None

    def _copy_to_clipboard(self, text: str) -> bool:
        """Copy text to system clipboard using pyperclip, CLI tools, or OSC 52."""
        if not text:
            return False
        # 1. Try pyperclip
        if pyperclip is not None:
            try:
                pyperclip.copy(text)
                return True
            except Exception:
                pass
        # 2. Try common CLI tools
        for cmd, args in [
            (["wl-copy"], {}),
            (["xclip", "-selection", "clipboard"], {}),
            (["xsel", "--clipboard", "--input"], {}),
            (["pbcopy"], {}),
        ]:
            try:
                subprocess.run(cmd, input=text.encode(), check=True, capture_output=True, **args)
                return True
            except Exception:
                continue
        # 3. Fallback OSC 52 (works in many modern terminals)
        try:
            self._osc52_copy(text)
            return True
        except Exception:
            pass
        return False

    def _osc52_copy(self, text: str) -> None:
        """Copy text using OSC 52 terminal escape sequence."""
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        seq = f"\033]52;c;{encoded}\a"
        print(seq, end="", flush=True)

    def _pick_file(self) -> str | None:
        """Pick a file path using a native GUI dialog or clipboard."""
        has_gui = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

        # 1. Try tkinter (portable, works on most systems with GUI)
        if has_gui:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                filepath = filedialog.askopenfilename(title="Choisir un fichier à envoyer")
                root.destroy()
                if filepath:
                    return filepath
            except Exception:
                pass

        # 2. Try zenity (Linux GNOME/GTK)
        if has_gui:
            try:
                result = subprocess.run(
                    ["zenity", "--file-selection", "--title=Choisir un fichier à envoyer"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass

        # 3. Try kdialog (KDE)
        if has_gui:
            try:
                result = subprocess.run(
                    ["kdialog", "--getopenfile", "", "*"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass

        # 4. Try osascript (macOS)
        try:
            result = subprocess.run(
                ["osascript", "-e", "return POSIX path of (choose file)"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        # 5. Try clipboard
        if pyperclip is not None:
            try:
                text = pyperclip.paste()
                if text and os.path.isfile(text.strip()):
                    return text.strip()
            except Exception:
                pass
        return None

    def _desktop_notify(self, title: str, msg: str) -> None:
        if not self.desktop_notifications:
            return
        try:
            subprocess.Popen(["notify-send", title, msg])
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Compose & mount
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main_area"):
            with Container(id="sidebar"):
                yield Static("Chargement...", id="status_box")
                with TabbedContent():
                    with TabPane("Salons", id="tab_channels"):
                        yield Static("", id="channels_box")
                    with TabPane("Membres", id="tab_members"):
                        yield Static("", id="members_box")
            yield DragHandle(self, id="resize_handle")
            with Container(id="chat_area"):
                with Container(id="chat"):
                    yield ChatLog(self, id="messages", wrap=True, markup=True, highlight=True)
                with Container(id="compose"):
                    yield OptionList(id="suggestions_list")
                    yield ChatTextArea(
                        id="message_input",
                        show_line_numbers=False,
                    )
        yield CustomFooter()

    def on_mount(self) -> None:
        self.query_one("#messages", ChatLog).write(
            "[bold green]42msg prêt[/bold green] — "
            "[bold]/create[/bold] [italic]nom max password [1|0][/italic] "
            "[bold]/list[/bold] [bold]/join[/bold] "
            "[bold]/leave[/bold] [bold]/help[/bold]"
        )
        self.query_one("#message_input", ChatTextArea).focus()
        self.client.on_game_invite = self._on_game_invite_received
        self.client.on_game_state_change = self._on_game_state_change
        self.client.on_game_end = self._on_game_end
        self.run_worker(self._startup())
        self.set_interval(0.2, self._drain_queues)
        self.set_interval(1.0, self._update_sidebar)
        self.set_interval(0.15, self._game_tick)

    # ------------------------------------------------------------------ #
    # Game tick
    # ------------------------------------------------------------------ #

    async def _game_tick(self) -> None:
        if self.client.current_game_session and hasattr(self.client.current_game_session, "tick"):
            try:
                self.client.current_game_session.tick()
                if self.client.on_game_state_change:
                    self.client.on_game_state_change(self.client.current_game_session.get_render_state())
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Sidebar updates
    # ------------------------------------------------------------------ #

    def _update_sidebar(self) -> None:
        try:
            status_box = self.query_one("#status_box", Static)
            channels_box = self.query_one("#channels_box", Static)
            members_box = self.query_one("#members_box", Static)
        except Exception:
            return
        cname = self.client.current_channel_name()
        net_mode = "Relais" if self.client.relay_url else "Direct (P2P)"
        role = "Hote" if self.client.is_hosting else "Invite" if cname else "-"

        status_text = (
            f"[bold cyan]👤 {self.login}[/bold cyan]\n"
            f"[dim]Réseau:[/dim] {net_mode}\n"
            f"[dim]Rôle:[/dim] {role}\n"
            f"[dim]Salon:[/dim] [bold]{cname or 'Aucun'}[/bold]"
        )
        status_box.update(status_text)

        channels = self.client.list_channels()
        ch_text = "\n[bold magenta] Salons actifs[/bold magenta]\n"
        if not channels:
            ch_text += "  (Aucun salon)"
        else:
            for i, ch in enumerate(channels):
                vis = "🔓" if ch.is_public else "🔒"
                campus = " 🏫" if ch.campus_only else ""
                ch_text += f"  [bold]{ch.name}[/bold] {vis}{campus}\n"
                ch_text += f"  └ {ch.user_count}/{ch.max_users} | /join {i}\n"
        channels_box.update(ch_text)

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

        members_box.update(mb_text)

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
        try:
            log = self.query_one("#messages", RichLog)
        except Exception:
            return
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
                self._desktop_notify("Nouveau MP", f"{sender} t'a envoye un message prive.")
            elif sender != self.login and f"@{self.login}" in message:
                highlighted = formatted.replace(
                    f"@{self.login}",
                    f"[bold red underline]@{self.login}[/bold red underline]",
                )
                log.write(f"[green][{ts_str}] [bold]{prefix}[/bold]:[/green] {highlighted}")
                self._desktop_notify("Mention 42msg", f"{sender} t'a mentionne.")
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

        while True:
            try:
                offer = self.client.file_offers_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._show_file_offer(offer)

        if new_messages:
            if at_bottom:
                log.scroll_end()
            else:
                self._has_unread = True
                self.notify("Nouveaux messages", title="42msg", severity="information")

    def _show_file_offer(self, offer: dict[str, Any]) -> None:
        try:
            chat_area = self.query_one("#chat_area", Container)
            for existing in chat_area.query(FileOfferBanner):
                existing.remove()
            banner = FileOfferBanner(
                self,
                offer["file_id"],
                offer["sender_login"],
                offer["file_name"],
                offer["file_size"],
            )
            chat_area.mount(banner, before=self.query_one("#compose", Container))
        except Exception:
            pass

    async def on_unmount(self) -> None:
        await self.client.stop()

    # ------------------------------------------------------------------ #
    # Input handling
    # ------------------------------------------------------------------ #

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        suggestions = self.query_one("#suggestions_list", OptionList)
        val = event.text_area.text

        if val and not val.startswith("/"):
            if self.client.current_channel_name():
                self.run_worker(self.client.send_typing_indicator())

        if val.startswith("/"):
            matches = [c for c in _COMMANDS if c.startswith(val)]
            if matches:
                suggestions.clear_options()
                suggestions.add_options(matches)
                suggestions.styles.display = "block"
                return
        suggestions.styles.display = "none"

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        inp = self.query_one("#message_input", ChatTextArea)
        inp.text = str(event.option.prompt)
        inp.action_cursor_line_end()
        inp.focus()
        self.query_one("#suggestions_list", OptionList).styles.display = "none"

    def on_key(self, event) -> None:
        if event.key == "tab":
            inp = self.query_one("#message_input", ChatTextArea)
            val = inp.text
            if val.startswith("/"):
                matches = [c for c in _COMMANDS if c.startswith(val)]
                if matches:
                    inp.text = matches[0]
                    inp.action_cursor_line_end()
                    event.stop()
                    event.prevent_default()
            self.query_one("#suggestions_list", OptionList).styles.display = "none"

    async def action_submit_message(self, content: str) -> None:
        content = content.strip()
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
        "  [bold]/create <nom> <max> [password] [1|0][/bold]     — créer un salon\n"
        "  [bold]/list[/bold]                                    — lister les salons\n"
        "  [bold]/join <ip> <port> <password>[/bold]             — rejoindre un salon\n"
        "  [bold]/join <index> <password>[/bold]                 — rejoindre depuis /list\n"
        "  [bold]/leave[/bold]                                   — quitter le salon\n"
        "  [bold]/peers[/bold]                                   — membres du salon\n"
        "  [bold]/msg <login> <text>[/bold]                      — message privé\n"
        "  [bold]/kick <login>[/bold]                            — expulser (hôte)\n"
        "  [bold]/ban <login>[/bold]                             — bannir (hôte)\n"
        "  [bold]/score list[/bold]                              — lister tes scores\n"
        "  [bold]/score <index>[/bold]                           — partager un score dans le salon\n"
        "  [bold]/leaderboard <index>[/bold]                     — classement du salon pour un jeu\n"
        "  [bold]/profile[/bold]                                 — voir ton profil\n"
        "  [bold]/profile <login>[/bold]                         — voir le profil d'un joueur\n"
        "  [bold]/profile bio <texte>[/bold]                     — changer ta bio\n"
        "  [bold]/profile status <texte>[/bold]                  — changer ton statut\n"
        "  [bold]/sendfile [chemin][/bold]                       — envoyer un fichier (max 10 Mo, salon obligatoire)\n"
        "  [bold]/settings[/bold]                                — paramètres\n"
        "  [bold]/help[/bold]                                    — cette aide\n"
        "  [bold]/quit[/bold]                                    — quitter\n"
        "  Tape un message puis Entrée pour l'envoyer dans le salon.",
            )
            # event.input.value = ""
            return

        if cmd == "/settings":
            self.action_toggle_settings()
            # event.input.value = ""
            return

        if cmd == "/list":
            channels = self.client.list_channels()
            if not channels:
                log.write(f"[magenta][{now}] Aucun salon trouvé sur le réseau[/magenta]")
            else:
                lines = [f"[magenta][{now}] Salons disponibles:[/magenta]"]
                for i, ch in enumerate(channels):
                    vis = "public" if ch.is_public else "privé"
                    campus = " 🏫 campus" if ch.campus_only else ""
                    lines.append(
                        f"  {i}. [bold]{ch.name}[/bold] — "
                        f"{ch.user_count}/{ch.max_users} — {vis}{campus} "
                        f"({ch.host_ip}:{ch.host_port})"
                    )
                log.write("\n".join(lines))
            # event.input.value = ""
            return

        if cmd == "/join":
            parts = content.split(" ")
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /join <nom|ip> [port] [password] ou /join <index> [password][/red]")
                # event.input.value = ""
                return

            if parts[1].isdigit() and len(parts[1]) < 4:
                idx = int(parts[1])
                password = parts[2] if len(parts) > 2 else ""
                channels = self.client.list_channels()
                if idx < 0 or idx >= len(channels):
                    log.write(f"[red][{now}] Index invalide[/red]")
                    # event.input.value = ""
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
                        # event.input.value = ""
                        return
                    host_ip = parts[1]
                    try:
                        host_port = int(parts[2])
                    except ValueError:
                        log.write(f"[red][{now}] port invalide[/red]")
                        # event.input.value = ""
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
            # event.input.value = ""
            return

        if cmd == "/leave":
            await self.client.leave_channel()
            # event.input.value = ""
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
            # event.input.value = ""
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
            # event.input.value = ""
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
            # event.input.value = ""
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
            # event.input.value = ""
            return

        if cmd == "/sendfile":
            parts = content.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                self.push_screen(FilePickerScreen(), callback=self._on_file_selected)
                return
            filepath = parts[1].strip()
            status = await self.client.send_file(filepath)
            if status == "sent":
                log.write(f"[green][{now}] Fichier en cours d'envoi...[/green]")
            elif status == "file_not_found":
                log.write(f"[red][{now}] Fichier introuvable: {filepath}[/red]")
            elif status == "not_a_file":
                log.write(f"[red][{now}] Ce n'est pas un fichier: {filepath}[/red]")
            elif status == "not_in_channel":
                log.write(f"[red][{now}] Tu dois être dans un salon pour envoyer un fichier[/red]")
            elif status == "file_too_large":
                log.write(f"[red][{now}] Fichier trop volumineux (max 10 Mo)[/red]")
            else:
                log.write(f"[red][{now}] Erreur envoi fichier: {status}[/red]")
            # event.input.value = ""
            return

        if cmd == "/games":
            self.action_toggle_games()
            # event.input.value = ""
            return

        if cmd == "/game_start":
            parts = content.split()
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /game_start <game_id>[/red]")
                log.write(f"[dim]Disponibles: snake, tictactoe, wordrace, chess[/dim]")
                # event.input.value = ""
                return
            await self._do_start_game(parts[1])
            # event.input.value = ""
            return

        if cmd == "/game_join":
            parts = content.split()
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /game_join <invite_id>[/red]")
                # event.input.value = ""
                return
            await self._do_join_game(parts[1])
            # event.input.value = ""
            return

        if cmd == "/game_leave":
            await self._do_leave_game()
            if self._game_screen:
                self.pop_screen()
                self._game_screen = None
            # event.input.value = ""
            return

        if cmd == "/game_action":
            parts = content.split()
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /game_action <action> [args...][/red]")
                # event.input.value = ""
                return
            action = parts[1]
            data = {}
            if action == "move":
                if len(parts) >= 4:
                    data = {"x": int(parts[2]), "y": int(parts[3])}
            elif action == "type":
                if len(parts) >= 3:
                    data = {"word": parts[2]}
            elif action == "set_word":
                if len(parts) >= 3:
                    data = {"word": parts[2]}
            elif action == "guess":
                if len(parts) >= 3:
                    data = {"letter": parts[2]}
            await self.client.send_game_action(action, data)
            # event.input.value = ""
            return

        if cmd == "/profile":
            parts = content.split(" ", 2)
            if len(parts) >= 3 and parts[1] == "bio":
                self.client.profile.update_profile(bio=parts[2])
                log.write(f"[green][{now}] Bio mise à jour.[/green]")
                # event.input.value = ""
                return
            elif len(parts) >= 3 and parts[1] == "status":
                self.client.profile.update_profile(status=parts[2])
                log.write(f"[green][{now}] Statut mis à jour.[/green]")
                # event.input.value = ""
                return

            target_user = parts[1] if len(parts) > 1 else self.client.login
            
            if target_user != self.client.login and not self.client.current_channel_name():
                log.write(f"[red][{now}] Tu dois être dans un salon pour voir le profil des autres joueurs.[/red]")
                # event.input.value = ""
                return
                
            profile_data = await self.client.profile_request(target_user)
            if not profile_data:
                log.write(f"[red][{now}] Impossible de récupérer le profil de {target_user} (utilisateur introuvable ou ne répond pas).[/red]")
            else:
                self.push_screen(ProfileScreen(target_user, profile_data))
                
            # event.input.value = ""
            return

        if cmd == "/score":
            parts = content.split()
            if len(parts) == 1 or parts[1] == "list":
                games = await self.client.score_list()
                if not games:
                    log.write(f"[magenta][{now}] Aucun score enregistré. Joue d'abord ![/magenta]")
                else:
                    lines = [f"[magenta][{now}] Scores enregistrés:[/magenta]"]
                    for idx, gid, name in games:
                        lines.append(f"  [bold]{idx}[/bold]. {name} [dim]({gid})[/dim]")
                    log.write("\n".join(lines))
            else:
                try:
                    idx = int(parts[1])
                except ValueError:
                    log.write(f"[red][{now}] usage: /score list | /score <index>[/red]")
                    # event.input.value = ""
                    return
                games = await self.client.score_list()
                if idx < 0 or idx >= len(games):
                    log.write(f"[red][{now}] Index invalide[/red]")
                    # event.input.value = ""
                    return
                game_id = games[idx][1]
                text = await self.client.score_share(game_id)
                status = await self.client.send_channel_message(text)
                if status != "sent":
                    log.write(f"[red][{now}] Impossible d'envoyer le score ({status})[/red]")
            # event.input.value = ""
            return

        if cmd == "/leaderboard":
            if not self.client.current_channel_name():
                log.write(f"[red][{now}] Tu n'es dans aucun salon[/red]")
                # event.input.value = ""
                return
            parts = content.split()
            if len(parts) < 2:
                log.write(f"[red][{now}] usage: /leaderboard <index>[/red]")
                # event.input.value = ""
                return
            try:
                idx = int(parts[1])
            except ValueError:
                log.write(f"[red][{now}] usage: /leaderboard <index>[/red]")
                # event.input.value = ""
                return
            games = await self.client.score_list()
            if idx < 0 or idx >= len(games):
                log.write(f"[red][{now}] Index invalide[/red]")
                # event.input.value = ""
                return
            game_id = games[idx][1]
            log.write(f"[cyan][{now}] Demande de classement pour {game_id}...[/cyan]")
            responses = await self.client.leaderboard_request(game_id)
            lines = [f"[bold cyan]🏆  Leaderboard {game_id}  🏆[/bold cyan]"]
            if not responses:
                lines.append("  Aucune réponse.")
            else:
                # Build a table: each row is a player, each column a metric
                from .games.base import get_game
                g = get_game(game_id)
                schema = g.score_schema if g else {}

                all_keys: set[str] = set()
                for scores in responses.values():
                    all_keys.update(scores.keys())
                
                if schema:
                    numeric_keys = [k for k in schema.keys() if any(isinstance(scores.get(k), (int, float)) for scores in responses.values())]
                else:
                    numeric_keys = [k for k in sorted(all_keys) if any(isinstance(scores.get(k), (int, float)) for scores in responses.values())]
                
                # Sort players by first numeric key descending (or alpha if none)
                players = list(responses.keys())
                if numeric_keys:
                    sort_key = numeric_keys[0]
                    players.sort(key=lambda p: responses.get(p, {}).get(sort_key, 0), reverse=True)
                for rank, player in enumerate(players, start=1):
                    scores = responses.get(player, {})
                    score_parts = []
                    for k in numeric_keys[:3]:  # show first 3 metrics
                        v = scores.get(k, 0)
                        label = schema.get(k, k) if schema else k
                        score_parts.append(f"{label}={v}")
                    score_str = ", ".join(score_parts) if score_parts else "—"
                    lines.append(f"  [bold]{rank}.[/bold] {player:12}  {score_str}")
            log.write("\n".join(lines))
            # event.input.value = ""
            return

        if cmd == "/create":
            tokens = content.split()
            campus_only = False
            filtered = tokens[:]
            if tokens and tokens[-1] in ("0", "1"):
                campus_only = (tokens[-1] == "1")
                filtered = tokens[:-1]
            if len(filtered) < 3:
                log.write(f"[red][{now}] usage: /create <nom> <max> [password] [1|0][/red]")
                # event.input.value = ""
                return
            name = filtered[1]
            try:
                max_users = int(filtered[2])
            except ValueError:
                log.write(f"[red][{now}] max doit être un nombre[/red]")
                # event.input.value = ""
                return
            password = " ".join(filtered[3:]) if len(filtered) > 3 else ""
            is_public = (password == "")
            status = await self.client.create_channel(name, password, max_users, is_public, campus_only)
            net_label = "campus" if campus_only else "public" if is_public else "privé"
            if status == "created":
                log.write(f"[green][{now}] Salon '{name}' créé ({net_label}) ![/green]")
            elif status == "already_in_channel":
                log.write(f"[red][{now}] Déjà dans un salon, quitte-le d'abord[/red]")
            else:
                log.write(f"[red][{now}] Échec création: {status}[/red]")
            # event.input.value = ""
            return

        if cmd.startswith("/"):
            log.write(f"[red][{now}] Commande inconnue: {cmd}[/red]")
            # event.input.value = ""
            return

        cname = self.client.current_channel_name()
        if not cname:
            log.write(
                f"[red][{now}][/red] Tu n'es dans aucun salon. "
                "Utilise [bold]/create[/bold] ou [bold]/join[/bold].",
            )
            # event.input.value = ""
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
        # event.input.value = ""


def run_tui(login: str | None = None) -> None:
    FtMsgApp(login=login).run()
