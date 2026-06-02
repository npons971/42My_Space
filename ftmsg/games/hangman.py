from __future__ import annotations

from typing import Any, Callable

from .base import BaseGame, BaseGameSession, GameInvite, register_game

# Built-in word list (~50 common English words)
WORD_LIST: list[str] = [
    "python", "galaxy", "bridge", "castle", "flower",
    "garden", "jungle", "knight", "market", "orange",
    "planet", "rabbit", "rocket", "stream", "sunset",
    "travel", "valley", "window", "zombie", "anchor",
    "breeze", "candle", "desert", "engine", "falcon",
    "guitar", "heaven", "island", "jigsaw", "kitten",
    "lemon", "mirror", "needle", "ocean", "parrot",
    "quartz", "river", "silver", "temple", "umbrella",
    "velvet", "walrus", "yellow", "ballet", "cherry",
    "dragon", "forest", "hammer", "ivory", "jacket",
]

# Hangman ASCII art for each wrong-guess stage (0–6)
HANGMAN_ART: list[str] = [
    # 0 wrong
    "  ┌───┐\n"
    "  │    \n"
    "  │    \n"
    "  │    \n"
    " ─┴─  ",
    # 1 wrong — head
    "  ┌───┐\n"
    "  │   O\n"
    "  │    \n"
    "  │    \n"
    " ─┴─  ",
    # 2 wrong — body
    "  ┌───┐\n"
    "  │   O\n"
    "  │   |\n"
    "  │    \n"
    " ─┴─  ",
    # 3 wrong — left arm
    "  ┌───┐\n"
    "  │   O\n"
    "  │  /|\n"
    "  │    \n"
    " ─┴─  ",
    # 4 wrong — right arm
    "  ┌───┐\n"
    "  │   O\n"
    "  │  /|\\\n"
    "  │    \n"
    " ─┴─  ",
    # 5 wrong — left leg
    "  ┌───┐\n"
    "  │   O\n"
    "  │  /|\\\n"
    "  │  /  \n"
    " ─┴─  ",
    # 6 wrong — right leg (DEAD)
    "  ┌───┐\n"
    "  │   O\n"
    "  │  /|\\\n"
    "  │  / \\\n"
    " ─┴─   DEAD!",
]

MAX_WRONG = 6


class HangmanSession(BaseGameSession):
    """Multiplayer Hangman. Player 1 picks a word, Player 2 guesses letters."""

    def __init__(
        self, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(invite, on_state_change, on_score)
        players = self.invite.players
        self.picker: str = players[0] if players else ""
        self.guesser: str = players[1] if len(players) > 1 else ""
        self.word: str = ""  # the secret — never sent to clients
        self.guessed_letters: list[str] = []
        self.wrong_count: int = 0
        self.phase: str = "picking"  # picking → guessing → finished
        self._update_state()

    # ------------------------------------------------------------------
    # Action handling
    # ------------------------------------------------------------------

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active:
            return

        if action == "set_word":
            self._handle_set_word(player, data)
        elif action == "guess":
            self._handle_guess(player, data)

    def _handle_set_word(self, player: str, data: dict[str, Any]) -> None:
        if self.phase != "picking":
            return
        if player != self.picker:
            return

        raw = data.get("word", "")
        if not isinstance(raw, str):
            return
        word = raw.strip().lower()

        # Validate: only alphabetic, at least 2 chars
        if len(word) < 2 or not word.isalpha():
            return

        self.word = word
        self.phase = "guessing"
        self._update_state()
        self.broadcast_state()

    def _handle_guess(self, player: str, data: dict[str, Any]) -> None:
        if self.phase != "guessing":
            return
        if player != self.guesser:
            return

        raw = data.get("letter", "")
        if not isinstance(raw, str):
            return
        letter = raw.strip().lower()

        # Must be a single alphabetic character
        if len(letter) != 1 or not letter.isalpha():
            return
        # Already guessed
        if letter in self.guessed_letters:
            return

        self.guessed_letters.append(letter)

        if letter not in self.word:
            self.wrong_count += 1

        # Check end conditions
        if self.wrong_count >= MAX_WRONG:
            # Picker wins — guesser is "hanged"
            self.phase = "finished"
            self._update_state()
            self.end_game(winner=self.picker)
            return

        if self._word_complete():
            # Guesser wins
            self.phase = "finished"
            self._update_state()
            self.end_game(winner=self.guesser)
            return

        self._update_state()
        self.broadcast_state()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _word_display(self) -> list[str]:
        """Build the display list: revealed letters or '_' for unguessed."""
        if not self.word:
            return []
        return [ch if ch in self.guessed_letters else "_" for ch in self.word]

    def _word_complete(self) -> bool:
        """True when every letter in the word has been guessed."""
        return bool(self.word) and all(ch in self.guessed_letters for ch in self.word)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _update_state(self) -> None:
        self.state = {
            "phase": self.phase,
            "picker": self.picker,
            "guesser": self.guesser,
            "word_display": self._word_display(),
            "guessed_letters": list(self.guessed_letters),
            "wrong_count": self.wrong_count,
            "max_wrong": MAX_WRONG,
            "hangman_art": HANGMAN_ART[min(self.wrong_count, MAX_WRONG)],
            "players": list(self.invite.players),
            "word_list": WORD_LIST if self.phase == "picking" else [],
        }

    def get_render_state(self) -> dict[str, Any]:
        state = {"active": self.is_active, "winner": self.winner, **self.state}
        # Reveal the word once the game is finished (so both players can see it)
        if self.phase == "finished":
            state["word"] = self.word
        return state

    def get_final_score(self) -> dict[str, Any]:
        return {
            "winner": self.winner,
            "players": list(self.invite.players),
            "picker": self.picker,
            "guesser": self.guesser,
            "word": self.word,
            "wrong_count": self.wrong_count,
        }


@register_game
class HangmanGame(BaseGame):
    game_id = "hangman"
    name = "Hangman"
    description = "Pick a word or guess letters — 6 wrong guesses and you're hanged!"
    min_players = 2
    max_players = 2
    is_solo = False
    score_schema = {"wins": "Victoires", "losses": "Défaites"}

    @classmethod
    def create_session(
        cls, invite: GameInvite,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
        on_score: Callable[[dict[str, Any]], None] | None = None,
    ) -> HangmanSession:
        return HangmanSession(invite, on_state_change, on_score)
