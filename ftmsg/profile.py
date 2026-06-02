from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROFILE_DIR = Path.home() / ".42msg"
PROFILE_PATH = PROFILE_DIR / "profile.json"


def _load_json() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        return {}
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(data: dict[str, Any]) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class ProfileManager:
    """Local JSON profile: scores, basic identity."""

    def __init__(self, login: str) -> None:
        self.login = login
        self._data = _load_json()
        if self._data.get("login") != login:
            self._data = {"login": login, "scores": {}}
            _save_json(self._data)

    def record_score(self, game_id: str, score_dict: dict[str, Any]) -> None:
        """Record a game score. Updates best/max values and accumulates totals."""
        scores = self._data.setdefault("scores", {})
        current = scores.setdefault(game_id, {})

        for key, value in score_dict.items():
            if isinstance(value, dict):
                # nested dicts (e.g. per-player breakdown) — just overwrite
                current[key] = value
                continue
            if not isinstance(value, (int, float)):
                current[key] = value
                continue

            key_lower = key.lower()
            if any(w in key_lower for w in ("best", "max", "high", "top", "record")):
                old = current.get(key, 0)
                current[key] = max(old, value)
            else:
                current[key] = current.get(key, 0) + value

        _save_json(self._data)

    def get_game_score(self, game_id: str) -> dict[str, Any] | None:
        scores = self._data.get("scores", {})
        s = scores.get(game_id, {})
        return dict(s) if s else None

    def list_games_with_scores(self) -> list[str]:
        return list(self._data.get("scores", {}).keys())

    def get_all_scores(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._data.get("scores", {}).items()}

    def get_summary(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "bio": self._data.get("bio", "Aucune bio."),
            "status": self._data.get("status", "Disponible"),
            "scores": self.get_all_scores()
        }

    def update_profile(self, bio: str | None = None, status: str | None = None) -> None:
        if bio is not None:
            self._data["bio"] = bio
        if status is not None:
            self._data["status"] = status
        _save_json(self._data)
