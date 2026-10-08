"""aiogram FSM holatini SQLite faylida saqlash.

Vercel'da har so'rov alohida jarayonda ishlashi mumkin, shuning uchun MemoryStorage
bosqichma-bosqich dialoglarni (trigger/qarz qo'shish) "unutib" qo'yadi.
Bu storage holatni bot bilan bir xil SQLite fayliga yozadi, fayl esa Supabase'da saqlanadi.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @staticmethod
    def _key(key: StorageKey) -> str:
        return ":".join(
            str(x)
            for x in (
                key.bot_id,
                key.chat_id,
                key.user_id,
                key.thread_id,
                key.business_connection_id,
                key.destiny,
            )
        )

    async def _ensure(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS fsm_storage (key TEXT PRIMARY KEY, state TEXT, data TEXT)"
        )

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure(db)
            await db.execute(
                "INSERT INTO fsm_storage (key, state, data) VALUES (?, ?, '{}') "
                "ON CONFLICT(key) DO UPDATE SET state = excluded.state",
                (self._key(key), value),
            )
            await db.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure(db)
            cur = await db.execute("SELECT state FROM fsm_storage WHERE key = ?", (self._key(key),))
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure(db)
            await db.execute(
                "INSERT INTO fsm_storage (key, state, data) VALUES (?, NULL, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (self._key(key), json.dumps(dict(data), ensure_ascii=False)),
            )
            await db.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure(db)
            cur = await db.execute("SELECT data FROM fsm_storage WHERE key = ?", (self._key(key),))
            row = await cur.fetchone()
            return json.loads(row[0]) if row and row[0] else {}

    async def close(self) -> None:
        pass
