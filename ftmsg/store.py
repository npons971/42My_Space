from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass(slots=True)
class PendingMessage:
    id: int
    target_login: str
    target_ip: str | None
    target_port: int | None
    frame: dict[str, Any]
    status: str
    created_at: float
    last_error: str | None


class MessageStore:
    def __init__(self, db_path: Path | None = None) -> None:
        default_path = Path.home() / ".42msg" / "messages.db"
        self.db_path = db_path or default_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_login TEXT NOT NULL,
                    target_ip TEXT,
                    target_port INTEGER,
                    frame_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_error TEXT
                )
                """
            )
            await db.commit()

    async def add_pending(
        self,
        target_login: str,
        frame: dict[str, Any],
        target_ip: str | None = None,
        target_port: int | None = None,
        last_error: str | None = None,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO outbound_messages
                (target_login, target_ip, target_port, frame_json, status, created_at, last_error)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (target_login, target_ip, target_port, json.dumps(frame), time.time(), last_error),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def list_pending_for_login(self, target_login: str) -> list[PendingMessage]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, target_login, target_ip, target_port, frame_json, status, created_at, last_error
                FROM outbound_messages
                WHERE target_login = ? AND status = 'pending'
                ORDER BY created_at ASC
                """,
                (target_login,),
            )
            rows = await cursor.fetchall()

        return [
            PendingMessage(
                id=row[0],
                target_login=row[1],
                target_ip=row[2],
                target_port=row[3],
                frame=json.loads(row[4]),
                status=row[5],
                created_at=row[6],
                last_error=row[7],
            )
            for row in rows
        ]

    async def mark_sent(self, message_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE outbound_messages SET status = 'sent', last_error = NULL WHERE id = ?",
                (message_id,),
            )
            await db.commit()

    async def set_error(self, message_id: int, error: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE outbound_messages SET last_error = ? WHERE id = ?",
                (error, message_id),
            )
            await db.commit()
