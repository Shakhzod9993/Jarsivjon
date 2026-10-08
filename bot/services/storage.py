"""Persistent SQLite storage for Telegram Business connections and triggers."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    """Asynchronous SQLite database manager."""

    def __init__(self, db_path: str = "bot_data.sqlite3") -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        """Initialize database tables and seed default triggers if empty."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Table: business connections
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS business_connections (
                    connection_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    user_chat_id INTEGER,
                    can_reply INTEGER NOT NULL DEFAULT 1,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # Table: triggers
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS triggers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL UNIQUE,
                    display_keyword TEXT NOT NULL,
                    response TEXT NOT NULL,
                    target TEXT NOT NULL, -- 'owner', 'interlocutor', 'both'
                    match_type TEXT NOT NULL, -- 'exact', 'contains'
                    msg_type TEXT NOT NULL DEFAULT 'text', -- 'text', 'photo', 'document', 'sticker'
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # Table: debtors
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS debtors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    username TEXT,
                    amount TEXT NOT NULL,
                    comment TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP,
                    last_notified_at TIMESTAMP
                );
                """
            )

            # Table: known users (contacts who communicated with bot)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS known_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            await db.commit()

            # Seed default triggers if none exist
            cursor = await db.execute("SELECT COUNT(*) AS count FROM triggers")
            row = await cursor.fetchone()
            count = row["count"] if row else 0

            if count == 0:
                logger.info("Database is empty. Seeding default triggers...")
                default_triggers = [
                    # Interlocutor card triggers (all map to card number in <code> tag)
                    (
                        "скинь карту",
                        "скинь карту",
                        "Номер карты:\n<code>{CARD_NUMBER}</code>",
                        "interlocutor",
                        "contains",
                        "text",
                    ),
                    (
                        "кинь карту",
                        "кинь карту",
                        "Номер карты:\n<code>{CARD_NUMBER}</code>",
                        "interlocutor",
                        "contains",
                        "text",
                    ),
                    (
                        "дай карту",
                        "дай карту",
                        "Номер карты:\n<code>{CARD_NUMBER}</code>",
                        "interlocutor",
                        "contains",
                        "text",
                    ),
                    (
                        "номер карты",
                        "номер карты",
                        "Номер карты:\n<code>{CARD_NUMBER}</code>",
                        "interlocutor",
                        "contains",
                        "text",
                    ),
                    # Owner triggers
                    (
                        ".card",
                        ".card",
                        "<code>{CARD_NUMBER}</code>",
                        "owner",
                        "exact",
                        "text",
                    ),
                    (
                        ".hello",
                        ".hello",
                        "Здравствуйте! Рад приветствовать вас. Чем могу помочь?",
                        "owner",
                        "exact",
                        "text",
                    ),
                    (
                        ".thanks",
                        ".thanks",
                        "Пожалуйста! Рад был помочь. Обращайтесь в любое время!",
                        "owner",
                        "exact",
                        "text",
                    ),
                ]

                for item in default_triggers:
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO triggers 
                        (keyword, display_keyword, response, target, match_type, msg_type)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        item,
                    )
                await db.commit()
                logger.info(f"Seeded {len(default_triggers)} default triggers.")

    # ---------------- Business Connection Methods ----------------

    async def save_business_connection(
        self,
        connection_id: str,
        user_id: int,
        user_chat_id: Optional[int],
        can_reply: bool,
        is_enabled: bool,
    ) -> None:
        """Create or update a business connection record."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO business_connections (connection_id, user_id, user_chat_id, can_reply, is_enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(connection_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    user_chat_id=excluded.user_chat_id,
                    can_reply=excluded.can_reply,
                    is_enabled=excluded.is_enabled,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    connection_id,
                    user_id,
                    user_chat_id,
                    1 if can_reply else 0,
                    1 if is_enabled else 0,
                ),
            )
            await db.commit()
            logger.info(
                f"Saved business connection: id={connection_id}, user={user_id}, "
                f"can_reply={can_reply}, is_enabled={is_enabled}"
            )

    async def get_business_connection(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a business connection by its ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM business_connections WHERE connection_id = ?",
                (connection_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_business_connections(self) -> List[Dict[str, Any]]:
        """Fetch all stored business connections."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM business_connections ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_active_connection_for_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get the latest active business connection for the given user ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM business_connections 
                WHERE user_id = ? AND is_enabled = 1
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---------------- Triggers Methods ----------------

    async def add_trigger(
        self,
        keyword: str,
        display_keyword: str,
        response: str,
        target: str = "both",
        match_type: str = "exact",
        msg_type: str = "text",
    ) -> int:
        """Insert or replace a trigger. Returns trigger ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO triggers (keyword, display_keyword, response, target, match_type, msg_type)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                    display_keyword=excluded.display_keyword,
                    response=excluded.response,
                    target=excluded.target,
                    match_type=excluded.match_type,
                    msg_type=excluded.msg_type
                RETURNING id
                """,
                (keyword, display_keyword, response, target, match_type, msg_type),
            )
            row = await cursor.fetchone()
            await db.commit()
            return int(row[0]) if row else 0

    async def delete_trigger(self, identifier: str) -> bool:
        """Delete a trigger by id or keyword."""
        async with aiosqlite.connect(self.db_path) as db:
            if identifier.isdigit():
                cursor = await db.execute("DELETE FROM triggers WHERE id = ?", (int(identifier),))
            else:
                cursor = await db.execute("DELETE FROM triggers WHERE keyword = ? OR display_keyword = ?", (identifier, identifier))
            await db.commit()
            return cursor.rowcount > 0

    async def get_all_triggers(self) -> List[Dict[str, Any]]:
        """Fetch all configured triggers."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM triggers ORDER BY id ASC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_triggers_for_target(self, target: str) -> List[Dict[str, Any]]:
        """Fetch triggers configured for target ('owner', 'interlocutor') or 'both'."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM triggers WHERE target = ? OR target = 'both' ORDER BY id ASC",
                (target,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_trigger_by_keyword(self, keyword: str) -> Optional[Dict[str, Any]]:
        """Fetch a single trigger by normalized keyword."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM triggers WHERE keyword = ?", (keyword,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---------------- Debtors Methods ----------------

    async def add_debtor(
        self,
        user_id: int,
        name: str,
        amount: str,
        comment: str = "",
        username: Optional[str] = None,
    ) -> int:
        """Add a debtor to tracking. Returns inserted debtor record ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO debtors (user_id, name, username, amount, comment, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
                RETURNING id
                """,
                (user_id, name, username, amount, comment),
            )
            row = await cursor.fetchone()
            await db.commit()
            return int(row[0]) if row else 0

    async def get_active_debtors(self) -> List[Dict[str, Any]]:
        """Fetch all currently active debtors."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM debtors WHERE is_active = 1 ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_debtor_by_id(self, debtor_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a debtor by record ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM debtors WHERE id = ?", (debtor_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_active_debts_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        """Fetch all active debts for a given telegram user ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM debtors WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def close_debt(self, debtor_id: int) -> bool:
        """Mark a debt as paid/closed."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE debtors
                SET is_active = 0, closed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND is_active = 1
                """,
                (debtor_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_debtor(self, debtor_id: int) -> bool:
        """Completely delete a debtor record from database."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM debtors WHERE id = ?", (debtor_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_last_notified(self, debtor_id: int) -> None:
        """Update last_notified_at timestamp for a debtor."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE debtors SET last_notified_at = CURRENT_TIMESTAMP WHERE id = ?",
                (debtor_id,),
            )
            await db.commit()

    # ---------------- Known Users Methods ----------------

    async def save_known_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
    ) -> None:
        """Register or update a known user who interacted with the bot."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO known_users (user_id, username, full_name, last_seen)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, known_users.username),
                    full_name = COALESCE(excluded.full_name, known_users.full_name),
                    last_seen = CURRENT_TIMESTAMP
                """,
                (user_id, username.lstrip("@").lower() if username else None, full_name),
            )
            await db.commit()

    async def get_known_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Find a known user by user ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM known_users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_known_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Find a known user by username."""
        clean_username = username.lstrip("@").lower()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM known_users WHERE LOWER(username) = ?",
                (clean_username,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

