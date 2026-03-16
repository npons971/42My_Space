from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


@dataclass(slots=True)
class TrustedIdentity:
    login: str
    signing_pubkey: str
    encryption_pubkey: str
    first_seen: float
    last_seen: float


class TrustStore:
    def __init__(self, db_path: Path | None = None) -> None:
        default_path = Path.home() / ".42msg" / "messages.db"
        self.db_path = db_path or default_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS trusted_identities (
                    login TEXT PRIMARY KEY,
                    signing_pubkey TEXT NOT NULL,
                    encryption_pubkey TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL
                )
                """
            )
            await db.commit()

    async def observe_peer(self, login: str, signing_pubkey: str, encryption_pubkey: str) -> str:
        existing = await self.get_identity(login)
        now = time.time()
        if existing is None:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO trusted_identities (login, signing_pubkey, encryption_pubkey, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (login, signing_pubkey, encryption_pubkey, now, now),
                )
                await db.commit()
            return "new"

        if existing.signing_pubkey == signing_pubkey and existing.encryption_pubkey == encryption_pubkey:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE trusted_identities SET last_seen = ? WHERE login = ?",
                    (now, login),
                )
                await db.commit()
            return "trusted"

        return "mismatch"

    async def get_identity(self, login: str) -> TrustedIdentity | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT login, signing_pubkey, encryption_pubkey, first_seen, last_seen
                FROM trusted_identities
                WHERE login = ?
                """,
                (login,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        return TrustedIdentity(
            login=row[0],
            signing_pubkey=row[1],
            encryption_pubkey=row[2],
            first_seen=row[3],
            last_seen=row[4],
        )
