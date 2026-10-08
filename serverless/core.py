"""Vercel funksiyalari uchun umumiy kod: webhook, setup, cron.

Ishlash tartibi (har bir so'rovda):
  1. SQLite bazasi Supabase Storage'dan /tmp ga yuklab olinadi
  2. bot/ dagi asl handlerlar update'ni qayta ishlaydi
  3. Baza o'zgargan bo'lsa, Supabase'ga qayta yuklanadi
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Vercel'da faqat /tmp yoziladi. bot.config import qilinishidan OLDIN o'rnatiladi.
DB_PATH = "/tmp/bot_data.sqlite3"
os.environ["DB_PATH"] = DB_PATH

import aiohttp  # noqa: E402  (aiogram bilan birga o'rnatiladi)
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.types import Update  # noqa: E402

from bot.config import settings  # noqa: E402
from bot.services.storage import Database  # noqa: E402
from serverless.fsm_storage import SQLiteStorage  # noqa: E402

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
)
logger = logging.getLogger("serverless")

# main.py dagi ro'yxat bilan bir xil
ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]

BUCKET = os.environ.get("SUPABASE_BUCKET", "telegram-bot")
OBJECT_NAME = "bot_data.sqlite3"


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def webhook_secret() -> str:
    """Telegram har webhook so'rovida yuboradigan maxfiy belgi (tokendan hosil qilinadi)."""
    return hashlib.sha256(("webhook:" + settings.BOT_TOKEN).encode()).hexdigest()[:48]


# ---------------- Supabase Storage (SQLite faylni saqlash) ----------------


class StorageError(RuntimeError):
    pass


class DownloadError(StorageError):
    """Bazani yuklab bo'lmadi: update qayta ishlanmaydi, Telegram keyinroq qayta yuboradi."""


def _supabase() -> Tuple[str, Dict[str, str]]:
    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise StorageError("SUPABASE_URL yoki SUPABASE_SERVICE_ROLE_KEY Vercel'da kiritilmagan")
    headers = {"apikey": key}
    # Eski JWT kalitlar (eyJ...) Authorization ham talab qiladi; yangi sb_secret_ kalitlarga apikey yetarli
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return url, headers


async def ensure_bucket() -> str:
    url, headers = _supabase()
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{url}/storage/v1/bucket",
            headers=headers,
            json={"id": BUCKET, "name": BUCKET, "public": False},
        ) as r:
            body = await r.text()
            if r.status in (200, 201):
                return "created"
            if "already exists" in body.lower() or "duplicate" in body.lower() or r.status == 409:
                return "ok"
            raise StorageError(f"Bucket yaratib bo'lmadi ({r.status}): {body[:300]}")


async def download_db() -> bool:
    """Bazani /tmp ga yuklaydi. Fayl hali yo'q bo'lsa False qaytaradi."""
    url, headers = _supabase()
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/storage/v1/object/{BUCKET}/{OBJECT_NAME}", headers=headers) as r:
            if r.status == 200:
                Path(DB_PATH).write_bytes(await r.read())
                return True
            body = await r.text()
            if r.status in (400, 404) and "not found" in body.lower():
                # Birinchi ishga tushirish: bo'sh baza yaratiladi
                Path(DB_PATH).unlink(missing_ok=True)
                return False
            # Boshqa xatoda davom etmaymiz, aks holda bo'sh baza haqiqiy bazaning ustiga yozilib ketadi
            raise DownloadError(f"Bazani yuklab bo'lmadi ({r.status}): {body[:300]}")


async def upload_db() -> None:
    url, headers = _supabase()
    data = Path(DB_PATH).read_bytes()
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{url}/storage/v1/object/{BUCKET}/{OBJECT_NAME}",
            headers={**headers, "x-upsert": "true", "Content-Type": "application/octet-stream"},
            data=data,
        ) as r:
            if r.status not in (200, 201):
                raise StorageError(f"Bazani saqlab bo'lmadi ({r.status}): {(await r.text())[:300]}")


def _db_hash() -> Optional[str]:
    p = Path(DB_PATH)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


# ---------------- Bot / Dispatcher ----------------

_dispatcher: Optional[Dispatcher] = None
_db = Database(db_path=DB_PATH)


def get_dispatcher() -> Dispatcher:
    """Dispatcher jarayon davomida bir marta yaratiladi (routerlar faqat bir marta ulanadi)."""
    global _dispatcher
    if _dispatcher is None:
        from bot.handlers.admin import setup_admin_router
        from bot.handlers.business import setup_business_router

        dp = Dispatcher(storage=SQLiteStorage(DB_PATH))
        dp.include_router(setup_admin_router(_db))
        dp.include_router(setup_business_router(_db))
        _dispatcher = dp
    return _dispatcher


def new_bot() -> Bot:
    return Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def _with_db(work) -> Any:
    """Bazani yuklab olish -> ishni bajarish -> o'zgargan bo'lsa saqlash."""
    await download_db()
    await _db.init_db()
    before = _db_hash()
    try:
        return await work()
    finally:
        if _db_hash() != before:
            await upload_db()


# ---------------- Endpointlar logikasi ----------------


async def handle_update(payload: Dict[str, Any]) -> None:
    dp = get_dispatcher()
    bot = new_bot()

    async def work():
        update = Update.model_validate(payload, context={"bot": bot})
        await dp.feed_update(bot, update)

    try:
        await _with_db(work)
    finally:
        await bot.session.close()


async def run_setup(base_url: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False}
    result["storage"] = await ensure_bucket()

    await _with_db(lambda: _noop())
    result["baza"] = "ok"

    bot = new_bot()
    try:
        me = await bot.get_me()
        result["bot"] = "@" + (me.username or "")
        webhook_url = f"{base_url.rstrip('/')}/api/webhook"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=webhook_secret(),
            allowed_updates=ALLOWED_UPDATES,
            max_connections=1,  # update'lar navbat bilan keladi -> baza fayli to'qnashmaydi
            drop_pending_updates=False,
        )
        info = await bot.get_webhook_info()
        result["webhook"] = {
            "url": info.url,
            "pending": info.pending_update_count,
            "last_error": info.last_error_message,
        }
        result["ok"] = True
    finally:
        await bot.session.close()
    return result


async def _noop() -> None:
    return None


async def run_daily_reminders() -> Dict[str, Any]:
    from bot.services.scheduler import run_daily_reminder_round

    bot = new_bot()
    try:
        return await _with_db(lambda: run_daily_reminder_round(bot, _db))
    finally:
        await bot.session.close()
