#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Professional Telegram Kino Bot  (Aiogram 3, Python 3.10+)

Bitta fayl: bot.py. Barcha ma'lumotlar JSON fayllarda (DATA_DIR papkasida).

Ishga tushirish:
    pip install -r requirements.txt
    cp .env.example .env     # BOT_TOKEN va MAIN_ADMIN_ID ni to'ldiring
    python bot.py
"""
from __future__ import annotations

import asyncio
import calendar
import contextlib
import copy
import html
import io
import ipaddress
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    CopyTextButton,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

logger = logging.getLogger("kinobot")

# =====================================================================
# 1. DOIMIYLAR
# =====================================================================
ROLE_MOVIE = "movie"   # 1-tur: Kino admini
ROLE_FULL = "full"     # 2-tur: To'liq admin
ROLE_MAIN = "main"     # 3-tur: Asosiy admin

ROLE_LABELS = {
    ROLE_MOVIE: "🎬 Kino admini (1-tur)",
    ROLE_FULL: "🛡 To‘liq admin (2-tur)",
    ROLE_MAIN: "👑 Asosiy admin (3-tur)",
}
ROLE_DESCRIPTIONS = {
    ROLE_MOVIE: "kino qo‘shish; faqat o‘zi qo‘shgan kinolarni ko‘rish va o‘chirish",
    ROLE_FULL: "kino boshqaruvi (hamma kino), majburiy obuna, statistika, admin boshqaruvi (faqat kino adminlari), VIP boshqaruvi, kanalga post joylash",
    ROLE_MAIN: "barcha huquqlar: to‘lovlarni tasdiqlash, backup/tiklash, sozlamalar, broadcast, rollarni o‘zgartirish",
}

_MOVIE_PERMS = {"movie_menu", "movie_add", "movie_view_own", "movie_delete_own"}
_FULL_PERMS = _MOVIE_PERMS | {
    "movie_view_all", "movie_delete_all", "channels", "stats", "admins", "vip", "channel_post",
}
_MAIN_PERMS = _FULL_PERMS | {
    "payments", "backup", "restore", "settings", "broadcast", "admin_roles", "admin_manage_full",
}
ROLE_PERMS = {ROLE_MOVIE: _MOVIE_PERMS, ROLE_FULL: _FULL_PERMS, ROLE_MAIN: _MAIN_PERMS}


def has_perm(role: Optional[str], perm: str) -> bool:
    return bool(role) and perm in ROLE_PERMS.get(role, set())


PLANS: dict[str, dict[str, Any]] = {
    "d15": {"label": "15 kunlik", "price": 10_000, "days": 15},
    "d30": {"label": "30 kunlik", "price": 15_000, "days": 30},
    "m2": {"label": "2 oylik", "price": 30_000, "months": 2},
    "y1": {"label": "1 yillik", "price": 150_000, "months": 12},
}

PAYMENT_TIMEOUT_MIN = 30
MAX_TITLE_LEN = 100
MAX_DESC_LEN = 800          # video caption limiti 1024 belgi, shuning uchun xavfsiz chegara
SEARCH_LIMIT = 20
PAGE_SIZE = 8
BROADCAST_DELAY = 0.05      # ~20 xabar/soniya (Telegram limiti ~30/soniya)

BTN_SEARCH = "🎬 Kino qidirish"
BTN_VIP = "💎 VIP obuna"
BTN_CHANNELS = "📢 Obuna kanallari"
BTN_PROFILE = "👤 Profilim"
BTN_ADMIN = "🛠 Admin panel"

# Admin panelning asosiy menyusi (ReplyKeyboardMarkup) tugmalari
ADM_BTN_MOVIES = "🎬 Kino boshqaruvi"
ADM_BTN_CHANNELS = "📢 Majburiy obuna"
ADM_BTN_STATS = "📊 Statistika"
ADM_BTN_ADMINS = "👮 Admin boshqaruvi"
ADM_BTN_VIP = "💎 VIP boshqaruvi"
ADM_BTN_POST = "📢 Kanalga post joylash"
ADM_BTN_BACKUP = "💾 Backup"
ADM_BTN_SETTINGS = "⚙️ Sozlamalar"
ADM_BTN_BROADCAST = "📣 Broadcast"
ADM_BTN_BACK = "⬅️ Asosiy menyu"
MENU_BUTTONS = {BTN_SEARCH, BTN_VIP, BTN_CHANNELS, BTN_PROFILE, BTN_ADMIN}

WEEKDAYS_UZ = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]

DEFAULT_SETTINGS = {
    "bot_name": "",
    "contact_username": "@senzaifsan",
    "subscription_enabled": True,
    "card_holder": "",
    "card_number": "",
    "payment_note": "",
    "next_movie_id": 1,
    "next_movie_code": 11,          # kino kodlari 11 dan boshlanadi; hisoblagich faqat oshadi (o'chirilgan kodlar qayta berilmaydi)
    "next_payment_no": 1,
    "next_grant_no": 1,
    "next_broadcast_no": 1,
    "next_post_no": 1,
}
DEFAULT_STATS = {
    "usage_by_day": {},
    "usage_by_hour": {},
    "usage_by_weekday": {},
    "admin_action_counts": {},
    "admin_log": [],
}
DEFAULT_DATA: dict[str, Any] = {
    "users": {},
    "movies": {},
    "admins": {},
    "channels": {},
    "vip": {"grants": {}},
    "payments": {},
    "statistics": DEFAULT_STATS,
    "settings": DEFAULT_SETTINGS,
    "join_requests": {},
    "broadcast": {"current": None, "history": []},
    "channel_posts": {},
}
MERGE_DEFAULTS = {"vip", "statistics", "settings", "broadcast"}


# =====================================================================
# 2. YORDAMCHI FUNKSIYALAR
# =====================================================================
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def add_months(dt: datetime, months: int) -> datetime:
    """Kalendar oy qo'shish (oy oxiri kunlari to'g'rilanadi: 31-yanvar + 1 oy = 28/29-fevral)."""
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def fmt_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%d.%m.%Y %H:%M UTC") if dt else "—"


def fmt_date(dt: Optional[datetime]) -> str:
    return dt.strftime("%d.%m.%Y") if dt else "—"


def fmt_money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " so‘m"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


_APOS = dict.fromkeys(map(ord, "'’ʻʼ`‘´"), None)


def norm(text: str) -> str:
    """Qidiruv uchun normallashtirish: kichik harf, ortiqcha bo'shliqsiz, apostrofsiz."""
    return " ".join(str(text).casefold().translate(_APOS).split())


def trunc(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def user_link_name(u: dict) -> str:
    name = u.get("first_name") or "Foydalanuvchi"
    return f'<a href="tg://user?id={u["user_id"]}">{esc(name)}</a>'


async def tg_retry(factory: Callable[[], Awaitable[Any]], tries: int = 3) -> Any:
    """FloodWait (RetryAfter) va vaqtinchalik tarmoq xatolarida qayta urinish."""
    for attempt in range(tries):
        try:
            return await factory()
        except TelegramRetryAfter as e:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
        except (TelegramNetworkError, TelegramServerError):
            if attempt == tries - 1:
                raise
            await asyncio.sleep(1 + attempt)


class RedactFilter(logging.Filter):
    """Bot tokeni va boshqa maxfiy qiymatlarni logdan olib tashlaydi."""

    def __init__(self, secrets_list: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets_list if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        for s in self.secrets:
            if s in msg:
                msg = msg.replace(s, "***")
        record.msg, record.args = msg, ()
        return True


# =====================================================================
# 3. JSON MANAGER
# =====================================================================
class JsonDB:
    """
    Xavfsiz JSON saqlash:
      * UTF-8, atomik yozish (tmp -> fsync -> os.replace),
      * oldingi versiya .bak sifatida saqlanadi (buzilishda avtomatik tiklanadi),
      * har bir fayl uchun alohida asyncio.Lock (parallel yozuvlar to'qnashmaydi),
      * xotirada kesh: o'qish/o'zgartirish sinxron, shuning uchun bitta event loopda
        ma'lumotlar yo'qolmaydi,
      * tez-tez o'zgaradigan ma'lumotlar uchun "dirty" belgisi va davriy flush.
    """

    FILES = (
        "users", "movies", "admins", "channels", "vip", "payments",
        "statistics", "settings", "join_requests", "broadcast", "channel_posts",
    )

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.dirty: set[str] = set()
        for name in self.FILES:
            self.data[name] = self._load(name)

    # ---- yo'llar ----
    def path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    # ---- yuklash ----
    def _load(self, name: str) -> Any:
        path = self.path(name)
        default = copy.deepcopy(DEFAULT_DATA[name])
        if not path.exists():
            self._write_sync(name, self._dumps(default))
            return default
        bak = path.with_suffix(".json.bak")
        for cand in (path, bak):
            try:
                obj = json.loads(cand.read_text(encoding="utf-8"))
                if not isinstance(obj, type(default)):
                    raise ValueError("fayl turi noto‘g‘ri")
                if name in MERGE_DEFAULTS:
                    for k, v in default.items():
                        obj.setdefault(k, copy.deepcopy(v))
                if cand != path:
                    logger.error("%s.json buzilgan edi — .bak nusxadan tiklandi", name)
                    self._write_sync(name, self._dumps(obj))
                return obj
            except (OSError, ValueError) as e:
                logger.error("%s o‘qishda xato: %s", cand.name, e)
        broken = path.with_name(f"{name}.json.corrupt-{int(time.time())}")
        with contextlib.suppress(OSError):
            path.rename(broken)
        logger.error("%s.json tiklab bo‘lmadi, bo‘sh holat yaratildi (eski fayl: %s)", name, broken.name)
        self._write_sync(name, self._dumps(default))
        return default

    # ---- yozish ----
    @staticmethod
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=1)

    def _write_sync(self, name: str, text: str) -> None:
        path = self.path(name)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            with contextlib.suppress(OSError):
                shutil.copy2(path, path.with_suffix(".json.bak"))
        os.replace(tmp, path)

    async def save(self, name: str) -> None:
        self.dirty.discard(name)
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            text = self._dumps(self.data[name])  # snapshot lock ichida olinadi => eng yangi holat
            try:
                await asyncio.to_thread(self._write_sync, name, text)
            except OSError as e:
                logger.error("%s.json yozishda xato: %s", name, e)
                self.dirty.add(name)

    def touch(self, name: str) -> None:
        self.dirty.add(name)

    async def flush(self) -> None:
        for name in list(self.dirty):
            await self.save(name)

    async def save_many(self, *names: str) -> None:
        for n in names:
            await self.save(n)

    def snapshot(self) -> dict[str, str]:
        """Barcha fayllarning bir lahzadagi izchil nusxasi (await yo'q => parallel yozuv aralashmaydi)."""
        return {f"{n}.json": self._dumps(self.data[n]) for n in self.FILES}

    def replace_all(self, texts: dict[str, str]) -> None:
        for fname, text in texts.items():
            name = fname[:-5]
            if name in self.FILES:
                obj = json.loads(text)
                default = copy.deepcopy(DEFAULT_DATA[name])
                if name in MERGE_DEFAULTS:
                    for k, v in default.items():
                        obj.setdefault(k, v)
                self.data[name] = obj
                self.dirty.add(name)


# =====================================================================
# 4. MANAGERLAR
# =====================================================================
class SubCheckError(Exception):
    """Obunani tekshirishda vaqtinchalik (tarmoq/server) xato."""


class UserManager:
    def __init__(self, app: "App"):
        self.app = app

    @property
    def items(self) -> dict[str, dict]:
        return self.app.db.data["users"]

    def get(self, uid: int) -> Optional[dict]:
        return self.items.get(str(uid))

    def upsert(self, tg_user) -> tuple[dict, bool]:
        key = str(tg_user.id)
        now = iso(utcnow())
        u = self.items.get(key)
        if u is None:
            u = {
                "user_id": tg_user.id,
                "first_name": tg_user.first_name or "",
                "username": tg_user.username,
                "joined_at": now,
                "last_active": now,
                "is_blocked": False,
                "vip_until": None,
                "watched_movies": [],
            }
            self.items[key] = u
            return u, True
        u["first_name"] = tg_user.first_name or u.get("first_name", "")
        u["username"] = tg_user.username
        u["last_active"] = now
        u["is_blocked"] = False
        return u, False

    def vip_until(self, u: Optional[dict]) -> Optional[datetime]:
        if not u:
            return None
        dt = parse_iso(u.get("vip_until"))
        return dt if dt and dt > utcnow() else None

    def is_vip(self, uid: int) -> bool:
        return self.vip_until(self.get(uid)) is not None

    def set_blocked(self, uid: int, flag: bool) -> None:
        u = self.get(uid)
        if u and u.get("is_blocked") != flag:
            u["is_blocked"] = flag
            self.app.db.touch("users")

    def add_watched(self, uid: int, movie_id: int) -> None:
        u = self.get(uid)
        if u is not None and movie_id not in u["watched_movies"]:
            u["watched_movies"].append(movie_id)


class MovieManager:
    def __init__(self, app: "App"):
        self.app = app

    @property
    def items(self) -> dict[str, dict]:
        return self.app.db.data["movies"]

    def get(self, movie_id: Any) -> Optional[dict]:
        return self.items.get(str(movie_id))

    def by_code(self, code: str) -> Optional[dict]:
        code = code.strip()
        return next((m for m in self.items.values() if m["code"] == code), None)

    def by_unique(self, unique_id: str) -> Optional[dict]:
        if not unique_id:
            return None
        return next((m for m in self.items.values() if m.get("file_unique_id") == unique_id), None)

    def sync_code_counter(self) -> None:
        """Hisoblagich hech qachon mavjud kodlardan past bo'lmasin (buzilish/eski backup holatlaridan himoya).
        Eski standart (1001) bilan o'rnatilgan, lekin hali birorta kino qo'shilmagan bot 11 dan boshlaydi."""
        s = self.app.settings
        if not self.items and s.get("next_movie_code") == 1001:
            s["next_movie_code"] = 11
            self.app.db.touch("settings")
        top = max((int(m["code"]) for m in self.items.values() if str(m.get("code", "")).isdigit()), default=0)
        if s["next_movie_code"] <= top:
            s["next_movie_code"] = top + 1
            self.app.db.touch("settings")

    def _next_code(self) -> str:
        s = self.app.settings
        while True:
            code = str(s["next_movie_code"])
            s["next_movie_code"] += 1
            if not self.by_code(code):
                return code

    def add(self, title: str, description: str, file_id: str, file_unique_id: str, added_by: int) -> dict:
        s = self.app.settings
        movie_id = s["next_movie_id"]
        s["next_movie_id"] += 1
        movie = {
            "movie_id": movie_id,
            "code": self._next_code(),
            "title": title,
            "description": description,
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "added_by": added_by,
            "added_at": iso(utcnow()),
            "views_count": 0,
        }
        self.items[str(movie_id)] = movie
        return movie

    def delete(self, movie_id: int) -> bool:
        return self.items.pop(str(movie_id), None) is not None

    def search(self, query: str, owner: Optional[int] = None, limit: int = SEARCH_LIMIT) -> list[dict]:
        q = norm(query)
        if not q:
            return []
        tokens = q.split()
        scored: list[tuple[int, int, str, dict]] = []
        for m in self.items.values():
            if owner is not None and m["added_by"] != owner:
                continue
            t = norm(m["title"])
            if q == m["code"]:
                score = 0
            elif t == q:
                score = 1
            elif t.startswith(q):
                score = 2
            elif q in t:
                score = 3
            elif len(tokens) > 1 and all(tok in t for tok in tokens):
                score = 4
            else:
                continue
            scored.append((score, -m.get("views_count", 0), t, m))
        scored.sort(key=lambda x: x[:3])
        return [x[3] for x in scored[:limit]]

    def listing(self, owner: Optional[int] = None) -> list[dict]:
        ms = [m for m in self.items.values() if owner is None or m["added_by"] == owner]
        return sorted(ms, key=lambda m: m["added_at"], reverse=True)

    def caption(self, m: dict) -> str:
        return f"🎬 <b>{esc(m['title'])}</b>\n\n{esc(m['description'])}"


class AdminManager:
    def __init__(self, app: "App"):
        self.app = app

    @property
    def items(self) -> dict[str, dict]:
        return self.app.db.data["admins"]

    def ensure_main(self) -> None:
        main = self.app.main_admin_id
        for k, a in list(self.items.items()):
            if a.get("role") == ROLE_MAIN and int(k) != main:
                a["role"] = ROLE_FULL
                self.app.db.touch("admins")
        rec = self.items.get(str(main))
        if not rec or rec.get("role") != ROLE_MAIN:
            self.items[str(main)] = {
                "user_id": main,
                "first_name": (rec or {}).get("first_name", "Asosiy admin"),
                "role": ROLE_MAIN,
                "added_by": main,
                "added_at": (rec or {}).get("added_at", iso(utcnow())),
            }
            self.app.db.touch("admins")

    def role_of(self, uid: int) -> Optional[str]:
        if uid == self.app.main_admin_id:
            return ROLE_MAIN
        rec = self.items.get(str(uid))
        if rec and rec.get("role") in (ROLE_MOVIE, ROLE_FULL):
            return rec["role"]
        return None

    def add(self, uid: int, role: str, added_by: int, name: str) -> dict:
        rec = {"user_id": uid, "first_name": name, "role": role, "added_by": added_by, "added_at": iso(utcnow())}
        self.items[str(uid)] = rec
        return rec

    def remove(self, uid: int) -> bool:
        return self.items.pop(str(uid), None) is not None

    def listing(self) -> list[dict]:
        order = {ROLE_MAIN: 0, ROLE_FULL: 1, ROLE_MOVIE: 2}
        return sorted(self.items.values(), key=lambda a: (order.get(a["role"], 9), a["added_at"]))


class SubscriptionManager:
    """Majburiy obuna: ochiq va private (join request) kanallar/guruhlar."""

    OK_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}

    def __init__(self, app: "App"):
        self.app = app
        self._alerted: dict[str, float] = {}

    @property
    def channels(self) -> dict[str, dict]:
        return self.app.db.data["channels"]

    @property
    def requests(self) -> dict[str, dict]:
        return self.app.db.data["join_requests"]

    def active_channels(self) -> list[dict]:
        return [c for c in self.channels.values() if c.get("is_active")]

    def record_join_request(self, chat_id: int, user_id: int) -> None:
        self.requests.setdefault(str(chat_id), {})[str(user_id)] = iso(utcnow())
        self.app.db.touch("join_requests")

    def clear_join_request(self, chat_id: int, user_id: int) -> None:
        if self.requests.get(str(chat_id), {}).pop(str(user_id), None):
            self.app.db.touch("join_requests")

    def has_join_request(self, chat_id: int, user_id: int) -> bool:
        return str(user_id) in self.requests.get(str(chat_id), {})

    async def _alert_admin(self, bot: Bot, ch: dict, reason: str) -> None:
        key = str(ch["channel_id"])
        if time.time() - self._alerted.get(key, 0) < 3600:
            return
        self._alerted[key] = time.time()
        with contextlib.suppress(Exception):
            await bot.send_message(
                self.app.main_admin_id,
                f"⚠️ <b>Majburiy obuna muammosi</b>\n\nKanal: {esc(ch['title'])} (<code>{ch['channel_id']}</code>)\n"
                f"Sabab: {esc(reason)}\n\nBot kanalda administrator ekanligini tekshiring. "
                "Muammo hal bo‘lguncha bu kanal tekshiruvdan o‘tkazib yuboriladi.",
            )

    async def is_member(self, bot: Bot, ch: dict, user_id: int) -> bool:
        """True = haqiqiy a'zo. Join request yuborilgan, lekin tasdiqlanmagan bo'lsa False."""
        for attempt in range(3):
            try:
                m = await bot.get_chat_member(ch["channel_id"], user_id)
                ok = m.status in self.OK_STATUSES or (
                    m.status == ChatMemberStatus.RESTRICTED and getattr(m, "is_member", False)
                )
                if ok:
                    self.clear_join_request(ch["channel_id"], user_id)
                return ok
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "user not found" in msg or "participant_id_invalid" in msg or "user_id_invalid" in msg:
                    return False
                logger.warning("Kanal %s sozlamasida xato: %s", ch["channel_id"], e)
                await self._alert_admin(bot, ch, str(e))
                return True  # sozlama xatosi: foydalanuvchini jazolamaymiz
            except TelegramForbiddenError as e:
                logger.warning("Kanal %s: bot huquqi yo‘q: %s", ch["channel_id"], e)
                await self._alert_admin(bot, ch, str(e))
                return True
            except (TelegramNetworkError, TelegramServerError) as e:
                if attempt == 2:
                    raise SubCheckError(str(e)) from e
                await asyncio.sleep(1 + attempt)
        raise SubCheckError("RetryAfter limiti")

    async def statuses(self, bot: Bot, user_id: int) -> list[tuple[dict, bool]]:
        chans = self.active_channels()
        results = await asyncio.gather(*(self.is_member(bot, c, user_id) for c in chans), return_exceptions=True)
        out = []
        for c, r in zip(chans, results):
            if isinstance(r, SubCheckError):
                raise r
            if isinstance(r, Exception):
                logger.error("Obuna tekshirish xatosi: %r", r)
                raise SubCheckError(repr(r))
            out.append((c, bool(r)))
        return out

    async def missing(self, bot: Bot, user_id: int) -> list[dict]:
        if not self.app.settings.get("subscription_enabled", True):
            return []
        return [c for c, ok in await self.statuses(bot, user_id) if not ok]


class VipManager:
    def __init__(self, app: "App"):
        self.app = app

    @property
    def grants(self) -> dict[str, dict]:
        return self.app.db.data["vip"]["grants"]

    @staticmethod
    def compute_end(start: datetime, plan_key: str) -> datetime:
        plan = PLANS[plan_key]
        if "months" in plan:
            return add_months(start, plan["months"])
        return start + timedelta(days=plan["days"])

    def grant(self, uid: int, plan_key: str, source: str, granted_by: Optional[int], payment_id: Optional[str] = None):
        """
        QOIDA: foydalanuvchida faol VIP bo'lsa, yangi muddat ESKI TUGASH SANASI USTIGA qo'shiladi;
        aks holda hozirgi vaqtdan boshlanadi. Barcha vaqtlar UTC.
        """
        u = self.app.users.get(uid)
        if u is None:
            raise KeyError("foydalanuvchi topilmadi")
        now = utcnow()
        current = self.app.users.vip_until(u)
        base = current if current else now
        end = self.compute_end(base, plan_key)
        no = self.app.settings["next_grant_no"]
        self.app.settings["next_grant_no"] += 1
        gid = f"G{no:05d}"
        self.grants[gid] = {
            "grant_id": gid, "user_id": uid, "plan": plan_key, "source": source,
            "granted_by": granted_by, "payment_id": payment_id,
            "granted_at": iso(now), "start_at": iso(base), "end_at": iso(end), "status": "active",
        }
        u["vip_until"] = iso(end)
        return base, end

    def cancel(self, uid: int, by: int) -> bool:
        u = self.app.users.get(uid)
        if not u or not self.app.users.vip_until(u):
            return False
        u["vip_until"] = None
        for g in self.grants.values():
            if g["user_id"] == uid and g["status"] == "active":
                g["status"] = "cancelled"
                g["cancelled_by"] = by
                g["cancelled_at"] = iso(utcnow())
        return True

    def sweep(self) -> list[int]:
        """Muddati tugagan VIPlarni oddiy foydalanuvchiga aylantiradi. Qaytaradi: tugaganlar IDsi."""
        now = utcnow()
        expired = []
        for u in self.app.users.items.values():
            dt = parse_iso(u.get("vip_until"))
            if dt and dt <= now:
                u["vip_until"] = None
                expired.append(u["user_id"])
        if expired:
            ex = set(expired)
            for g in self.grants.values():
                if g["user_id"] in ex and g["status"] == "active":
                    g["status"] = "expired"
        return expired

    def active_users(self) -> list[dict]:
        out = [u for u in self.app.users.items.values() if self.app.users.vip_until(u)]
        return sorted(out, key=lambda u: u["vip_until"])


class PaymentManager:
    def __init__(self, app: "App"):
        self.app = app

    @property
    def items(self) -> dict[str, dict]:
        return self.app.db.data["payments"]

    def create(self, uid: int, plan_key: str) -> dict:
        s = self.app.settings
        no = s["next_payment_no"]
        s["next_payment_no"] += 1
        now = utcnow()
        p = {
            "payment_id": f"P{no:05d}", "user_id": uid, "plan": plan_key,
            "amount": PLANS[plan_key]["price"], "receipt_file_id": None, "status": "awaiting_receipt",
            "created_at": iso(now), "expires_at": iso(now + timedelta(minutes=PAYMENT_TIMEOUT_MIN)),
            "reviewed_by": None, "admin_comment": None,
        }
        self.items[p["payment_id"]] = p
        return p

    def by_status(self, uid: int, status: str) -> Optional[dict]:
        return next((p for p in self.items.values() if p["user_id"] == uid and p["status"] == status), None)

    def awaiting(self, uid: int) -> Optional[dict]:
        return self.by_status(uid, "awaiting_receipt")

    def pending(self, uid: int) -> Optional[dict]:
        return self.by_status(uid, "pending")

    @staticmethod
    def is_expired(p: dict) -> bool:
        dt = parse_iso(p["expires_at"])
        return bool(dt and dt <= utcnow())

    def mark_expired(self, p: dict) -> None:
        if p["status"] == "awaiting_receipt":
            p["status"] = "expired"
            p["expired_notified"] = False

    def expire_due(self) -> int:
        n = 0
        for p in self.items.values():
            if p["status"] == "awaiting_receipt" and self.is_expired(p):
                self.mark_expired(p)
                n += 1
        return n

    def unnotified_expired(self) -> list[dict]:
        return [p for p in self.items.values() if p["status"] == "expired" and p.get("expired_notified") is False]

    def submit_receipt(self, p: dict, file_id: str) -> None:
        p["receipt_file_id"] = file_id
        p["status"] = "pending"
        p["receipt_sent_at"] = iso(utcnow())

    def pending_list(self) -> list[dict]:
        return sorted((p for p in self.items.values() if p["status"] == "pending"), key=lambda p: p["created_at"])

    def approve(self, pid: str, admin_id: int, comment: str) -> Optional[dict]:
        p = self.items.get(pid)
        if not p or p["status"] != "pending":  # takroriy tasdiqlashdan himoya
            return None
        start, end = self.app.vip.grant(p["user_id"], p["plan"], "payment", admin_id, pid)
        p.update(
            status="approved", reviewed_by=admin_id, reviewed_at=iso(utcnow()),
            admin_comment=comment, vip_start=iso(start), vip_end=iso(end),
        )
        return p

    def reject(self, pid: str, admin_id: int, reason: str) -> Optional[dict]:
        p = self.items.get(pid)
        if not p or p["status"] != "pending":
            return None
        p.update(status="rejected", reviewed_by=admin_id, reviewed_at=iso(utcnow()), admin_comment=reason)
        return p


class StatisticsManager:
    def __init__(self, app: "App"):
        self.app = app

    @property
    def s(self) -> dict:
        return self.app.db.data["statistics"]

    def record_usage(self) -> None:
        now = utcnow()
        for bucket, key in (
            ("usage_by_day", now.strftime("%Y-%m-%d")),
            ("usage_by_hour", f"{now.hour:02d}"),
            ("usage_by_weekday", str(now.weekday())),
        ):
            self.s[bucket][key] = self.s[bucket].get(key, 0) + 1
        self.app.db.touch("statistics")

    def log_admin(self, admin_id: int, action: str, details: str = "") -> None:
        logger.info("ADMIN %s | %s | %s", admin_id, action, details)
        counts = self.s["admin_action_counts"].setdefault(str(admin_id), {})
        counts[action] = counts.get(action, 0) + 1
        log = self.s["admin_log"]
        log.append({"ts": iso(utcnow()), "admin_id": admin_id, "action": action, "details": details[:200]})
        if len(log) > 2000:
            del log[: len(log) - 2000]
        self.app.db.touch("statistics")

    # ---- hisobotlar (matn) ----
    def users_text(self) -> str:
        users = list(self.app.users.items.values())
        now = utcnow()
        today = now.date()

        def joined(u):
            return parse_iso(u["joined_at"])

        c_today = sum(1 for u in users if joined(u) and joined(u).date() == today)
        c_yest = sum(1 for u in users if joined(u) and joined(u).date() == today - timedelta(days=1))
        c7 = sum(1 for u in users if joined(u) and joined(u) >= now - timedelta(days=7))
        c30 = sum(1 for u in users if joined(u) and joined(u) >= now - timedelta(days=30))
        active = sum(1 for u in users if (parse_iso(u["last_active"]) or now - timedelta(days=999)) >= now - timedelta(days=7))
        vip = sum(1 for u in users if self.app.users.vip_until(u))
        blocked = sum(1 for u in users if u.get("is_blocked"))
        return (
            "👥 <b>Foydalanuvchilar statistikasi</b>\n\n"
            f"Umumiy: <b>{len(users)}</b>\n"
            f"Bugun qo‘shilgan: <b>{c_today}</b>\n"
            f"Kecha qo‘shilgan: <b>{c_yest}</b>\n"
            f"Oxirgi 7 kunda: <b>{c7}</b>\n"
            f"Oxirgi 30 kunda: <b>{c30}</b>\n"
            f"Faol (oxirgi 7 kunda murojaat qilgan): <b>{active}</b>\n"
            f"💎 VIP: <b>{vip}</b>\n"
            f"Oddiy: <b>{len(users) - vip}</b>\n"
            f"Botni bloklagan: <b>{blocked}</b>\n\n"
            "<i>Bloklaganlar soni faqat bot aniqlagan holatlar bo‘yicha (xabar yuborib ko‘rilganda yoki Telegram bildirganda).</i>"
        )

    def movies_text(self) -> str:
        ms = list(self.app.movies.items.values())
        now = utcnow()

        def added(m):
            return parse_iso(m["added_at"])

        c_today = sum(1 for m in ms if added(m) and added(m).date() == now.date())
        c7 = sum(1 for m in ms if added(m) and added(m) >= now - timedelta(days=7))
        c30 = sum(1 for m in ms if added(m) and added(m) >= now - timedelta(days=30))
        top = sorted(ms, key=lambda m: m.get("views_count", 0), reverse=True)[:10]
        top_lines = "\n".join(
            f"{i}. {esc(trunc(m['title'], 40))} (<code>{m['code']}</code>) — <b>{m.get('views_count', 0)}</b>"
            for i, m in enumerate(top, 1)
        ) or "Hozircha kino yo‘q."
        return (
            "🎬 <b>Kino statistikasi</b>\n\n"
            f"Umumiy kinolar: <b>{len(ms)}</b>\n"
            f"Bugun qo‘shilgan: <b>{c_today}</b>\n"
            f"Oxirgi 7 kunda: <b>{c7}</b>\n"
            f"Oxirgi 30 kunda: <b>{c30}</b>\n\n"
            f"🔥 <b>Eng ko‘p ko‘rilganlar:</b>\n{top_lines}"
        )

    def movies_views_page(self, page: int) -> tuple[str, int]:
        ms = sorted(self.app.movies.items.values(), key=lambda m: m.get("views_count", 0), reverse=True)
        pages = max(1, (len(ms) + 19) // 20)
        page = max(0, min(page, pages - 1))
        chunk = ms[page * 20: page * 20 + 20]
        lines = "\n".join(
            f"{page * 20 + i}. {esc(trunc(m['title'], 40))} (<code>{m['code']}</code>) — <b>{m.get('views_count', 0)}</b>"
            for i, m in enumerate(chunk, 1)
        ) or "Hozircha kino yo‘q."
        return f"👁 <b>Har bir kinoning ko‘rishlar soni</b> ({page + 1}/{pages})\n\n{lines}", pages

    def activity_text(self) -> str:
        s = self.s
        now = utcnow()
        by_day = s["usage_by_day"]
        if not by_day:
            return "📈 <b>Faollik statistikasi</b>\n\nMa‘lumot hali to‘planmagan."

        def sum_days(n):
            return sum(by_day.get((now - timedelta(days=i)).strftime("%Y-%m-%d"), 0) for i in range(n))

        hours = s["usage_by_hour"]
        hour_lines = "\n".join(f"{h}:00 — {hours[h]}" for h in sorted(hours) if hours[h])
        wd = s["usage_by_weekday"]
        wd_lines = "\n".join(f"{WEEKDAYS_UZ[int(k)]} — {v}" for k, v in sorted(wd.items(), key=lambda kv: -kv[1]))
        best_h = sorted(hours.items(), key=lambda kv: -kv[1])[:3]
        best_d = sorted(by_day.items(), key=lambda kv: -kv[1])[:3]
        return (
            "📈 <b>Faollik statistikasi</b> (UTC)\n\n"
            f"Bugungi foydalanishlar: <b>{sum_days(1)}</b>\n"
            f"Haftalik (7 kun): <b>{sum_days(7)}</b>\n"
            f"Oylik (30 kun): <b>{sum_days(30)}</b>\n\n"
            "⏰ <b>Eng faol vaqtlar:</b> " + ", ".join(f"{h}:00 ({n})" for h, n in best_h) + "\n"
            "📅 <b>Eng faol kunlar:</b> " + ", ".join(f"{d} ({n})" for d, n in best_d) + "\n\n"
            f"🗓 <b>Hafta kunlari bo‘yicha:</b>\n{wd_lines}\n\n"
            f"🕐 <b>Soat bo‘yicha foydalanishlar:</b>\n{hour_lines}\n\n"
            "<i>Hisoblash statistika yoqilgan paytdan boshlanadi.</i>"
        )

    def vip_text(self) -> str:
        ps = list(self.app.payments.items.values())
        approved = [p for p in ps if p["status"] == "approved"]
        rejected = sum(1 for p in ps if p["status"] == "rejected")
        pending = sum(1 for p in ps if p["status"] == "pending")
        awaiting = sum(1 for p in ps if p["status"] == "awaiting_receipt")
        revenue = sum(p["amount"] for p in approved)
        by_plan = "\n".join(
            f"• {PLANS[k]['label']}: <b>{sum(1 for p in approved if p['plan'] == k)}</b> ta"
            for k in PLANS
        )
        manual = sum(1 for g in self.app.vip.grants.values() if g["source"] in ("manual", "extend"))
        return (
            "💎 <b>VIP statistikasi</b>\n\n"
            f"Umumiy VIP sotuvlari: <b>{len(approved)}</b>\n"
            f"✅ Tasdiqlangan to‘lovlar: <b>{len(approved)}</b>\n"
            f"❌ Rad etilgan: <b>{rejected}</b>\n"
            f"⏳ Kutilayotgan (admin tekshiruvida): <b>{pending}</b>\n"
            f"📸 Chek kutilayotgan: <b>{awaiting}</b>\n\n"
            f"📦 <b>Tariflar bo‘yicha sotuvlar:</b>\n{by_plan}\n\n"
            f"💰 Jami tushum: <b>{fmt_money(revenue)}</b>\n"
            f"🎁 Admin tomonidan qo‘lda berilgan/uzaytirilgan: <b>{manual}</b> marta"
        )

    def admins_text(self) -> str:
        movies = self.app.movies.items.values()
        lines = []
        for a in self.app.admins.listing():
            uid = a["user_id"]
            added = sum(1 for m in movies if m["added_by"] == uid)
            counts = self.s["admin_action_counts"].get(str(uid), {})
            total = sum(counts.values())
            acts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:6]) or "—"
            lines.append(
                f"• <b>{esc(a['first_name'])}</b> (<code>{uid}</code>) — {ROLE_LABELS[a['role']]}\n"
                f"   🎬 Qo‘shgan kinolar: <b>{added}</b>\n   ⚙️ Amallar: <b>{total}</b> ({esc(acts)})"
            )
        return "👮 <b>Admin statistikasi</b>\n\n" + "\n\n".join(lines)


class BackupManager:
    def __init__(self, app: "App"):
        self.app = app

    def build_zip(self, snap: Optional[dict[str, str]] = None) -> bytes:
        snap = snap if snap is not None else self.app.db.snapshot()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, text in snap.items():
                zf.writestr(fname, text)
            zf.writestr("backup_info.txt", f"Kino bot backup\nUTC: {iso(utcnow())}\n")
        return buf.getvalue()

    def filename(self) -> str:
        return f"kinobot_backup_{utcnow().strftime('%Y%m%d_%H%M%S')}.zip"

    def parse_zip(self, raw: bytes) -> dict[str, str]:
        """ZIP ichidagi JSON fayllarni tekshiradi. Xato bo'lsa ValueError."""
        if len(raw) > 20 * 1024 * 1024:
            raise ValueError("Fayl juda katta.")
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as e:
            raise ValueError("Bu ZIP arxiv emas.") from e
        allowed = {f"{n}.json": n for n in JsonDB.FILES}
        texts: dict[str, str] = {}
        total = 0
        for info in zf.infolist():
            base = Path(info.filename).name
            if base not in allowed:
                continue
            total += info.file_size
            if total > 200 * 1024 * 1024:
                raise ValueError("Arxiv ichidagi ma'lumot haddan tashqari katta.")
            text = zf.read(info).decode("utf-8")
            obj = json.loads(text)
            if not isinstance(obj, type(DEFAULT_DATA[allowed[base]])):
                raise ValueError(f"{base} tuzilmasi noto‘g‘ri.")
            texts[base] = text
        required = {"users.json", "movies.json", "settings.json", "admins.json"}
        if not required <= set(texts):
            raise ValueError("Arxivda users/movies/admins/settings JSON fayllari yo‘q.")
        return texts

    def restore(self, texts: dict[str, str]) -> None:
        old = {k: v for k, v in self.app.settings.items() if k.startswith("next_") and isinstance(v, int)}
        self.app.db.replace_all(texts)
        for k, v in old.items():  # hisoblagichlar orqaga qaytmasin: o'chirilgan kod/ID lar qayta berilmaydi
            if self.app.settings.get(k, 0) < v:
                self.app.settings[k] = v
        self.app.settings.setdefault("next_post_no", 1)
        self.app.admins.ensure_main()
        self.app.movies.sync_code_counter()


class BroadcastManager:
    def __init__(self, app: "App"):
        self.app = app

    @property
    def state(self) -> dict:
        return self.app.db.data["broadcast"]

    @property
    def current(self) -> Optional[dict]:
        return self.state.get("current")

    def is_running(self) -> bool:
        cur = self.current
        return bool(cur and cur["status"] == "running")

    def create_job(self, admin_id: int, ctype: str, text_html: Optional[str], file_id: Optional[str]) -> dict:
        s = self.app.settings
        no = s["next_broadcast_no"]
        s["next_broadcast_no"] += 1
        recipients = [u["user_id"] for u in self.app.users.items.values()]
        job = {
            "job_id": f"B{no:04d}", "created_by": admin_id, "content_type": ctype,
            "text_html": text_html, "file_id": file_id, "status": "running",
            "total": len(recipients), "sent": 0, "blocked": 0, "other_errors": 0,
            "recipients": recipients, "index": 0, "started_at": iso(utcnow()), "finished_at": None,
        }
        self.state["current"] = job
        return job

    @staticmethod
    async def _deliver(bot: Bot, job: dict, uid: int) -> None:
        t = job["content_type"]
        if t == "text":
            await bot.send_message(uid, job["text_html"])
        elif t == "photo":
            await bot.send_photo(uid, job["file_id"], caption=job["text_html"])
        elif t == "video":
            await bot.send_video(uid, job["file_id"], caption=job["text_html"])
        else:
            raise ValueError("noma'lum tur")

    @staticmethod
    def report_text(job: dict) -> str:
        failed = job["blocked"] + job["other_errors"]
        title = {"done": "✅ Broadcast yakunlandi", "cancelled": "⛔ Broadcast to‘xtatildi"}.get(
            job["status"], "📣 Broadcast")
        return (
            f"{title}\n\n"
            f"👥 Jami foydalanuvchilar: <b>{job['total']}</b>\n"
            f"✅ Muvaffaqiyatli yuborilganlar: <b>{job['sent']}</b>\n"
            f"❌ Yuborilmaganlar: <b>{failed}</b>\n"
            f"🚫 Botni bloklaganlar: <b>{job['blocked']}</b>\n"
            f"⚠️ Boshqa xatoliklar: <b>{job['other_errors']}</b>"
        )

    async def run(self, bot: Bot, delay: float = BROADCAST_DELAY, progress_cb=None) -> None:
        job = self.current
        if not job:
            return
        last_progress = time.monotonic()
        net_fail = 0
        while job["status"] == "running" and job["index"] < len(job["recipients"]):
            uid = job["recipients"][job["index"]]
            try:
                await self._deliver(bot, job, uid)
                job["sent"] += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                continue
            except TelegramForbiddenError as e:
                if "blocked" in str(e).lower():
                    job["blocked"] += 1
                    self.app.users.set_blocked(uid, True)
                else:
                    job["other_errors"] += 1
            except (TelegramNetworkError, TelegramServerError):
                net_fail += 1
                if net_fail < 3:
                    await asyncio.sleep(2)
                    continue
                job["other_errors"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("Broadcast xato (uid=%s): %r", uid, e)
                job["other_errors"] += 1
            net_fail = 0
            job["index"] += 1
            self.app.db.touch("broadcast")
            if job["index"] % 100 == 0:
                await self.app.db.save("broadcast")  # qayta ishga tushganda takror yuborish minimal bo'lsin
            await asyncio.sleep(delay)
            if progress_cb and time.monotonic() - last_progress > 5:
                last_progress = time.monotonic()
                with contextlib.suppress(Exception):
                    await progress_cb(job)
        if job["status"] == "running":
            job["status"] = "done"
        job["finished_at"] = iso(utcnow())
        job["recipients"] = []  # joy tejash
        summary = {k: v for k, v in job.items() if k not in ("recipients", "text_html", "file_id")}
        self.state["history"].append(summary)
        del self.state["history"][:-20]
        await self.app.db.save_many("broadcast", "users")


class PostManager:
    """Kanalga post joylash: qoralamalar va yuborish holati (channel_posts.json).
    Holatlar: draft → sending → sent | failed | uncertain; draft/failed → cancelled."""

    DRAFT_TTL = 3600          # preview 1 soat amal qiladi
    KEEP = 200                # JSON da saqlanadigan postlar soni

    def __init__(self, app: "App"):
        self.app = app

    @property
    def items(self) -> dict[str, dict]:
        return self.app.db.data["channel_posts"]

    def get(self, pid: str) -> Optional[dict]:
        return self.items.get(pid)

    def recover_after_restart(self) -> None:
        """Bot yuborish paytida to'xtagan bo'lsa, post yuborilgan-yuborilmaganligi noma'lum: qayta yuborilmaydi."""
        for p in self.items.values():
            if p["status"] == "sending":
                p["status"] = "uncertain"
                p["error"] = "Bot qayta ishga tushdi: post yuborilgan-yuborilmaganligi noma'lum."
                self.app.db.touch("channel_posts")

    def create(self, admin_id: int, channel: dict, media_type: str, file_id: str, text_html: str,
               button: dict, preview_mid: Optional[int]) -> dict:
        s = self.app.settings
        no = s["next_post_no"]
        s["next_post_no"] += 1
        p = {
            "post_id": f"PO{no:05d}", "admin_id": admin_id,
            "channel_id": channel["id"], "channel_title": channel["title"],
            "channel_username": channel.get("username"), "channel_type": channel.get("type"),
            "media_type": media_type, "file_id": file_id, "text_html": text_html, "button": button,
            "status": "draft", "attempts": 0, "created_at": iso(utcnow()), "sent_at": None,
            "message_id": None, "error": None, "preview_message_id": preview_mid,
        }
        self.items[p["post_id"]] = p
        self.prune()
        return p

    def is_expired_draft(self, p: dict) -> bool:
        dt = parse_iso(p["created_at"])
        return p["status"] in ("draft", "failed") and bool(dt) and (utcnow() - dt).total_seconds() > self.DRAFT_TTL

    def prune(self) -> None:
        for pid in [k for k, p in self.items.items() if p["status"] in ("draft", "cancelled", "failed")
                    and (parse_iso(p["created_at"]) or utcnow()) < utcnow() - timedelta(days=2)]:
            del self.items[pid]
        extra = len(self.items) - self.KEEP
        if extra > 0:
            for pid in sorted((k for k, p in self.items.items() if p["status"] != "sending"),
                              key=lambda k: self.items[k]["created_at"])[:extra]:
                del self.items[pid]


# =====================================================================
# 5. ILOVA KONTEYNERI
# =====================================================================
class App:
    def __init__(self, data_dir: str | Path, main_admin_id: int):
        self.main_admin_id = int(main_admin_id)
        self.db = JsonDB(data_dir)
        self.users = UserManager(self)
        self.movies = MovieManager(self)
        self.admins = AdminManager(self)
        self.subs = SubscriptionManager(self)
        self.vip = VipManager(self)
        self.payments = PaymentManager(self)
        self.stats = StatisticsManager(self)
        self.backup = BackupManager(self)
        self.broadcast = BroadcastManager(self)
        self.posts = PostManager(self)
        self.admins.ensure_main()
        self.posts.recover_after_restart()
        self.movies.sync_code_counter()
        self.bot_username: Optional[str] = None
        self.user_locks: dict[int, asyncio.Lock] = {}
        self.broadcast_task: Optional[asyncio.Task] = None
        self.restore_buffer: dict[int, dict[str, str]] = {}

    @property
    def settings(self) -> dict:
        return self.db.data["settings"]

    def lock_for(self, uid: int) -> asyncio.Lock:
        return self.user_locks.setdefault(uid, asyncio.Lock())

    async def save(self, *names: str) -> None:
        await self.db.save_many(*names)


APP: App = None  # type: ignore[assignment]

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
MOVIE_START_RE = re.compile(r"^movie_(\d{1,12})$")


async def ensure_bot_username(bot: Bot) -> Optional[str]:
    """BOT_USERNAME (.env) yoki Telegram API (getMe) orqali bot usernamesini aniqlaydi."""
    if APP.bot_username:
        return APP.bot_username
    env = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    if USERNAME_RE.match(env):
        APP.bot_username = env
        return env
    try:
        me = await tg_retry(lambda: bot.get_me())
    except TelegramAPIError as e:
        logger.error("Bot usernamesini aniqlab bo‘lmadi: %s", e)
        return None
    APP.bot_username = me.username
    return APP.bot_username


async def movie_link(bot: Bot, code: str) -> Optional[str]:
    """Kino uchun deep link: https://t.me/BOT_USERNAME?start=movie_KOD"""
    username = await ensure_bot_username(bot)
    return f"https://t.me/{username}?start=movie_{code}" if username else None


# =====================================================================
# 6. FSM HOLATLARI, KLAVIATURALAR, YORDAMCHILAR
# =====================================================================
class MovieAdd(StatesGroup):
    video = State()
    description = State()
    title = State()


class MovieDel(StatesGroup):
    code = State()


class MovieFind(StatesGroup):
    query = State()


class ChannelAdd(StatesGroup):
    ref = State()
    link = State()


class ChannelLink(StatesGroup):
    link = State()


class AdminAdd(StatesGroup):
    uid = State()


class VipUser(StatesGroup):
    uid = State()


class SettingEdit(StatesGroup):
    value = State()


class BroadcastSt(StatesGroup):
    content = State()
    confirm = State()


class PayReview(StatesGroup):
    comment = State()
    reason = State()


class RestoreSt(StatesGroup):
    zipfile = State()


class PostSt(StatesGroup):
    channel = State()
    media = State()
    text = State()
    btn_kind = State()
    url_title = State()
    url_value = State()
    movie_code = State()
    movie_title = State()
    confirm = State()


def ikb(rows: list[list[tuple]]) -> InlineKeyboardMarkup:
    """rows: [[(text, callback_data)], [(text, 'url', url)], [(text, 'copy', matn)] ...]"""
    out = []
    for row in rows:
        btns = []
        for b in row:
            if len(b) == 3 and b[1] == "url":
                btns.append(InlineKeyboardButton(text=b[0], url=b[2]))
            elif len(b) == 3 and b[1] == "copy":
                btns.append(InlineKeyboardButton(text=b[0], copy_text=CopyTextButton(text=b[2][:256])))
            else:
                btns.append(InlineKeyboardButton(text=b[0], callback_data=b[1]))
        out.append(btns)
    return InlineKeyboardMarkup(inline_keyboard=out)


CANCEL_ROW = [("❌ Bekor qilish", "adm:cancel")]


def main_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_VIP)],
        [KeyboardButton(text=BTN_PROFILE)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def show(target: Message | CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    """Imkon bo'lsa mavjud xabarni tahrirlaydi, bo'lmasa yangi xabar yuboradi."""
    if isinstance(target, CallbackQuery):
        msg = target.message
        if isinstance(msg, Message):
            try:
                await msg.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e).lower():
                    return
            await msg.answer(text, reply_markup=markup)
        else:
            await target.bot.send_message(target.from_user.id, text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def need(event: Message | CallbackQuery, perm: str) -> Optional[str]:
    """Server tomonida huquqni tekshiradi (har bir handler va callback ichida)."""
    role = APP.admins.role_of(event.from_user.id)
    if role and has_perm(role, perm):
        return role
    if isinstance(event, CallbackQuery):
        await event.answer("⛔ Sizda bu amal uchun ruxsat yo‘q.", show_alert=True)
    else:
        await event.answer("⛔ Sizda bu amal uchun ruxsat yo‘q.")
    return None


async def notify_user(bot: Bot, uid: int, text: str, markup=None) -> bool:
    try:
        await tg_retry(lambda: bot.send_message(uid, text, reply_markup=markup))
        return True
    except TelegramForbiddenError:
        APP.users.set_blocked(uid, True)
    except TelegramAPIError as e:
        logger.warning("Foydalanuvchiga (%s) xabar yuborib bo‘lmadi: %s", uid, e)
    return False


# (tugma matni, kerakli ruxsat) — tartib menyudagi tartib. Ruxsatlar ROLE_PERMS dan olinadi.
ADMIN_MENU: list[tuple[str, str]] = [
    (ADM_BTN_MOVIES, "movie_menu"),
    (ADM_BTN_CHANNELS, "channels"),
    (ADM_BTN_STATS, "stats"),
    (ADM_BTN_ADMINS, "admins"),
    (ADM_BTN_VIP, "vip"),
    (ADM_BTN_POST, "channel_post"),
    (ADM_BTN_BACKUP, "backup"),
    (ADM_BTN_SETTINGS, "settings"),
    (ADM_BTN_BROADCAST, "broadcast"),
]


def admin_kb(role: str) -> ReplyKeyboardMarkup:
    """Admin panelning asosiy menyusi: faqat rolga ruxsat etilgan bo'limlar ko'rsatiladi."""
    btns = [KeyboardButton(text=t) for t, perm in ADMIN_MENU if has_perm(role, perm)]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([KeyboardButton(text=ADM_BTN_BACK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def ack(target: Message | CallbackQuery, text: Optional[str] = None, **kw) -> None:
    """Callback bo'lsa javob beradi (Message uchun hech narsa qilmaydi)."""
    if isinstance(target, CallbackQuery):
        await target.answer(text, **kw)


def panel_text(role: str) -> str:
    return f"🛠 <b>Admin panel</b>\n\nRolingiz: {ROLE_LABELS[role]}\n\nBo‘limni pastdagi menyudan tanlang."


def paginate(items: list, page: int, per: int = PAGE_SIZE) -> tuple[list, int, int]:
    pages = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    return items[page * per: (page + 1) * per], page, pages


def bot_name() -> str:
    return APP.settings.get("bot_name") or "Kino"


def start_text() -> str:
    return (
        "👋 Assalomu alaykum!\n\n"
        f"Men {esc(bot_name())} kino botiman.\n\n"
        "🔎 Kino nomini yoki kod yozing — men uni topib beraman.\n\n"
        f"🎬 Savol va takliflar uchun {esc(APP.settings.get('contact_username') or '@senzaifsan')} admini bilan bog‘laning.\n\n"
        "Pastdagi tugmalar orqali bo‘limlardan foydalanishingiz mumkin."
    )


# =====================================================================
# 7. MIDDLEWARE
# =====================================================================
class ActivityMiddleware(BaseMiddleware):
    """Foydalanuvchini ro'yxatga oladi (faqat bir marta), faolligini va statistikani yangilaydi."""

    async def __call__(self, handler, event, data):
        tg_user = data.get("event_from_user")
        chat = data.get("event_chat")
        data["is_new_user"] = False
        if tg_user and not tg_user.is_bot and (chat is None or chat.type == ChatType.PRIVATE):
            try:
                _, is_new = APP.users.upsert(tg_user)
                data["is_new_user"] = is_new
                APP.stats.record_usage()
                if is_new:
                    await APP.save("users")
                else:
                    APP.db.touch("users")
            except Exception:  # noqa: BLE001
                logger.exception("Middleware xatosi")
        return await handler(event, data)


# =====================================================================
# 8. ROUTERLAR
# =====================================================================
common = Router(name="common")
admin_r = Router(name="admin")
user_r = Router(name="user")
search_r = Router(name="search")
for _r in (common, admin_r, user_r, search_r):
    _r.message.filter(F.chat.type == ChatType.PRIVATE)


# =====================================================================
# 9. FOYDALANUVCHI: /start, MENYU
# =====================================================================
@common.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot, is_new_user: bool = False):
    await state.clear()
    is_admin = APP.admins.role_of(message.from_user.id) is not None
    payload = (command.args or "").strip()
    if not payload.startswith("movie_"):
        return await message.answer(start_text(), reply_markup=main_kb(is_admin))
    # Kino havolasi: /start movie_11
    if is_new_user:  # birinchi marta kirgan foydalanuvchiga menyu klaviaturasi ham beriladi
        await message.answer(start_text(), reply_markup=main_kb(is_admin))
    parsed = MOVIE_START_RE.match(payload)
    movie = APP.movies.by_code(parsed.group(1)) if parsed else None
    if not movie:
        return await message.answer(
            "❌ Bu havola bo‘yicha kino topilmadi.\n\n"
            "Havola noto‘g‘ri, eskirgan yoki kino o‘chirilgan bo‘lishi mumkin. Kino nomini yoki kodini yozib qidirib ko‘ring.")
    # Majburiy obuna / VIP qoidalari oddiy kino olish bilan bir xil (deliver_movie)
    await deliver_movie(bot, message, message.from_user.id, str(movie["movie_id"]), edit_window=False)


@common.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    is_admin = APP.admins.role_of(message.from_user.id) is not None
    await message.answer("❌ Bekor qilindi.", reply_markup=main_kb(is_admin))


@common.message(F.text == BTN_SEARCH)
async def menu_search(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔎 Kino nomini yoki kodini yozing.")


@common.message(F.text == BTN_PROFILE)
async def menu_profile(message: Message, state: FSMContext):
    await state.clear()
    u = APP.users.get(message.from_user.id)
    vip_dt = APP.users.vip_until(u)
    uname = f"@{esc(u['username'])}" if u.get("username") else "—"
    badge = "💎 <b>VIP</b>" if vip_dt else "Oddiy foydalanuvchi"
    text = (
        f"👤 <b>Profilim</b> {'💎' if vip_dt else ''}\n\n"
        f"🆔 Telegram ID: <code>{u['user_id']}</code>\n"
        f"📛 Ism: {esc(u['first_name'])}\n"
        f"🔗 Username: {uname}\n"
        f"📅 Ro‘yxatdan o‘tgan sana: {fmt_dt(parse_iso(u['joined_at']))}\n"
        f"💎 VIP holati: {badge}\n"
        f"⏳ VIP tugash sanasi: {fmt_dt(vip_dt)}\n"
        f"🎬 Ko‘rgan kinolari soni: <b>{len(u['watched_movies'])}</b>"
    )
    await message.answer(text)


# ---- VIP ----
def vip_info_text(uid: int) -> str:
    vip_dt = APP.users.vip_until(APP.users.get(uid))
    plans = "\n".join(f"{p['label']} — {fmt_money(p['price'])}" for p in PLANS.values())
    status = f"\n\n✅ Sizda VIP faol. Tugash sanasi: <b>{fmt_dt(vip_dt)}</b>" if vip_dt else ""
    return (
        "💎 <b>VIP OBUNA</b>\n\n"
        "VIP imkoniyatlari:\n"
        "• Majburiy obunasiz kino olish.\n"
        "• Barcha kinolardan foydalanish.\n"
        "• VIP muddati davomida obuna tekshiruvlarisiz foydalanish.\n\n"
        f"<b>Tariflar:</b>\n{plans}{status}"
    )


def vip_plans_kb() -> InlineKeyboardMarkup:
    return ikb([[(f"{p['label']} — {fmt_money(p['price'])}", f"vip_plan:{k}")] for k, p in PLANS.items()])


@common.message(F.text == BTN_VIP)
async def menu_vip(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(vip_info_text(message.from_user.id), reply_markup=vip_plans_kb())


@user_r.callback_query(F.data == "vip_home")
async def cb_vip_home(cb: CallbackQuery):
    await show(cb, vip_info_text(cb.from_user.id), vip_plans_kb())
    await cb.answer()


@user_r.callback_query(F.data.startswith("vip_plan:"))
async def cb_vip_plan(cb: CallbackQuery):
    uid = cb.from_user.id
    key = cb.data.split(":", 1)[1]
    if key not in PLANS:
        return await cb.answer("Noto‘g‘ri tarif.", show_alert=True)
    s = APP.settings
    if not s.get("card_number") or not s.get("card_holder"):
        return await cb.answer("Hozircha to‘lov ma'lumotlari kiritilmagan. Admin bilan bog‘laning.", show_alert=True)
    if APP.payments.pending(uid):
        return await cb.answer("⏳ Oldingi to‘lov so‘rovingiz admin tomonidan ko‘rib chiqilmoqda.", show_alert=True)
    old = APP.payments.awaiting(uid)
    if old:  # qayta tarif tanlash: eski kutayotgan so'rov bekor qilinadi
        old["status"] = "cancelled"
    p = APP.payments.create(uid, key)
    await APP.save("payments", "settings")
    plan = PLANS[key]
    deadline = parse_iso(p["expires_at"])
    note = f"\n📝 Izoh: {esc(s['payment_note'])}" if s.get("payment_note") else ""
    text = (
        f"💎 <b>Tanlangan tarif:</b> {plan['label']}\n"
        f"💰 <b>Summa:</b> {fmt_money(plan['price'])}\n\n"
        "💳 <b>To‘lov ma'lumotlari</b>\n"
        f"👤 Karta egasi: <b>{esc(s['card_holder'])}</b>\n"
        f"💳 Karta raqami: <code>{esc(s['card_number'])}</code>{note}\n\n"
        "📸 To‘lov chekini rasm shaklida yuboring.\n\n"
        f"⏳ Chekni yuborish uchun {PAYMENT_TIMEOUT_MIN} daqiqa vaqtingiz bor. ({deadline.strftime('%H:%M')} UTC gacha)\n\n"
        "⚠️ Faqat haqiqiy to‘lov chekini yuboring."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Karta raqamini nusxalash", copy_text=CopyTextButton(text=s["card_number"].replace(" ", "")))],
        [InlineKeyboardButton(text="📋 Karta egasi ismini nusxalash", copy_text=CopyTextButton(text=s["card_holder"][:256]))],
        [InlineKeyboardButton(text="🔄 Boshqa tarif", callback_data=f"vip_cancel:{p['payment_id']}"),
         InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"vip_cancel:{p['payment_id']}")],
    ])
    await show(cb, text, kb)
    await cb.answer()


@user_r.callback_query(F.data.startswith("vip_cancel:"))
async def cb_vip_cancel(cb: CallbackQuery):
    pid = cb.data.split(":", 1)[1]
    p = APP.payments.items.get(pid)
    if p and p["user_id"] == cb.from_user.id and p["status"] == "awaiting_receipt":
        p["status"] = "cancelled"
        await APP.save("payments")
    await show(cb, vip_info_text(cb.from_user.id), vip_plans_kb())
    await cb.answer("To‘lov so‘rovi bekor qilindi.")


class AwaitingReceipt(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return APP.payments.awaiting(message.from_user.id) is not None


async def send_receipt_to_admin(bot: Bot, p: dict) -> None:
    caption = payment_caption(p)
    kb = ikb([[("✅ Tasdiqlash", f"pay_ok:{p['payment_id']}"), ("❌ Rad etish", f"pay_no:{p['payment_id']}")]])
    sent = await tg_retry(lambda: bot.send_photo(APP.main_admin_id, p["receipt_file_id"], caption=caption, reply_markup=kb))
    p["admin_chat_id"], p["admin_message_id"] = sent.chat.id, sent.message_id
    await APP.save("payments")


@user_r.message(AwaitingReceipt(), F.photo)
async def receipt_photo(message: Message, bot: Bot):
    p = APP.payments.awaiting(message.from_user.id)
    if not p:
        return
    if APP.payments.is_expired(p):
        APP.payments.mark_expired(p)
        p["expired_notified"] = True
        await APP.save("payments")
        return await message.answer(
            f"⌛ {PAYMENT_TIMEOUT_MIN} daqiqalik muddat tugadi, chek qabul qilinmaydi.\n"
            "Iltimos, 💎 VIP obuna bo‘limidan qaytadan tarif tanlang."
        )
    APP.payments.submit_receipt(p, message.photo[-1].file_id)
    await APP.save("payments")
    await message.answer("✅ Chekingiz qabul qilindi. Admin tekshirgach, natija haqida xabar beramiz.")
    try:
        await send_receipt_to_admin(bot, p)
    except TelegramAPIError as e:
        logger.error("Chekni adminga yuborib bo‘lmadi (%s): %s", p["payment_id"], e)


@user_r.message(AwaitingReceipt())
async def receipt_wrong(message: Message):
    p = APP.payments.awaiting(message.from_user.id)
    if p and APP.payments.is_expired(p):
        APP.payments.mark_expired(p)
        p["expired_notified"] = True
        await APP.save("payments")
        await message.answer(f"⌛ {PAYMENT_TIMEOUT_MIN} daqiqalik chek yuborish muddati tugadi. 💎 VIP obuna bo‘limidan qayta tarif tanlang.")
        if message.text and not message.text.startswith("/"):
            await do_search(message, message.text)
        return
    if message.text and not message.text.startswith("/") and APP.movies.search(message.text):
        return await do_search(message, message.text)  # kino qidirish chek kutish paytida ham ishlaydi
    await message.answer(
        "❗ Chek faqat <b>rasm</b> shaklida qabul qilinadi.\n\n"
        "Matn, stiker, GIF, video yoki fayl yuborilmasin. To‘lov chekining rasmini yuboring."
    )


# =====================================================================
# 10. OBUNA KANALLARI, QIDIRUV VA KINO YUBORISH
# =====================================================================
def sub_window(missing: list[dict], user_id: int, movie_id: str) -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    for c in missing:
        pending = c["channel_type"] == "private" and APP.subs.has_join_request(c["channel_id"], user_id)
        label = ("⏳ " if pending else "") + c["title"]
        rows.append([(trunc(label, 60), "url", c["invite_link"])])
    rows.append([("🔄 Tekshirish", f"chk:{movie_id}")])
    text = (
        f"🎬 Kinoni olish uchun quyidagi {len(missing)} ta kanal va guruhga a‘zo bo‘ling.\n\n"
        "So‘ng 🔄 Tekshirish tugmasini bosing."
    )
    if any(c["channel_type"] == "private" for c in missing):
        text += "\n\nℹ️ Yopiq kanallarda so‘rov yuborganingizdan keyin kanal admini tasdiqlashi kerak."
    return text, ikb(rows)


async def deliver_movie(bot: Bot, target: Message | CallbackQuery, user_id: int, movie_id: str, edit_window: bool) -> None:
    """Obunani tekshiradi (VIP bo'lmasa) va kinoni yuboradi."""
    async with APP.lock_for(user_id):
        m = APP.movies.get(movie_id)
        if not m:
            msg = "😕 Bu kino topilmadi (o‘chirilgan bo‘lishi mumkin)."
            return await show(target, msg) if isinstance(target, CallbackQuery) else await target.answer(msg)
        if not APP.users.is_vip(user_id):
            try:
                missing = await APP.subs.missing(bot, user_id)
            except SubCheckError:
                msg = "⚠️ Obunani tekshirishda vaqtinchalik xatolik. Birozdan so‘ng qayta urinib ko‘ring."
                if isinstance(target, CallbackQuery):
                    return await target.answer(msg, show_alert=True)
                return await target.answer(msg)
            if missing:
                text, kb = sub_window(missing, user_id, movie_id)
                if isinstance(target, CallbackQuery):
                    msg_obj = target.message
                    try:
                        await msg_obj.edit_text(text, reply_markup=kb)
                    except TelegramBadRequest as e:
                        if "not modified" in str(e).lower():
                            return await target.answer("❌ Hali barcha kanallarga a‘zo bo‘lmagansiz.", show_alert=True)
                        raise
                else:
                    await target.answer(text, reply_markup=kb)
                return
        try:
            await tg_retry(lambda: bot.send_video(user_id, m["file_id"], caption=APP.movies.caption(m)))
        except TelegramAPIError as e:
            logger.error("Kino yuborilmadi (%s): %s", m["movie_id"], e)
            msg = "⚠️ Kinoni yuborib bo‘lmadi. Iltimos, keyinroq urinib ko‘ring."
            return await (target.answer(msg, show_alert=True) if isinstance(target, CallbackQuery) else target.answer(msg))
        m["views_count"] = m.get("views_count", 0) + 1
        APP.users.add_watched(user_id, m["movie_id"])
        APP.db.touch("movies")
        APP.db.touch("users")
        if isinstance(target, CallbackQuery) and edit_window:
            with contextlib.suppress(TelegramBadRequest):
                await target.message.edit_text("✅ A‘zolik tasdiqlandi. Kino yuborildi.")


@user_r.callback_query(F.data.startswith("mv:"))
async def cb_movie(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await deliver_movie(bot, cb, cb.from_user.id, cb.data.split(":", 1)[1], edit_window=False)


@user_r.callback_query(F.data.startswith("chk:"))
async def cb_check(cb: CallbackQuery, bot: Bot):
    movie_id = cb.data.split(":", 1)[1]
    if movie_id == "0":  # eski «Obuna kanallari» oynasidan qolgan tugma
        return await cb.answer("ℹ️ Bu tugma eskirgan. Kino nomini yoki kodini yozing.", show_alert=True)
    await cb.answer()
    await deliver_movie(bot, cb, cb.from_user.id, movie_id, edit_window=True)


@common.message(F.text == BTN_CHANNELS)
async def menu_channels_legacy(message: Message, state: FSMContext):
    """Eski klaviaturada qolib ketgan «Obuna kanallari» tugmasi: tugma olib tashlangan, menyu yangilanadi.
    Kanalga obuna tugmalari faqat kino olish jarayonida ko'rsatiladi."""
    await state.clear()
    is_admin = APP.admins.role_of(message.from_user.id) is not None
    await message.answer("ℹ️ Menyu yangilandi. Majburiy obuna kanallari kinoni olish paytida ko‘rsatiladi.",
                         reply_markup=main_kb(is_admin))


async def do_search(message: Message, text: str) -> None:
    q = text.strip()
    results = APP.movies.search(q)
    if not results:
        if len(norm(q)) <= 1:
            return await message.answer("🔎 Kino nomini yozing, masalan: Astral")
        return await message.answer(f"😕 {esc(trunc(q, 100))} bo‘yicha hech narsa topilmadi.\n\nNomni boshqacha yozib ko‘ring.")
    kb = ikb([[(trunc(m["title"], 60), f"mv:{m['movie_id']}")] for m in results])
    await message.answer(f"🔎 Topildi: {len(results)} ta", reply_markup=kb)


@search_r.message(F.text)
async def text_search(message: Message):
    if message.text.startswith("/"):
        return await message.answer("❓ Noma'lum buyruq. Kino nomini yoki kodini yozing.")
    await do_search(message, message.text)


@search_r.message()
async def other_content(message: Message):
    await message.answer("🔎 Kino nomini yoki kodini matn shaklida yozing.")


# =====================================================================
# 11. ADMIN PANEL: KIRISH, BEKOR QILISH, KINO BOSHQARUVI
# =====================================================================
async def guard(ev: Message | CallbackQuery, perm: str, state: Optional[FSMContext] = None) -> Optional[str]:
    role = await need(ev, perm)
    if role is None and state is not None:
        await state.clear()
    return role


PERM_LABELS = {
    "movie_menu": "kino menyusi", "movie_add": "kino qo‘shish", "movie_view_own": "o‘z kinolarini ko‘rish",
    "movie_delete_own": "o‘z kinolarini o‘chirish", "movie_view_all": "barcha kinolarni ko‘rish",
    "movie_delete_all": "barcha kinolarni o‘chirish", "channels": "majburiy obuna", "stats": "statistika",
    "admins": "admin boshqaruvi", "vip": "VIP boshqaruvi", "payments": "to‘lovlarni tasdiqlash",
    "backup": "backup", "restore": "tiklash", "settings": "sozlamalar", "broadcast": "broadcast",
    "admin_roles": "rollarni o‘zgartirish", "admin_manage_full": "to‘liq adminlarni boshqarish",
    "channel_post": "kanalga post joylash",
}


@admin_r.message(Command("admin"))
@admin_r.message(F.text == BTN_ADMIN)
async def admin_entry(message: Message, state: FSMContext):
    await state.clear()
    role = APP.admins.role_of(message.from_user.id)
    if not role:
        return await message.answer("⛔ Sizda admin huquqi yo‘q.")
    await message.answer(panel_text(role), reply_markup=admin_kb(role))


class IsAdmin(BaseFilter):
    """Faqat admin yuborgan xabarlar o'tadi; oddiy foydalanuvchining matni qidiruvga tushadi."""

    async def __call__(self, message: Message) -> bool:
        return APP.admins.role_of(message.from_user.id) is not None


@admin_r.message(IsAdmin(), F.text.in_({t for t, _ in ADMIN_MENU}))
async def admin_section(message: Message, state: FSMContext):
    """ReplyKeyboard orqali admin bo'limini ochish. Ruxsat server tomonida qayta tekshiriladi.
    Bu handler FSM handlerlaridan oldin turadi: menyu tugmasi bosilsa joriy jarayon bekor qilinadi."""
    await state.clear()
    perm, render = ADMIN_SECTIONS[message.text]
    if not await need(message, perm):
        return
    await render(message, state)


@admin_r.message(F.text == ADM_BTN_BACK)
async def admin_back(message: Message, state: FSMContext):
    await state.clear()
    is_admin = APP.admins.role_of(message.from_user.id) is not None
    await message.answer("🏠 Asosiy menyu.", reply_markup=main_kb(is_admin))


@admin_r.callback_query(F.data.in_({"adm:home", "adm:cancel"}))
async def adm_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    role = APP.admins.role_of(cb.from_user.id)
    if not role:
        return await cb.answer("⛔ Ruxsat yo‘q.", show_alert=True)
    await show(cb, panel_text(role))  # inline tugmalarsiz; asosiy menyu — pastdagi ReplyKeyboard
    await cb.answer("Bekor qilindi." if cb.data == "adm:cancel" else None)


def movie_scope(role: str, uid: int) -> Optional[int]:
    """None = barcha kinolar, aks holda faqat shu admin qo'shganlari."""
    return None if has_perm(role, "movie_view_all") else uid


def can_delete(role: str, uid: int, m: dict) -> bool:
    return has_perm(role, "movie_delete_all") or (has_perm(role, "movie_delete_own") and m["added_by"] == uid)


def can_view(role: str, uid: int, m: dict) -> bool:
    return has_perm(role, "movie_view_all") or (has_perm(role, "movie_view_own") and m["added_by"] == uid)


@admin_r.callback_query(F.data == "adm:mv")
async def mv_menu(cb: CallbackQuery, state: FSMContext):
    await render_mv_menu(cb, state)


async def render_mv_menu(cb: Message | CallbackQuery, state: Optional[FSMContext] = None):
    role = await guard(cb, "movie_menu", state)
    if not role:
        return
    await state.clear()
    scope = "Siz faqat o‘zingiz qo‘shgan kinolarni ko‘rasiz." if movie_scope(role, cb.from_user.id) else "Barcha kinolar bilan ishlay olasiz."
    kb = ikb([
        [("➕ Kino qo‘shish", "adm:mv_add")],
        [("📋 Kinolar ro‘yxati", "adm:mv_list:0"), ("🔍 Qidirish", "adm:mv_find")],
        [("🗑 Kino o‘chirish", "adm:mv_del")],
        [("⬅️ Orqaga", "adm:home")],
    ])
    await show(cb, f"🎬 <b>Kino boshqaruvi</b>\n\n{scope}", kb)
    await ack(cb)


# ---- kino qo'shish ----
@admin_r.callback_query(F.data == "adm:mv_add")
async def mv_add_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "movie_add", state):
        return
    await state.clear()
    await state.set_state(MovieAdd.video)
    await show(cb, "🎬 <b>Kino qo‘shish</b>\n\n1/3 — Kino videosini yuboring.", ikb([CANCEL_ROW]))
    await cb.answer()


def _video_of(message: Message):
    if message.video:
        return message.video
    if message.document and (message.document.mime_type or "").startswith("video/"):
        return message.document
    return None


@admin_r.message(MovieAdd.video, F.video | F.document)
async def mv_add_video(message: Message, state: FSMContext):
    if not await guard(message, "movie_add", state):
        return
    v = _video_of(message)
    if not v:
        return await message.answer("❗ Bu video emas. Iltimos, <b>video</b> yuboring.", reply_markup=ikb([CANCEL_ROW]))
    dup = APP.movies.by_unique(v.file_unique_id)
    if dup:
        return await message.answer(
            f"⚠️ Bu video allaqachon qo‘shilgan: <b>{esc(dup['title'])}</b> (kod: <code>{dup['code']}</code>).\n"
            "Boshqa video yuboring.", reply_markup=ikb([CANCEL_ROW]))
    await state.update_data(file_id=v.file_id, file_unique_id=v.file_unique_id)
    await state.set_state(MovieAdd.description)
    await message.answer("✅ Video qabul qilindi.\n\n2/3 — Kino tavsifini (description) yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(MovieAdd.video)
async def mv_add_video_bad(message: Message):
    await message.answer("❗ Iltimos, kino <b>videosini</b> yuboring (matn, rasm yoki boshqa format emas).", reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(MovieAdd.description, F.text)
async def mv_add_desc(message: Message, state: FSMContext):
    if not await guard(message, "movie_add", state):
        return
    text = message.text.strip()
    if len(text) < 2:
        return await message.answer("❗ Tavsif juda qisqa. Qaytadan yuboring.", reply_markup=ikb([CANCEL_ROW]))
    if len(text) > MAX_DESC_LEN:
        return await message.answer(
            f"❗ Tavsif juda uzun ({len(text)} belgi). Eng ko‘pi bilan {MAX_DESC_LEN} belgi bo‘lishi kerak.",
            reply_markup=ikb([CANCEL_ROW]))
    await state.update_data(description=text)
    await state.set_state(MovieAdd.title)
    await message.answer("✅ Tavsif saqlandi.\n\n3/3 — Kino nomini yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(MovieAdd.description)
async def mv_add_desc_bad(message: Message):
    await message.answer("❗ Tavsifni <b>matn</b> ko‘rinishida yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(MovieAdd.title, F.text)
async def mv_add_title(message: Message, state: FSMContext, bot: Bot):
    role = await guard(message, "movie_add", state)
    if not role:
        return
    title = " ".join(message.text.split())
    if not title or len(title) > MAX_TITLE_LEN:
        return await message.answer(f"❗ Kino nomi 1–{MAX_TITLE_LEN} belgidan iborat bo‘lishi kerak.", reply_markup=ikb([CANCEL_ROW]))
    data = await state.get_data()
    # Tekshiruv va qo'shish orasida await yo'q: bir vaqtda qo'shilgan kinolarda ham nom/video/kod takrorlanmaydi
    if any(norm(m["title"]) == norm(title) for m in APP.movies.items.values()):
        return await message.answer(
            "⚠️ Bunday nomli kino allaqachon bor. Nomni aniqroq yozing (masalan, yil yoki qism raqamini qo‘shing).",
            reply_markup=ikb([CANCEL_ROW]))
    if not data.get("file_id") or APP.movies.by_unique(data.get("file_unique_id", "")):
        await state.clear()
        return await message.answer("⚠️ Jarayon buzildi yoki video takrorlangan. Qaytadan boshlang.", reply_markup=ikb([[("⬅️ Orqaga", "adm:mv")]]))
    m = APP.movies.add(title, data["description"], data["file_id"], data["file_unique_id"], message.from_user.id)
    await APP.save("settings", "movies")  # avval hisoblagich, keyin kino: uzilishda kod takrorlanmaydi
    APP.stats.log_admin(message.from_user.id, "movie_add", f"{m['code']} {m['title']}")
    await state.clear()
    text, kb = await movie_added_message(bot, m)
    await message.answer(text, reply_markup=kb)


async def movie_added_message(bot: Bot, m: dict) -> tuple[str, InlineKeyboardMarkup]:
    link = await movie_link(bot, m["code"])
    text = (
        "✅ Kino muvaffaqiyatli qo‘shildi!\n\n"
        f"🎬 Kino: {esc(m['title'])}\n"
        f"🔢 Kino kodi: <code>{m['code']}</code>\n\n"
    )
    rows: list = []
    if link:
        text += f"🔗 Kino havolasi:\n<code>{esc(link)}</code>\n\n<i>Havolani bosib nusxalash mumkin.</i>"
        rows.append([("📋 Havolani nusxalash", "copy", link)])
    else:
        text += "⚠️ Bot usernamesi aniqlanmadi, havola yaratib bo‘lmadi. .env ga BOT_USERNAME qo‘shing."
    rows.append([("➕ Yana kino qo‘shish", "adm:mv_add")])
    rows.append([("⬅️ Kino boshqaruvi", "adm:mv")])
    return text, ikb(rows)


@admin_r.message(MovieAdd.title)
async def mv_add_title_bad(message: Message):
    await message.answer("❗ Kino nomini <b>matn</b> ko‘rinishida yuboring.", reply_markup=ikb([CANCEL_ROW]))


# ---- ro'yxat, ma'lumot, qidirish ----
@admin_r.callback_query(F.data.startswith("adm:mv_list:"))
async def mv_list(cb: CallbackQuery):
    role = await guard(cb, "movie_menu")
    if not role:
        return
    page = int(cb.data.rsplit(":", 1)[1])
    items = APP.movies.listing(movie_scope(role, cb.from_user.id))
    chunk, page, pages = paginate(items, page)
    rows = [[(trunc(f"{m['title']} · {m['code']}", 60), f"adm:mv_i:{m['movie_id']}")] for m in chunk]
    nav = []
    if page > 0:
        nav.append(("◀️", f"adm:mv_list:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"adm:mv_list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⬅️ Orqaga", "adm:mv")])
    title = "📋 <b>Kinolar ro‘yxati</b>" if movie_scope(role, cb.from_user.id) is None else "📋 <b>Siz qo‘shgan kinolar</b>"
    body = f"{title} — {len(items)} ta ({page + 1}/{pages})" if items else f"{title}\n\nHozircha kino yo‘q."
    await show(cb, body, ikb(rows))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:mv_i:"))
async def mv_info(cb: CallbackQuery, bot: Bot):
    role = await guard(cb, "movie_menu")
    if not role:
        return
    m = APP.movies.get(cb.data.rsplit(":", 1)[1])
    if not m or not can_view(role, cb.from_user.id, m):
        return await cb.answer("Kino topilmadi yoki ruxsat yo‘q.", show_alert=True)
    adder = APP.users.get(m["added_by"]) or APP.admins.items.get(str(m["added_by"])) or {}
    text = (
        f"🎬 <b>{esc(m['title'])}</b>\n\n"
        f"🔢 Kod: <code>{m['code']}</code>\n"
        f"📝 Tavsif: {esc(trunc(m['description'], 400))}\n"
        f"👤 Qo‘shgan admin: {esc(adder.get('first_name', '—'))} (<code>{m['added_by']}</code>)\n"
        f"📅 Qo‘shilgan: {fmt_dt(parse_iso(m['added_at']))}\n"
        f"👁 Ko‘rishlar: <b>{m.get('views_count', 0)}</b>"
    )
    rows = []
    link = await movie_link(bot, m["code"])
    if link:
        text += f"\n🔗 Havola: <code>{esc(link)}</code>"
        rows.append([("📋 Havolani nusxalash", "copy", link)])
    if can_delete(role, cb.from_user.id, m):
        rows.append([("🗑 O‘chirish", f"adm:mv_x:{m['movie_id']}")])
    rows.append([("⬅️ Orqaga", "adm:mv")])
    await show(cb, text, ikb(rows))
    await cb.answer()


@admin_r.callback_query(F.data == "adm:mv_find")
async def mv_find_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "movie_menu", state):
        return
    await state.clear()
    await state.set_state(MovieFind.query)
    await show(cb, "🔍 Kino nomini yoki kodini yozing.", ikb([CANCEL_ROW]))
    await cb.answer()


@admin_r.message(MovieFind.query, F.text)
async def mv_find_do(message: Message, state: FSMContext):
    role = await guard(message, "movie_menu", state)
    if not role:
        return
    res = APP.movies.search(message.text, movie_scope(role, message.from_user.id))
    await state.clear()
    if not res:
        return await message.answer("😕 Hech narsa topilmadi.", reply_markup=ikb([[("🔍 Qayta qidirish", "adm:mv_find")], [("⬅️ Orqaga", "adm:mv")]]))
    rows = [[(trunc(f"{m['title']} · {m['code']}", 60), f"adm:mv_i:{m['movie_id']}")] for m in res]
    rows.append([("⬅️ Orqaga", "adm:mv")])
    await message.answer(f"🔎 Topildi: {len(res)} ta", reply_markup=ikb(rows))


@admin_r.message(MovieFind.query)
async def mv_find_bad(message: Message):
    await message.answer("❗ Matn yuboring.", reply_markup=ikb([CANCEL_ROW]))


# ---- o'chirish ----
@admin_r.callback_query(F.data == "adm:mv_del")
async def mv_del_menu(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "movie_menu", state):
        return
    await state.clear()
    kb = ikb([
        [("🔢 Kod orqali o‘chirish", "adm:mv_delcode")],
        [("📋 Ro‘yxatdan tanlash", "adm:mv_list:0")],
        [("⬅️ Orqaga", "adm:mv")],
    ])
    await show(cb, "🗑 <b>Kino o‘chirish</b>\n\nUsulni tanlang:", kb)
    await cb.answer()


@admin_r.callback_query(F.data == "adm:mv_delcode")
async def mv_delcode_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "movie_menu", state):
        return
    await state.set_state(MovieDel.code)
    await show(cb, "🔢 O‘chirmoqchi bo‘lgan kinoning <b>kodini</b> yuboring.", ikb([CANCEL_ROW]))
    await cb.answer()


@admin_r.message(MovieDel.code, F.text)
async def mv_delcode_do(message: Message, state: FSMContext):
    role = await guard(message, "movie_menu", state)
    if not role:
        return
    m = APP.movies.by_code(message.text)
    if not m or not can_view(role, message.from_user.id, m):
        return await message.answer("😕 Bunday kodli kino topilmadi (yoki bu kino sizga tegishli emas). Kodni qayta yuboring.", reply_markup=ikb([CANCEL_ROW]))
    if not can_delete(role, message.from_user.id, m):
        await state.clear()
        return await message.answer("⛔ Bu kinoni o‘chirishga ruxsatingiz yo‘q.")
    await state.clear()
    await message.answer(
        f"❓ <b>{esc(m['title'])}</b> (kod: <code>{m['code']}</code>) kinosini o‘chirishni tasdiqlaysizmi?",
        reply_markup=ikb([[("✅ Ha, o‘chirish", f"adm:mv_xy:{m['movie_id']}"), ("❌ Yo‘q", "adm:mv")]]))


@admin_r.message(MovieDel.code)
async def mv_delcode_bad(message: Message):
    await message.answer("❗ Kino kodini matn ko‘rinishida yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.callback_query(F.data.startswith("adm:mv_x:"))
async def mv_del_ask(cb: CallbackQuery):
    role = await guard(cb, "movie_menu")
    if not role:
        return
    m = APP.movies.get(cb.data.rsplit(":", 1)[1])
    if not m or not can_delete(role, cb.from_user.id, m):
        return await cb.answer("Kino topilmadi yoki o‘chirishga ruxsat yo‘q.", show_alert=True)
    await show(cb, f"❓ <b>{esc(m['title'])}</b> (kod: <code>{m['code']}</code>) kinosini o‘chirishni tasdiqlaysizmi?",
               ikb([[("✅ Ha, o‘chirish", f"adm:mv_xy:{m['movie_id']}"), ("❌ Yo‘q", f"adm:mv_i:{m['movie_id']}")]]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:mv_xy:"))
async def mv_del_do(cb: CallbackQuery):
    role = await guard(cb, "movie_menu")
    if not role:
        return
    m = APP.movies.get(cb.data.rsplit(":", 1)[1])
    if not m:
        return await cb.answer("Kino allaqachon o‘chirilgan.", show_alert=True)
    if not can_delete(role, cb.from_user.id, m):
        return await cb.answer("⛔ Bu kinoni o‘chirishga ruxsatingiz yo‘q.", show_alert=True)
    APP.movies.delete(m["movie_id"])
    await APP.save("movies")
    APP.stats.log_admin(cb.from_user.id, "movie_delete", f"{m['code']} {m['title']}")
    await show(cb, f"✅ <b>{esc(m['title'])}</b> o‘chirildi.", ikb([[("⬅️ Kino boshqaruvi", "adm:mv")]]))
    await cb.answer()


# =====================================================================
# 12. TO'LOVNI KO'RIB CHIQISH (faqat asosiy admin)
# =====================================================================
def payment_caption(p: dict, footer: str = "") -> str:
    u = APP.users.get(p["user_id"]) or {"user_id": p["user_id"], "first_name": "?", "username": None}
    plan = PLANS[p["plan"]]
    return (
        "💳 <b>VIP to‘lov so‘rovi</b>\n\n"
        f"👤 Foydalanuvchi: {user_link_name(u)}\n"
        f"🆔 Telegram ID: <code>{u['user_id']}</code>\n"
        f"🔗 Username: {'@' + esc(u['username']) if u.get('username') else '—'}\n"
        f"💎 Tarif: {plan['label']}\n"
        f"💰 Summa: {fmt_money(p['amount'])}\n"
        f"🕒 So‘rov vaqti: {fmt_dt(parse_iso(p.get('receipt_sent_at')))}\n"
        f"🧾 So‘rov ID: <code>{p['payment_id']}</code>{footer}"
    )


def review_kb(pid: str) -> InlineKeyboardMarkup:
    return ikb([[("✅ Tasdiqlash", f"pay_ok:{pid}"), ("❌ Rad etish", f"pay_no:{pid}")]])


async def _drop_markup(cb: CallbackQuery) -> None:
    with contextlib.suppress(TelegramAPIError):
        await cb.message.edit_reply_markup(reply_markup=None)


@admin_r.callback_query(F.data.regexp(r"^pay_(ok|no):"))
async def pay_review_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "payments", state):
        return
    action, pid = cb.data.split(":", 1)
    p = APP.payments.items.get(pid)
    if not p or p["status"] != "pending":
        await _drop_markup(cb)
        return await cb.answer("Bu so‘rov allaqachon ko‘rib chiqilgan.", show_alert=True)
    await state.clear()
    await state.update_data(pid=pid)
    if action == "pay_ok":
        await state.set_state(PayReview.comment)
        prompt = "📝 Foydalanuvchiga yuboriladigan <b>izoh</b> yoki fikringizni yozing:"
    else:
        await state.set_state(PayReview.reason)
        prompt = "✏️ Rad etish <b>sababini</b> yozing (u foydalanuvchiga yuboriladi):"
    await _drop_markup(cb)
    await cb.message.answer(f"🧾 <code>{pid}</code>\n\n{prompt}", reply_markup=ikb([[("⬅️ Orqaga qaytish", f"pay_back:{pid}")]]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("pay_back:"))
async def pay_back(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "payments", state):
        return
    await state.clear()
    pid = cb.data.split(":", 1)[1]
    p = APP.payments.items.get(pid)
    with contextlib.suppress(TelegramAPIError):
        await cb.message.delete()
    if p and p["status"] == "pending" and p.get("admin_chat_id"):
        with contextlib.suppress(TelegramAPIError):
            await cb.bot.edit_message_reply_markup(chat_id=p["admin_chat_id"], message_id=p["admin_message_id"], reply_markup=review_kb(pid))
    await cb.answer("Bekor qilindi.")


async def _finish_review(message: Message, bot: Bot, p: dict, user_text: str, footer: str) -> None:
    await APP.save("payments", "users", "vip", "settings")
    if p.get("admin_chat_id"):
        with contextlib.suppress(TelegramAPIError):
            await bot.edit_message_caption(chat_id=p["admin_chat_id"], message_id=p["admin_message_id"],
                                           caption=payment_caption(p, footer), reply_markup=None)
    sent = await notify_user(bot, p["user_id"], user_text)
    note = "" if sent else "\n⚠️ Foydalanuvchiga xabar yetkazilmadi (u botni bloklagan bo‘lishi mumkin)."
    await message.answer(f"Bajarildi.{note}", reply_markup=ikb([[("💎 VIP boshqaruvi", "adm:vip")]]))


@admin_r.message(PayReview.comment, F.text)
async def pay_approve_comment(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "payments", state):
        return
    comment = message.text.strip()
    if not comment or len(comment) > 500:
        return await message.answer("❗ Izoh 1–500 belgidan iborat bo‘lishi kerak.")
    pid = (await state.get_data()).get("pid")
    await state.clear()
    p = APP.payments.approve(pid, message.from_user.id, comment)
    if not p:
        return await message.answer("⚠️ Bu so‘rov allaqachon ko‘rib chiqilgan.")
    APP.stats.log_admin(message.from_user.id, "payment_approve", f"{pid} user={p['user_id']}")
    end = parse_iso(p["vip_end"])
    text = (
        "🎉 Tabriklaymiz! VIP obunangiz faollashtirildi.\n\n"
        f"💎 Tarif: {PLANS[p['plan']]['label']}\n"
        f"📅 Tugash sanasi: {fmt_dt(end)}\n\n"
        f"📝 Admin izohi:\n{esc(comment)}\n\n"
        "Endi siz kinolarni majburiy obunasiz tomosha qilishingiz mumkin!"
    )
    await _finish_review(message, bot, p, text, "\n\n✅ <b>TASDIQLANDI</b>")


@admin_r.message(PayReview.reason, F.text)
async def pay_reject_reason(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "payments", state):
        return
    reason = message.text.strip()
    if not reason or len(reason) > 500:
        return await message.answer("❗ Sabab 1–500 belgidan iborat bo‘lishi kerak.")
    pid = (await state.get_data()).get("pid")
    await state.clear()
    p = APP.payments.reject(pid, message.from_user.id, reason)
    if not p:
        return await message.answer("⚠️ Bu so‘rov allaqachon ko‘rib chiqilgan.")
    APP.stats.log_admin(message.from_user.id, "payment_reject", f"{pid} user={p['user_id']}")
    text = (
        "❌ VIP obuna so‘rovingiz rad etildi.\n\n"
        f"Sabab:\n{esc(reason)}\n\n"
        "Agar xohlasangiz, qayta to‘lov so‘rovi yuborishingiz mumkin."
    )
    await _finish_review(message, bot, p, text, "\n\n❌ <b>RAD ETILDI</b>")


@admin_r.message(PayReview.comment)
@admin_r.message(PayReview.reason)
async def pay_review_bad(message: Message):
    await message.answer("❗ Iltimos, matn yuboring.")


# =====================================================================
# 13. MAJBURIY OBUNA: ADMIN BOSHQARUVI
# =====================================================================
PRIVATE_LINK_RE = re.compile(r"^(?:https?://)?t\.me/(?:\+[\w-]{8,}|joinchat/[\w-]{8,})/?$")


def parse_chat_ref(text: str) -> Optional[int | str]:
    t = text.strip()
    if re.fullmatch(r"-?\d{5,20}", t):
        return int(t)
    m = re.fullmatch(r"(?:https?://)?t\.me/([A-Za-z]\w{3,})/?", t)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"@?[A-Za-z]\w{3,}", t):
        return "@" + t.lstrip("@")
    return None


def norm_link(text: str) -> str:
    t = text.strip().rstrip("/")
    return t if t.startswith("http") else "https://" + t


async def inspect_chat(bot: Bot, ref: int | str) -> tuple[Optional[dict], Optional[str]]:
    """Kanal mavjudligi va botning administratorlik huquqlarini tekshiradi."""
    try:
        chat = await tg_retry(lambda: bot.get_chat(ref))
    except TelegramAPIError:
        return None, "Kanal/guruh topilmadi yoki bot unga qo‘shilmagan. Avval botni kanalga administrator qilib qo‘shing."
    if chat.type == ChatType.PRIVATE:
        return None, "Bu kanal yoki guruh emas."
    try:
        me = await tg_retry(lambda: bot.get_chat_member(chat.id, bot.id))
    except TelegramAPIError:
        return None, "Botning kanaldagi huquqlarini aniqlab bo‘lmadi. Botni administrator qiling."
    if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return None, "Bot bu kanalda administrator emas. A‘zolikni tekshirish uchun botga administrator huquqi bering."
    can_invite = me.status == ChatMemberStatus.CREATOR or bool(getattr(me, "can_invite_users", False))
    return {"id": chat.id, "title": chat.title or str(chat.id), "username": chat.username, "can_invite": can_invite}, None


def ch_menu_kb() -> InlineKeyboardMarkup:
    on = APP.settings.get("subscription_enabled", True)
    return ikb([
        [("➕ Ochiq kanal/guruh", "adm:ch_add:public"), ("➕ Private kanal/guruh", "adm:ch_add:private")],
        [("📋 Kanallar ro‘yxati", "adm:ch_list")],
        [(f"{'🟢 Obuna yoqilgan — o‘chirish' if on else '🔴 Obuna o‘chirilgan — yoqish'}", "adm:ch_tog")],
        [("⬅️ Orqaga", "adm:home")],
    ])


def ch_menu_text() -> str:
    on = APP.settings.get("subscription_enabled", True)
    return (
        "📢 <b>Majburiy obuna</b>\n\n"
        f"Holat: {'🟢 yoqilgan' if on else '🔴 vaqtincha o‘chirilgan'}\n"
        f"Kanallar soni: <b>{len(APP.subs.channels)}</b> (faol: {len(APP.subs.active_channels())})"
    )


@admin_r.callback_query(F.data == "adm:ch")
async def ch_menu(cb: CallbackQuery, state: FSMContext):
    await render_ch_menu(cb, state)


async def render_ch_menu(cb: Message | CallbackQuery, state: Optional[FSMContext] = None):
    if not await guard(cb, "channels", state):
        return
    await state.clear()
    await show(cb, ch_menu_text(), ch_menu_kb())
    await ack(cb)


@admin_r.callback_query(F.data == "adm:ch_tog")
async def ch_toggle(cb: CallbackQuery):
    if not await guard(cb, "channels"):
        return
    APP.settings["subscription_enabled"] = not APP.settings.get("subscription_enabled", True)
    await APP.save("settings")
    APP.stats.log_admin(cb.from_user.id, "subscription_toggle", str(APP.settings["subscription_enabled"]))
    await show(cb, ch_menu_text(), ch_menu_kb())
    await cb.answer("Saqlandi.")


@admin_r.callback_query(F.data.startswith("adm:ch_add:"))
async def ch_add_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "channels", state):
        return
    ctype = cb.data.rsplit(":", 1)[1]
    if ctype not in ("public", "private"):
        return await cb.answer()
    await state.clear()
    await state.set_state(ChannelAdd.ref)
    await state.update_data(ctype=ctype)
    if ctype == "public":
        txt = ("📢 <b>Ochiq kanal/guruh qo‘shish</b>\n\n"
               "Avval botni kanalga <b>administrator</b> qiling, so‘ng kanal username (@kanal) yoki chat ID sini yuboring.")
    else:
        txt = ("🔒 <b>Private kanal/guruh qo‘shish</b>\n\n"
               "Avval botni kanalga <b>administrator</b> qiling (taklif havolalarini boshqarish huquqi bilan), "
               "so‘ng kanalning chat ID sini yuboring (masalan: <code>-1001234567890</code>).\n\n"
               "ℹ️ Chat ID ni bilish uchun kanaldan biror xabarni @getidsbot ga forward qiling.")
    await show(cb, txt, ikb([CANCEL_ROW]))
    await cb.answer()


async def _save_channel(chat: dict, ctype: str, link: str) -> dict:
    rec = {
        "channel_id": chat["id"], "title": chat["title"], "channel_type": ctype,
        "username": chat["username"], "invite_link": link, "is_active": True,
    }
    APP.subs.channels[str(chat["id"])] = rec
    await APP.save("channels")
    return rec


@admin_r.message(ChannelAdd.ref, F.text)
async def ch_add_ref(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "channels", state):
        return
    ref = parse_chat_ref(message.text)
    if ref is None:
        return await message.answer("❗ Format noto‘g‘ri. @username yoki chat ID (masalan -1001234567890) yuboring.", reply_markup=ikb([CANCEL_ROW]))
    data = await state.get_data()
    ctype = data["ctype"]
    chat, err = await inspect_chat(bot, ref)
    if err:
        return await message.answer(f"❗ {err}", reply_markup=ikb([CANCEL_ROW]))
    if str(chat["id"]) in APP.subs.channels:
        await state.clear()
        return await message.answer("⚠️ Bu kanal allaqachon qo‘shilgan.", reply_markup=ikb([[("⬅️ Orqaga", "adm:ch")]]))
    if ctype == "public" and not chat["username"]:
        return await message.answer("❗ Bu kanalda ochiq username yo‘q — uni «Private kanal» sifatida qo‘shing.", reply_markup=ikb([CANCEL_ROW]))
    if ctype == "private" and chat["username"]:
        return await message.answer("❗ Bu kanal ochiq (username bor) — uni «Ochiq kanal» sifatida qo‘shing.", reply_markup=ikb([CANCEL_ROW]))
    if ctype == "public":
        rec = await _save_channel(chat, ctype, f"https://t.me/{chat['username']}")
        APP.stats.log_admin(message.from_user.id, "channel_add", f"{rec['channel_id']} public")
        await state.clear()
        return await message.answer(
            f"✅ Ochiq kanal qo‘shildi: <b>{esc(rec['title'])}</b>\nHavola: {rec['invite_link']}",
            reply_markup=ikb([[("⬅️ Majburiy obuna", "adm:ch")]]))
    await state.update_data(chat=chat)
    await state.set_state(ChannelAdd.link)
    rows = []
    if chat["can_invite"]:
        rows.append([("🤖 So‘rovli havolani avtomatik yaratish", "adm:ch_auto")])
    rows.append(CANCEL_ROW)
    await message.answer(
        f"✅ Kanal topildi: <b>{esc(chat['title'])}</b>\n\n"
        "Endi <b>join request (so‘rov bilan qo‘shilish)</b> yoqilgan taklif havolasini yuboring (t.me/+...).\n"
        "Yoki pastdagi tugma orqali bot o‘zi yaratib bersin.\n\n"
        "⚠️ Havola «So‘rov yuborish orqali qo‘shilish» (Request admin approval) rejimida bo‘lishi kerak.",
        reply_markup=ikb(rows))


@admin_r.callback_query(ChannelAdd.link, F.data == "adm:ch_auto")
async def ch_add_auto(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not await guard(cb, "channels", state):
        return
    chat = (await state.get_data()).get("chat")
    if not chat:
        await state.clear()
        return await cb.answer("Jarayon buzildi. Qaytadan boshlang.", show_alert=True)
    try:
        link = await tg_retry(lambda: bot.create_chat_invite_link(chat["id"], name="KinoBot", creates_join_request=True))
    except TelegramAPIError as e:
        return await cb.answer(f"Havola yaratib bo‘lmadi: {str(e)[:150]}", show_alert=True)
    rec = await _save_channel(chat, "private", link.invite_link)
    APP.stats.log_admin(cb.from_user.id, "channel_add", f"{rec['channel_id']} private(auto)")
    await state.clear()
    await show(cb, f"✅ Private kanal qo‘shildi: <b>{esc(rec['title'])}</b>\nHavola: {rec['invite_link']}", ikb([[("⬅️ Majburiy obuna", "adm:ch")]]))
    await cb.answer()


@admin_r.message(ChannelAdd.link, F.text)
async def ch_add_link(message: Message, state: FSMContext):
    if not await guard(message, "channels", state):
        return
    if not PRIVATE_LINK_RE.match(message.text.strip()):
        return await message.answer("❗ Havola formati noto‘g‘ri. Masalan: https://t.me/+AbCdEfGh123", reply_markup=ikb([CANCEL_ROW]))
    chat = (await state.get_data()).get("chat")
    if not chat:
        await state.clear()
        return await message.answer("⚠️ Jarayon buzildi. Qaytadan boshlang.")
    rec = await _save_channel(chat, "private", norm_link(message.text))
    APP.stats.log_admin(message.from_user.id, "channel_add", f"{rec['channel_id']} private")
    await state.clear()
    await message.answer(f"✅ Private kanal qo‘shildi: <b>{esc(rec['title'])}</b>", reply_markup=ikb([[("⬅️ Majburiy obuna", "adm:ch")]]))


@admin_r.message(ChannelAdd.ref)
@admin_r.message(ChannelAdd.link)
async def ch_add_bad(message: Message):
    await message.answer("❗ Iltimos, matn yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.callback_query(F.data == "adm:ch_list")
async def ch_list(cb: CallbackQuery):
    if not await guard(cb, "channels"):
        return
    chans = list(APP.subs.channels.values())
    if not chans:
        return await show(cb, "📋 Kanallar ro‘yxati bo‘sh.", ikb([[("⬅️ Orqaga", "adm:ch")]])) or await cb.answer()
    rows = [[(trunc(f"{'🟢' if c['is_active'] else '⚪️'} {'🔒' if c['channel_type'] == 'private' else '📢'} {c['title']}", 60), f"adm:ch_i:{c['channel_id']}")] for c in chans]
    rows.append([("⬅️ Orqaga", "adm:ch")])
    await show(cb, "📋 <b>Kanallar ro‘yxati</b>\n\n🟢 faol · ⚪️ nofaol · 🔒 private · 📢 ochiq", ikb(rows))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:ch_i:"))
async def ch_info(cb: CallbackQuery, bot: Bot):
    if not await guard(cb, "channels"):
        return
    await render_channel(cb, bot, cb.data.rsplit(":", 1)[1])


async def render_channel(cb: CallbackQuery, bot: Bot, cid: str) -> None:
    c = APP.subs.channels.get(cid)
    if not c:
        return await cb.answer("Kanal topilmadi.", show_alert=True)
    chat, err = await inspect_chat(bot, c["channel_id"])
    health = "✅ Bot administrator, a‘zolikni tekshira oladi." if chat else f"⚠️ Muammo: {esc(err)}"
    cnt = ""
    if chat:
        with contextlib.suppress(TelegramAPIError):
            cnt = f"\n👥 A‘zolar: <b>{await bot.get_chat_member_count(c['channel_id'])}</b>"
    waiting = len(APP.subs.requests.get(cid, {}))
    text = (
        f"{'🔒' if c['channel_type'] == 'private' else '📢'} <b>{esc(c['title'])}</b>\n\n"
        f"🆔 Chat ID: <code>{c['channel_id']}</code>\n"
        f"Turi: {'private (join request)' if c['channel_type'] == 'private' else 'ochiq'}\n"
        f"Username: {'@' + esc(c['username']) if c.get('username') else '—'}\n"
        f"Havola: {esc(c['invite_link'])}\n"
        f"Holat: {'🟢 faol' if c['is_active'] else '⚪️ nofaol'}{cnt}\n"
        f"⏳ Tasdiq kutayotgan so‘rovlar (bot ko‘rgan): <b>{waiting}</b>\n\n{health}"
    )
    rows = [[("🔄 Qayta tekshirish", f"adm:ch_i:{cid}")]]
    if c["channel_type"] == "private":
        rows.append([("🔗 Invite linkni yangilash", f"adm:ch_lnk:{cid}")])
    rows.append([("⏸ Nofaol qilish" if c["is_active"] else "▶️ Faollashtirish", f"adm:ch_t:{cid}")])
    rows.append([("🗑 O‘chirish", f"adm:ch_x:{cid}")])
    rows.append([("⬅️ Ro‘yxat", "adm:ch_list")])
    await show(cb, text, ikb(rows))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:ch_t:"))
async def ch_item_toggle(cb: CallbackQuery, bot: Bot):
    if not await guard(cb, "channels"):
        return
    c = APP.subs.channels.get(cb.data.rsplit(":", 1)[1])
    if not c:
        return await cb.answer("Kanal topilmadi.", show_alert=True)
    c["is_active"] = not c["is_active"]
    await APP.save("channels")
    APP.stats.log_admin(cb.from_user.id, "channel_toggle", f"{c['channel_id']} {c['is_active']}")
    await render_channel(cb, bot, str(c["channel_id"]))


@admin_r.callback_query(F.data.startswith("adm:ch_x:"))
async def ch_del_ask(cb: CallbackQuery):
    if not await guard(cb, "channels"):
        return
    cid = cb.data.rsplit(":", 1)[1]
    c = APP.subs.channels.get(cid)
    if not c:
        return await cb.answer("Kanal topilmadi.", show_alert=True)
    await show(cb, f"❓ <b>{esc(c['title'])}</b> kanalini majburiy obunadan o‘chirishni tasdiqlaysizmi?",
               ikb([[("✅ Ha", f"adm:ch_xy:{cid}"), ("❌ Yo‘q", f"adm:ch_i:{cid}")]]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:ch_xy:"))
async def ch_del_do(cb: CallbackQuery):
    if not await guard(cb, "channels"):
        return
    cid = cb.data.rsplit(":", 1)[1]
    c = APP.subs.channels.pop(cid, None)
    if c:
        APP.subs.requests.pop(cid, None)
        await APP.save("channels", "join_requests")
        APP.stats.log_admin(cb.from_user.id, "channel_delete", f"{cid} {c['title']}")
    await show(cb, "✅ Kanal o‘chirildi.", ikb([[("⬅️ Majburiy obuna", "adm:ch")]]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:ch_lnk:"))
async def ch_link_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "channels", state):
        return
    cid = cb.data.rsplit(":", 1)[1]
    c = APP.subs.channels.get(cid)
    if not c or c["channel_type"] != "private":
        return await cb.answer("Faqat private kanal havolasini yangilash mumkin.", show_alert=True)
    await state.clear()
    await state.set_state(ChannelLink.link)
    await state.update_data(cid=cid)
    await show(cb, "🔗 Yangi taklif havolasini yuboring (t.me/+...) yoki avtomatik yaratishni tanlang.",
               ikb([[("🤖 Avtomatik yaratish", "adm:ch_lauto")], CANCEL_ROW]))
    await cb.answer()


async def _apply_link(cid: str, link: str, admin_id: int) -> Optional[dict]:
    c = APP.subs.channels.get(cid)
    if not c:
        return None
    c["invite_link"] = link
    await APP.save("channels")
    APP.stats.log_admin(admin_id, "channel_link_update", cid)
    return c


@admin_r.callback_query(ChannelLink.link, F.data == "adm:ch_lauto")
async def ch_link_auto(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not await guard(cb, "channels", state):
        return
    cid = (await state.get_data()).get("cid")
    c = APP.subs.channels.get(cid or "")
    if not c:
        await state.clear()
        return await cb.answer("Kanal topilmadi.", show_alert=True)
    try:
        link = await tg_retry(lambda: bot.create_chat_invite_link(c["channel_id"], name="KinoBot", creates_join_request=True))
    except TelegramAPIError as e:
        return await cb.answer(f"Havola yaratib bo‘lmadi: {str(e)[:150]}", show_alert=True)
    await _apply_link(cid, link.invite_link, cb.from_user.id)
    await state.clear()
    await show(cb, f"✅ Havola yangilandi:\n{link.invite_link}", ikb([[("⬅️ Kanal", f"adm:ch_i:{cid}")]]))
    await cb.answer()


@admin_r.message(ChannelLink.link, F.text)
async def ch_link_text(message: Message, state: FSMContext):
    if not await guard(message, "channels", state):
        return
    if not PRIVATE_LINK_RE.match(message.text.strip()):
        return await message.answer("❗ Havola formati noto‘g‘ri. Masalan: https://t.me/+AbCdEfGh123", reply_markup=ikb([CANCEL_ROW]))
    cid = (await state.get_data()).get("cid")
    c = await _apply_link(cid or "", norm_link(message.text), message.from_user.id)
    await state.clear()
    await message.answer("✅ Havola yangilandi." if c else "⚠️ Kanal topilmadi.", reply_markup=ikb([[("⬅️ Majburiy obuna", "adm:ch")]]))


@admin_r.message(ChannelLink.link)
async def ch_link_bad(message: Message):
    await message.answer("❗ Iltimos, havolani matn ko‘rinishida yuboring.", reply_markup=ikb([CANCEL_ROW]))


# =====================================================================
# 14. STATISTIKA
# =====================================================================
@admin_r.callback_query(F.data == "adm:st")
async def st_menu(cb: CallbackQuery):
    await render_st_menu(cb)


async def render_st_menu(cb: Message | CallbackQuery, state: Optional[FSMContext] = None):
    if not await guard(cb, "stats"):
        return
    kb = ikb([
        [("👥 Foydalanuvchilar", "adm:st_u"), ("🎬 Kinolar", "adm:st_m")],
        [("📈 Faollik", "adm:st_a"), ("💎 VIP", "adm:st_v")],
        [("👮 Adminlar", "adm:st_ad"), ("👁 Ko‘rishlar", "adm:st_w:0")],
        [("⬅️ Orqaga", "adm:home")],
    ])
    await show(cb, "📊 <b>Statistika</b>\n\nBo‘limni tanlang:", kb)
    await ack(cb)


@admin_r.callback_query(F.data.in_({"adm:st_u", "adm:st_m", "adm:st_a", "adm:st_v", "adm:st_ad"}))
async def st_show(cb: CallbackQuery):
    if not await guard(cb, "stats"):
        return
    fn = {"adm:st_u": APP.stats.users_text, "adm:st_m": APP.stats.movies_text, "adm:st_a": APP.stats.activity_text,
          "adm:st_v": APP.stats.vip_text, "adm:st_ad": APP.stats.admins_text}[cb.data]
    await show(cb, trunc(fn(), 4000), ikb([[("🔄 Yangilash", cb.data)], [("⬅️ Statistika", "adm:st")]]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:st_w:"))
async def st_views(cb: CallbackQuery):
    if not await guard(cb, "stats"):
        return
    text, pages = APP.stats.movies_views_page(int(cb.data.rsplit(":", 1)[1]))
    page = int(re.search(r"\((\d+)/", text).group(1)) - 1
    nav = []
    if page > 0:
        nav.append(("◀️", f"adm:st_w:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"adm:st_w:{page + 1}"))
    rows = ([nav] if nav else []) + [[("⬅️ Statistika", "adm:st")]]
    await show(cb, trunc(text, 4000), ikb(rows))
    await cb.answer()


# =====================================================================
# 15. ADMIN BOSHQARUVI
# =====================================================================
def perms_text(role: str) -> str:
    return ", ".join(PERM_LABELS[p] for p in sorted(ROLE_PERMS[role], key=lambda x: list(PERM_LABELS).index(x)))


def admin_detail(a: dict) -> str:
    return (
        f"👤 <b>{esc(a['first_name'])}</b>\n"
        f"🆔 ID: <code>{a['user_id']}</code>\n"
        f"🏷 Turi: {ROLE_LABELS[a['role']]}\n"
        f"📅 Qo‘shilgan: {fmt_dt(parse_iso(a['added_at']))}\n"
        f"🔑 Ruxsatlari: {esc(perms_text(a['role']))}"
    )


def can_manage(actor_role: str, target_role: str) -> bool:
    if target_role == ROLE_MAIN:
        return False
    if target_role == ROLE_FULL:
        return has_perm(actor_role, "admin_manage_full")
    return has_perm(actor_role, "admins")


@admin_r.callback_query(F.data.regexp(r"^adm:ad$|^adm:ad_list:\d+$"))
async def ad_list(cb: CallbackQuery, state: FSMContext):
    page = int(cb.data.rsplit(":", 1)[1]) if cb.data.startswith("adm:ad_list:") else 0
    await render_ad_list(cb, state, page)


async def render_ad_list(cb: Message | CallbackQuery, state: Optional[FSMContext] = None, page: int = 0):
    if not await guard(cb, "admins", state):
        return
    await state.clear()
    chunk, page, pages = paginate(APP.admins.listing(), page, 5)
    body = "\n\n".join(admin_detail(a) for a in chunk)
    rows = [[(trunc(f"{a['first_name']} · {ROLE_LABELS[a['role']].split(' (')[0]}", 60), f"adm:ad_i:{a['user_id']}")] for a in chunk]
    nav = []
    if page > 0:
        nav.append(("◀️", f"adm:ad_list:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"adm:ad_list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("➕ Admin qo‘shish", "adm:ad_add")])
    rows.append([("⬅️ Orqaga", "adm:home")])
    await show(cb, trunc(f"👮 <b>Adminlar</b> ({page + 1}/{pages})\n\n{body}", 4000), ikb(rows))
    await ack(cb)


@admin_r.callback_query(F.data.startswith("adm:ad_i:"))
async def ad_info(cb: CallbackQuery):
    role = await guard(cb, "admins")
    if not role:
        return
    a = APP.admins.items.get(cb.data.rsplit(":", 1)[1])
    if not a:
        return await cb.answer("Admin topilmadi.", show_alert=True)
    rows = []
    if can_manage(role, a["role"]):
        if has_perm(role, "admin_roles"):
            other = ROLE_FULL if a["role"] == ROLE_MOVIE else ROLE_MOVIE
            rows.append([(f"🔁 {ROLE_LABELS[other]} ga o‘tkazish", f"adm:ad_r:{a['user_id']}:{other}")])
        rows.append([("🗑 Adminlikdan olish", f"adm:ad_x:{a['user_id']}")])
    rows.append([("⬅️ Ro‘yxat", "adm:ad_list:0")])
    await show(cb, admin_detail(a), ikb(rows))
    await cb.answer()


@admin_r.callback_query(F.data == "adm:ad_add")
async def ad_add_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "admins", state):
        return
    await state.clear()
    await state.set_state(AdminAdd.uid)
    await show(cb, "➕ <b>Admin qo‘shish</b>\n\nYangi adminning Telegram ID sini yuboring.\n"
                   "ℹ️ U avval botga /start bosgan bo‘lishi kerak.", ikb([CANCEL_ROW]))
    await cb.answer()


def resolve_user(text: str) -> Optional[dict]:
    t = text.strip()
    if re.fullmatch(r"\d{4,15}", t):
        return APP.users.get(int(t))
    if re.fullmatch(r"@?[A-Za-z]\w{3,}", t):
        name = t.lstrip("@").casefold()
        return next((u for u in APP.users.items.values() if (u.get("username") or "").casefold() == name), None)
    return None


@admin_r.message(AdminAdd.uid, F.text)
async def ad_add_uid(message: Message, state: FSMContext):
    role = await guard(message, "admins", state)
    if not role:
        return
    if not re.fullmatch(r"\d{4,15}", message.text.strip()):
        return await message.answer("❗ Faqat raqamlardan iborat Telegram ID yuboring.", reply_markup=ikb([CANCEL_ROW]))
    uid = int(message.text.strip())
    if uid == APP.main_admin_id or APP.admins.role_of(uid):
        return await message.answer("⚠️ Bu foydalanuvchi allaqachon admin.", reply_markup=ikb([CANCEL_ROW]))
    u = APP.users.get(uid)
    if not u:
        return await message.answer("😕 Bu foydalanuvchi botdan foydalanmagan. U avval botga /start bosishi kerak.", reply_markup=ikb([CANCEL_ROW]))
    await state.clear()
    rows = [[(ROLE_LABELS[ROLE_MOVIE], f"adm:ad_a:{uid}:{ROLE_MOVIE}")]]
    if has_perm(role, "admin_manage_full"):
        rows.append([(ROLE_LABELS[ROLE_FULL], f"adm:ad_a:{uid}:{ROLE_FULL}")])
    rows.append(CANCEL_ROW)
    await message.answer(f"👤 {esc(u['first_name'])} (<code>{uid}</code>)\n\nAdmin turini tanlang:", reply_markup=ikb(rows))


@admin_r.message(AdminAdd.uid)
async def ad_add_bad(message: Message):
    await message.answer("❗ Telegram ID ni raqam ko‘rinishida yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.callback_query(F.data.startswith("adm:ad_a:"))
async def ad_add_role(cb: CallbackQuery, bot: Bot):
    role = await guard(cb, "admins")
    if not role:
        return
    _, _, uid_s, new_role = cb.data.split(":")
    uid = int(uid_s)
    if new_role not in (ROLE_MOVIE, ROLE_FULL) or not can_manage(role, new_role):
        return await cb.answer("⛔ Bu turdagi adminni qo‘shishga ruxsatingiz yo‘q.", show_alert=True)
    u = APP.users.get(uid)
    if not u or uid == APP.main_admin_id or APP.admins.role_of(uid):
        return await cb.answer("Foydalanuvchi topilmadi yoki allaqachon admin.", show_alert=True)
    APP.admins.add(uid, new_role, cb.from_user.id, u["first_name"])
    await APP.save("admins")
    APP.stats.log_admin(cb.from_user.id, "admin_add", f"{uid} {new_role}")
    await show(cb, f"✅ {esc(u['first_name'])} — {ROLE_LABELS[new_role]} etib tayinlandi.", ikb([[("⬅️ Adminlar", "adm:ad_list:0")]]))
    await cb.answer()
    await notify_user(bot, uid, f"🎉 Siz botda <b>{ROLE_LABELS[new_role]}</b> etib tayinlandingiz.\n🛠 Admin panel tugmasi menyuda paydo bo‘ldi.", main_kb(True))


@admin_r.callback_query(F.data.startswith("adm:ad_r:"))
async def ad_change_role(cb: CallbackQuery):
    role = await guard(cb, "admin_roles")
    if not role:
        return
    _, _, uid_s, new_role = cb.data.split(":")
    a = APP.admins.items.get(uid_s)
    if not a or a["role"] == ROLE_MAIN or int(uid_s) == APP.main_admin_id or new_role not in (ROLE_MOVIE, ROLE_FULL):
        return await cb.answer("Admin topilmadi yoki rolini o‘zgartirib bo‘lmaydi.", show_alert=True)
    a["role"] = new_role
    await APP.save("admins")
    APP.stats.log_admin(cb.from_user.id, "admin_role_change", f"{uid_s} {new_role}")
    await show(cb, admin_detail(a), ikb([[("⬅️ Ro‘yxat", "adm:ad_list:0")]]))
    await cb.answer("Rol o‘zgartirildi.")


@admin_r.callback_query(F.data.startswith("adm:ad_x:"))
async def ad_del_ask(cb: CallbackQuery):
    role = await guard(cb, "admins")
    if not role:
        return
    uid_s = cb.data.rsplit(":", 1)[1]
    a = APP.admins.items.get(uid_s)
    if not a or not can_manage(role, a["role"]) or int(uid_s) == APP.main_admin_id:
        return await cb.answer("⛔ Bu adminni olib tashlashga ruxsatingiz yo‘q.", show_alert=True)
    await show(cb, f"❓ <b>{esc(a['first_name'])}</b> ni adminlikdan olishni tasdiqlaysizmi?",
               ikb([[("✅ Ha", f"adm:ad_xy:{uid_s}"), ("❌ Yo‘q", f"adm:ad_i:{uid_s}")]]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("adm:ad_xy:"))
async def ad_del_do(cb: CallbackQuery, bot: Bot):
    role = await guard(cb, "admins")
    if not role:
        return
    uid_s = cb.data.rsplit(":", 1)[1]
    a = APP.admins.items.get(uid_s)
    if not a or not can_manage(role, a["role"]) or int(uid_s) == APP.main_admin_id:
        return await cb.answer("⛔ Bu adminni olib tashlashga ruxsatingiz yo‘q.", show_alert=True)
    APP.admins.remove(int(uid_s))
    await APP.save("admins")
    APP.stats.log_admin(cb.from_user.id, "admin_remove", uid_s)
    await show(cb, "✅ Admin olib tashlandi. (Qo‘shgan kinolari saqlanadi.)", ikb([[("⬅️ Adminlar", "adm:ad_list:0")]]))
    await cb.answer()
    await notify_user(bot, int(uid_s), "ℹ️ Sizning adminlik huquqingiz bekor qilindi.", main_kb(False))


# =====================================================================
# 16. VIP BOSHQARUVI
# =====================================================================
@admin_r.callback_query(F.data == "adm:vip")
async def vip_menu(cb: CallbackQuery, state: FSMContext):
    await render_vip_menu(cb, state)


async def render_vip_menu(cb: Message | CallbackQuery, state: Optional[FSMContext] = None):
    role = await guard(cb, "vip", state)
    if not role:
        return
    await state.clear()
    rows = [[("👥 VIP foydalanuvchilar", "adm:vip_list:0")],
            [("➕ VIP berish / uzaytirish", "adm:vip_give")],
            [("❌ VIPni bekor qilish", "adm:vip_cancel")]]
    if has_perm(role, "payments"):
        rows.append([(f"⏳ Kutilayotgan to‘lovlar ({len(APP.payments.pending_list())})", "adm:vip_pend")])
    rows.append([("⬅️ Orqaga", "adm:home")])
    text = (
        "💎 <b>VIP boshqaruvi</b>\n\n"
        f"Faol VIP foydalanuvchilar: <b>{len(APP.vip.active_users())}</b>\n\n"
        "ℹ️ Qoida: foydalanuvchida faol VIP bo‘lsa, yangi muddat <b>eski tugash sanasi ustiga</b> qo‘shiladi; "
        "aks holda hozirgi vaqtdan (UTC) boshlanadi."
    )
    await show(cb, text, ikb(rows))
    await ack(cb)


@admin_r.callback_query(F.data.startswith("adm:vip_list:"))
async def vip_list(cb: CallbackQuery):
    if not await guard(cb, "vip"):
        return
    chunk, page, pages = paginate(APP.vip.active_users(), int(cb.data.rsplit(":", 1)[1]), 15)
    lines = "\n".join(
        f"• {esc(trunc(u['first_name'], 25))} (<code>{u['user_id']}</code>) — {fmt_date(parse_iso(u['vip_until']))}" for u in chunk
    ) or "Hozircha VIP foydalanuvchi yo‘q."
    nav = []
    if page > 0:
        nav.append(("◀️", f"adm:vip_list:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"adm:vip_list:{page + 1}"))
    rows = ([nav] if nav else []) + [[("⬅️ Orqaga", "adm:vip")]]
    await show(cb, trunc(f"👥 <b>VIP foydalanuvchilar</b> ({page + 1}/{pages})\n\n{lines}", 4000), ikb(rows))
    await cb.answer()


@admin_r.callback_query(F.data.in_({"adm:vip_give", "adm:vip_cancel"}))
async def vip_ask_user(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "vip", state):
        return
    await state.clear()
    await state.set_state(VipUser.uid)
    await state.update_data(action="give" if cb.data == "adm:vip_give" else "cancel")
    await show(cb, "🆔 Foydalanuvchining Telegram ID sini (yoki @username) yuboring.", ikb([CANCEL_ROW]))
    await cb.answer()


@admin_r.message(VipUser.uid, F.text)
async def vip_user_input(message: Message, state: FSMContext):
    if not await guard(message, "vip", state):
        return
    u = resolve_user(message.text)
    if not u:
        return await message.answer("😕 Foydalanuvchi topilmadi (u botga /start bosgan bo‘lishi kerak). Qayta yuboring.", reply_markup=ikb([CANCEL_ROW]))
    action = (await state.get_data()).get("action")
    await state.clear()
    vip_dt = APP.users.vip_until(u)
    info = f"👤 {esc(u['first_name'])} (<code>{u['user_id']}</code>)\n💎 VIP: {('faol, ' + fmt_dt(vip_dt)) if vip_dt else 'yo‘q'}\n\n"
    if action == "give":
        rows = [[(f"{p['label']} — {fmt_money(p['price'])}", f"adm:vip_g:{u['user_id']}:{k}")] for k, p in PLANS.items()]
        rows.append(CANCEL_ROW)
        await message.answer(info + "Tarifni tanlang:", reply_markup=ikb(rows))
    else:
        if not vip_dt:
            return await message.answer(info + "Bu foydalanuvchida faol VIP yo‘q.", reply_markup=ikb([[("⬅️ Orqaga", "adm:vip")]]))
        await message.answer(info + "VIPni bekor qilishni tasdiqlaysizmi?",
                             reply_markup=ikb([[("✅ Ha, bekor qilish", f"adm:vip_cx:{u['user_id']}"), ("❌ Yo‘q", "adm:vip")]]))


@admin_r.message(VipUser.uid)
async def vip_user_bad(message: Message):
    await message.answer("❗ Telegram ID yoki @username ni matn ko‘rinishida yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.callback_query(F.data.startswith("adm:vip_g:"))
async def vip_give_do(cb: CallbackQuery, bot: Bot):
    if not await guard(cb, "vip"):
        return
    _, _, uid_s, key = cb.data.split(":")
    u = APP.users.get(int(uid_s))
    if not u or key not in PLANS:
        return await cb.answer("Foydalanuvchi yoki tarif topilmadi.", show_alert=True)
    was_vip = APP.users.vip_until(u) is not None
    start, end = APP.vip.grant(u["user_id"], key, "extend" if was_vip else "manual", cb.from_user.id)
    await APP.save("users", "vip", "settings")
    APP.stats.log_admin(cb.from_user.id, "vip_extend" if was_vip else "vip_give", f"{uid_s} {key}")
    await show(cb, f"✅ {esc(u['first_name'])} uchun VIP {'uzaytirildi' if was_vip else 'berildi'}.\n"
                   f"Tarif: {PLANS[key]['label']}\nBoshlanish: {fmt_dt(start)}\nTugash: <b>{fmt_dt(end)}</b>",
               ikb([[("⬅️ VIP boshqaruvi", "adm:vip")]]))
    await cb.answer()
    await notify_user(bot, u["user_id"], f"🎁 Sizga admin tomonidan VIP {'uzaytirildi' if was_vip else 'berildi'}!\n\n"
                                          f"💎 Tarif: {PLANS[key]['label']}\n📅 Tugash sanasi: {fmt_dt(end)}")


@admin_r.callback_query(F.data.startswith("adm:vip_cx:"))
async def vip_cancel_do(cb: CallbackQuery, bot: Bot):
    if not await guard(cb, "vip"):
        return
    uid = int(cb.data.rsplit(":", 1)[1])
    if not APP.vip.cancel(uid, cb.from_user.id):
        return await cb.answer("Faol VIP topilmadi.", show_alert=True)
    await APP.save("users", "vip")
    APP.stats.log_admin(cb.from_user.id, "vip_cancel", str(uid))
    await show(cb, "✅ VIP bekor qilindi.", ikb([[("⬅️ VIP boshqaruvi", "adm:vip")]]))
    await cb.answer()
    await notify_user(bot, uid, "ℹ️ VIP obunangiz admin tomonidan bekor qilindi.")


@admin_r.callback_query(F.data == "adm:vip_pend")
async def vip_pending(cb: CallbackQuery, bot: Bot):
    if not await guard(cb, "payments"):
        return
    pend = APP.payments.pending_list()
    await cb.answer()
    if not pend:
        return await show(cb, "⏳ Kutilayotgan to‘lovlar yo‘q.", ikb([[("⬅️ VIP boshqaruvi", "adm:vip")]]))
    await show(cb, f"⏳ Kutilayotgan to‘lovlar: <b>{len(pend)}</b>. Quyida yuborilmoqda…", ikb([[("⬅️ VIP boshqaruvi", "adm:vip")]]))
    for p in pend[:10]:
        try:
            sent = await tg_retry(lambda p=p: bot.send_photo(cb.from_user.id, p["receipt_file_id"], caption=payment_caption(p), reply_markup=review_kb(p["payment_id"])))
            p["admin_chat_id"], p["admin_message_id"] = sent.chat.id, sent.message_id
        except TelegramAPIError as e:
            logger.error("Kutilayotgan to‘lovni yuborib bo‘lmadi: %s", e)
    await APP.save("payments")


# =====================================================================
# 17. BACKUP VA TIKLASH (faqat asosiy admin)
# =====================================================================
@admin_r.callback_query(F.data == "adm:bk")
async def bk_menu(cb: CallbackQuery, state: FSMContext):
    await render_bk_menu(cb, state)


async def render_bk_menu(cb: Message | CallbackQuery, state: Optional[FSMContext] = None):
    if not await guard(cb, "backup", state):
        return
    await state.clear()
    kb = ikb([[("💾 Backup olish", "adm:bk_do")], [("♻️ Backupdan tiklash", "adm:bk_rs")], [("⬅️ Orqaga", "adm:home")]])
    await show(cb, "💾 <b>Backup</b>\n\nBarcha JSON fayllar ZIP arxivga yig‘ilib, faqat sizga yuboriladi.\n"
                   "Tiklashdan oldin joriy holatning backupi avtomatik yuboriladi.", kb)
    await ack(cb)


async def send_backup(bot: Bot, chat_id: int, caption: str) -> None:
    snap = APP.db.snapshot()  # await yo'q => izchil nusxa
    raw = await asyncio.to_thread(APP.backup.build_zip, snap)
    await tg_retry(lambda: bot.send_document(chat_id, BufferedInputFile(raw, filename=APP.backup.filename()), caption=caption))


@admin_r.callback_query(F.data == "adm:bk_do")
async def bk_do(cb: CallbackQuery, bot: Bot):
    if not await guard(cb, "backup"):
        return
    await cb.answer("Tayyorlanmoqda…")
    try:
        await send_backup(bot, cb.from_user.id, f"💾 Backup — {fmt_dt(utcnow())}")
    except TelegramAPIError as e:
        logger.error("Backup yuborilmadi: %s", e)
        return await cb.message.answer("⚠️ Backupni yuborib bo‘lmadi (fayl juda katta bo‘lishi mumkin).")
    APP.stats.log_admin(cb.from_user.id, "backup", "")


@admin_r.callback_query(F.data == "adm:bk_rs")
async def bk_restore_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "restore", state):
        return
    if APP.broadcast.is_running():
        return await cb.answer("Broadcast davom etmoqda. Tugagach tiklang.", show_alert=True)
    await state.clear()
    await state.set_state(RestoreSt.zipfile)
    await show(cb, "♻️ Backup ZIP faylini yuboring (bot yaratgan arxiv).\n\n⚠️ Joriy ma'lumotlar almashtiriladi.", ikb([CANCEL_ROW]))
    await cb.answer()


@admin_r.message(RestoreSt.zipfile, F.document)
async def bk_restore_file(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "restore", state):
        return
    doc = message.document
    if (doc.file_size or 0) > 20 * 1024 * 1024:
        return await message.answer("❗ Fayl 20 MB dan katta — Telegram bot orqali yuklab bo‘lmaydi.", reply_markup=ikb([CANCEL_ROW]))
    buf = io.BytesIO()
    try:
        await bot.download(doc, destination=buf)
        texts = await asyncio.to_thread(APP.backup.parse_zip, buf.getvalue())
    except (ValueError, UnicodeDecodeError, zipfile.BadZipFile) as e:
        return await message.answer(f"❗ Arxiv yaroqsiz: {esc(e)}", reply_markup=ikb([CANCEL_ROW]))
    except TelegramAPIError:
        return await message.answer("❗ Faylni yuklab bo‘lmadi. Qayta urinib ko‘ring.", reply_markup=ikb([CANCEL_ROW]))
    APP.restore_buffer[message.from_user.id] = texts
    await state.clear()
    await message.answer(
        f"✅ Arxiv tekshirildi. Fayllar: {', '.join(sorted(texts))}\n\n"
        "Tiklashni tasdiqlaysizmi? Avval joriy holat backupi sizga yuboriladi.",
        reply_markup=ikb([[("✅ Tiklash", "adm:rs_ok"), ("❌ Bekor qilish", "adm:rs_no")]]))


@admin_r.message(RestoreSt.zipfile)
async def bk_restore_bad(message: Message):
    await message.answer("❗ ZIP faylni <b>hujjat</b> sifatida yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.callback_query(F.data.in_({"adm:rs_ok", "adm:rs_no"}))
async def bk_restore_confirm(cb: CallbackQuery, bot: Bot):
    if not await guard(cb, "restore"):
        return
    texts = APP.restore_buffer.pop(cb.from_user.id, None)
    if cb.data == "adm:rs_no" or not texts:
        await show(cb, "❌ Tiklash bekor qilindi." if texts else "⚠️ Tiklash ma'lumoti topilmadi.", ikb([[("⬅️ Backup", "adm:bk")]]))
        return await cb.answer()
    if APP.broadcast.is_running():
        return await cb.answer("Broadcast davom etmoqda. Tugagach tiklang.", show_alert=True)
    await cb.answer("Bajarilmoqda…")
    try:
        await send_backup(bot, cb.from_user.id, "💾 Tiklashdan oldingi joriy holat backupi")
    except TelegramAPIError as e:
        logger.error("Pre-restore backup yuborilmadi: %s", e)
        return await show(cb, "⚠️ Joriy holat backupini yuborib bo‘lmadi — xavfsizlik uchun tiklash to‘xtatildi.", ikb([[("⬅️ Backup", "adm:bk")]]))
    APP.backup.restore(texts)
    await APP.save(*JsonDB.FILES)
    APP.stats.log_admin(cb.from_user.id, "restore", ",".join(sorted(texts)))
    await show(cb, "✅ Ma'lumotlar tiklandi.", ikb([[("⬅️ Admin panel", "adm:home")]]))


# =====================================================================
# 18. BOT SOZLAMALARI (faqat asosiy admin)
# =====================================================================
SETTING_FIELDS = {
    "bot_name": "Bot nomi",
    "contact_username": "Admin username (/start xabarida)",
    "card_holder": "Karta egasi (ism familiya)",
    "card_number": "Karta raqami",
    "payment_note": "To‘lov uchun izoh",
}


def settings_text() -> str:
    s = APP.settings
    return (
        "⚙️ <b>Bot sozlamalari</b>\n\n"
        f"🤖 Bot nomi: <b>{esc(s.get('bot_name') or '—')}</b>\n"
        f"📞 Admin: {esc(s.get('contact_username') or '—')}\n"
        f"👤 Karta egasi: {esc(s.get('card_holder') or '—')}\n"
        f"💳 Karta raqami: <code>{esc(s.get('card_number') or '—')}</code>\n"
        f"📝 To‘lov izohi: {esc(s.get('payment_note') or '—')}\n"
        f"📢 Majburiy obuna: {'🟢 yoqilgan' if s.get('subscription_enabled', True) else '🔴 o‘chirilgan'}"
    )


@admin_r.callback_query(F.data == "adm:set")
async def set_menu(cb: CallbackQuery, state: FSMContext):
    await render_set_menu(cb, state)


async def render_set_menu(cb: Message | CallbackQuery, state: Optional[FSMContext] = None):
    if not await guard(cb, "settings", state):
        return
    await state.clear()
    rows = [[(f"✏️ {label}", f"adm:set_e:{k}")] for k, label in SETTING_FIELDS.items()]
    rows.append([("⬅️ Orqaga", "adm:home")])
    await show(cb, settings_text(), ikb(rows))
    await ack(cb)


@admin_r.callback_query(F.data.startswith("adm:set_e:"))
async def set_edit_start(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "settings", state):
        return
    key = cb.data.rsplit(":", 1)[1]
    if key not in SETTING_FIELDS:
        return await cb.answer()
    await state.set_state(SettingEdit.value)
    await state.update_data(key=key)
    hint = "\n\nTozalash uchun «-» yuboring." if key == "payment_note" else ""
    await show(cb, f"✏️ <b>{SETTING_FIELDS[key]}</b> uchun yangi qiymatni yuboring.{hint}", ikb([CANCEL_ROW]))
    await cb.answer()


@admin_r.message(SettingEdit.value, F.text)
async def set_edit_value(message: Message, state: FSMContext):
    if not await guard(message, "settings", state):
        return
    key = (await state.get_data()).get("key")
    raw = " ".join(message.text.split())
    err, val = None, raw
    if key == "bot_name":
        if not 1 <= len(raw) <= 40:
            err = "Bot nomi 1–40 belgi bo‘lishi kerak."
    elif key == "contact_username":
        if not re.fullmatch(r"@?[A-Za-z][\w]{4,31}", raw):
            err = "Username noto‘g‘ri. Masalan: @senzaifsan"
        val = "@" + raw.lstrip("@")
    elif key == "card_holder":
        if not 3 <= len(raw) <= 60:
            err = "Karta egasi ismi 3–60 belgi bo‘lishi kerak."
    elif key == "card_number":
        digits = re.sub(r"[\s-]", "", raw)
        if not digits.isdigit() or not 12 <= len(digits) <= 19:
            err = "Karta raqami 12–19 ta raqamdan iborat bo‘lishi kerak."
        val = " ".join(digits[i:i + 4] for i in range(0, len(digits), 4))
    elif key == "payment_note":
        if raw == "-":
            val = ""
        elif len(raw) > 200:
            err = "Izoh 200 belgidan oshmasin."
    else:
        err = "Noma'lum sozlama."
    if err:
        return await message.answer(f"❗ {err}", reply_markup=ikb([CANCEL_ROW]))
    APP.settings[key] = val
    await APP.save("settings")
    APP.stats.log_admin(message.from_user.id, "setting_change", key)  # qiymat logga yozilmaydi
    await state.clear()
    await message.answer("✅ Saqlandi.\n\n" + settings_text(), reply_markup=ikb([[("⬅️ Sozlamalar", "adm:set")]]))


@admin_r.message(SettingEdit.value)
async def set_edit_bad(message: Message):
    await message.answer("❗ Matn yuboring.", reply_markup=ikb([CANCEL_ROW]))


# =====================================================================
# 19. BROADCAST (faqat asosiy admin)
# =====================================================================
def progress_text(job: dict) -> str:
    return (
        f"📣 <b>Broadcast {job['job_id']}</b> — yuborilmoqda…\n\n"
        f"Jarayon: <b>{job['index']}/{job['total']}</b>\n"
        f"✅ Yuborildi: {job['sent']}\n🚫 Bloklagan: {job['blocked']}\n⚠️ Boshqa xato: {job['other_errors']}"
    )


@admin_r.callback_query(F.data == "adm:bc")
async def bc_menu(cb: CallbackQuery, state: FSMContext):
    await render_bc_menu(cb, state)


async def render_bc_menu(cb: Message | CallbackQuery, state: Optional[FSMContext] = None):
    if not await guard(cb, "broadcast", state):
        return
    await state.clear()
    cur = APP.broadcast.current
    if APP.broadcast.is_running():
        text = progress_text(cur)
        rows = [[("🔄 Yangilash", "adm:bc"), ("⛔ To‘xtatish", "adm:bc_stop")], [("⬅️ Orqaga", "adm:home")]]
    else:
        text = "📣 <b>Broadcast</b>\n\nBarcha bot foydalanuvchilariga ommaviy xabar yuborish."
        if cur:
            text += "\n\n<b>Oxirgi broadcast natijasi:</b>\n" + BroadcastManager.report_text(cur)
        rows = [[("✉️ Yangi broadcast", "adm:bc_new")], [("⬅️ Orqaga", "adm:home")]]
    await show(cb, text, ikb(rows))
    await ack(cb)


@admin_r.callback_query(F.data == "adm:bc_stop")
async def bc_stop(cb: CallbackQuery):
    if not await guard(cb, "broadcast"):
        return
    if APP.broadcast.is_running():
        APP.broadcast.current["status"] = "cancelled"
        APP.stats.log_admin(cb.from_user.id, "broadcast_stop", APP.broadcast.current["job_id"])
        await cb.answer("To‘xtatilmoqda…")
    else:
        await cb.answer("Faol broadcast yo‘q.", show_alert=True)


@admin_r.callback_query(F.data == "adm:bc_new")
async def bc_new(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "broadcast", state):
        return
    if APP.broadcast.is_running():
        return await cb.answer("Broadcast allaqachon davom etmoqda.", show_alert=True)
    await state.clear()
    await state.set_state(BroadcastSt.content)
    await show(cb, "📣 Yuboriladigan xabarni yuboring:\n\n• oddiy matn\n• rasm + caption\n• video + caption", ikb([CANCEL_ROW]))
    await cb.answer()


async def _bc_preview(message: Message, state: FSMContext, bot: Bot, ctype: str, text_html: Optional[str], file_id: Optional[str]) -> None:
    job_like = {"content_type": ctype, "text_html": text_html, "file_id": file_id}
    try:
        await BroadcastManager._deliver(bot, job_like, message.from_user.id)
    except TelegramAPIError as e:
        return await message.answer(f"❗ Xabarni ko‘rsatib bo‘lmadi: {esc(str(e)[:200])}\nQaytadan yuboring.", reply_markup=ikb([CANCEL_ROW]))
    await state.update_data(ctype=ctype, text_html=text_html, file_id=file_id)
    await state.set_state(BroadcastSt.confirm)
    await message.answer(
        f"👆 Yuqorida xabar ko‘rinishi (preview).\n\n👥 Qabul qiluvchilar: <b>{len(APP.users.items)}</b> ta foydalanuvchi.\nYuborishni tasdiqlaysizmi?",
        reply_markup=ikb([[("✅ Tasdiqlash", "adm:bc_ok"), ("❌ Bekor qilish", "adm:cancel")]]))


@admin_r.message(BroadcastSt.content, F.text)
async def bc_text(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "broadcast", state):
        return
    if len(message.text) > 4000:
        return await message.answer("❗ Matn juda uzun (4000 belgigacha).", reply_markup=ikb([CANCEL_ROW]))
    await _bc_preview(message, state, bot, "text", message.html_text, None)


@admin_r.message(BroadcastSt.content, F.photo)
async def bc_photo(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "broadcast", state):
        return
    if len(message.caption or "") > 1000:
        return await message.answer("❗ Caption juda uzun (1000 belgigacha).", reply_markup=ikb([CANCEL_ROW]))
    await _bc_preview(message, state, bot, "photo", message.html_text if message.caption else None, message.photo[-1].file_id)


@admin_r.message(BroadcastSt.content, F.video)
async def bc_video(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "broadcast", state):
        return
    if len(message.caption or "") > 1000:
        return await message.answer("❗ Caption juda uzun (1000 belgigacha).", reply_markup=ikb([CANCEL_ROW]))
    await _bc_preview(message, state, bot, "video", message.html_text if message.caption else None, message.video.file_id)


@admin_r.message(BroadcastSt.content)
async def bc_bad(message: Message):
    await message.answer("❗ Faqat matn, rasm (caption bilan) yoki video (caption bilan) yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.callback_query(BroadcastSt.confirm, F.data == "adm:bc_ok")
async def bc_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not await guard(cb, "broadcast", state):
        return
    if APP.broadcast.is_running():
        await state.clear()
        return await cb.answer("Broadcast allaqachon davom etmoqda.", show_alert=True)
    d = await state.get_data()
    await state.clear()
    if not d.get("ctype"):
        return await cb.answer("Xabar topilmadi. Qaytadan boshlang.", show_alert=True)
    job = APP.broadcast.create_job(cb.from_user.id, d["ctype"], d.get("text_html"), d.get("file_id"))
    await APP.save("broadcast", "settings")
    APP.stats.log_admin(cb.from_user.id, "broadcast_start", f"{job['job_id']} total={job['total']}")
    APP.broadcast_task = asyncio.create_task(run_broadcast_task(bot))
    await show(cb, f"🚀 Broadcast <b>{job['job_id']}</b> boshlandi. Natija tayyor bo‘lgach hisobot yuboriladi.", ikb([[("📣 Holat", "adm:bc")]]))
    await cb.answer()


async def run_broadcast_task(bot: Bot, resumed: bool = False) -> None:
    """Alohida asyncio task: bot boshqa so'rovlarga javob berishda davom etadi."""
    job = APP.broadcast.current
    if not job:
        return
    admin_id = job["created_by"]
    status_msg = None
    with contextlib.suppress(TelegramAPIError):
        head = "♻️ Bot qayta ishga tushgani uchun broadcast davom ettirildi.\n\n" if resumed else ""
        status_msg = await bot.send_message(admin_id, head + progress_text(job))

    async def progress(j: dict) -> None:
        if status_msg:
            with contextlib.suppress(TelegramAPIError):
                await status_msg.edit_text(progress_text(j))

    try:
        await APP.broadcast.run(bot, progress_cb=progress)
    except Exception:  # noqa: BLE001
        logger.exception("Broadcast kutilmagan xato bilan to‘xtadi")
        job["status"] = "cancelled"
        job["finished_at"] = iso(utcnow())
        await APP.save("broadcast")
    with contextlib.suppress(TelegramAPIError):
        await tg_retry(lambda: bot.send_message(admin_id, BroadcastManager.report_text(job)))


# =====================================================================
# 19b. KANALGA POST JOYLASH (media + matn + pastki inline tugma — bitta post)
# =====================================================================
BUTTON_TITLE_MAX = 64
POST_CAPTION_MAX = 1024
_HOST_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")


def validate_button_url(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Xavfsiz va yaroqli http/https URL. Qaytaradi: (url, xato_xabari)."""
    url = raw.strip()
    if not url:
        return None, "URL bo‘sh."
    if len(url) > 512:
        return None, "URL juda uzun (512 belgigacha)."
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in url):
        return None, "URL ichida bo‘sh joy yoki noto‘g‘ri belgilar bo‘lmasligi kerak."
    try:
        parsed = urlparse(url)
        port = parsed.port
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None, "URL formati noto‘g‘ri."
    if parsed.scheme.lower() not in ("http", "https"):
        return None, "Faqat http:// yoki https:// bilan boshlanadigan havola qabul qilinadi."
    if not host or not parsed.netloc:
        return None, "URL da domen nomi ko‘rsatilmagan."
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return None, "URL ichida login yoki parol bo‘lishi mumkin emas."
    if port is not None and not 1 <= port <= 65535:
        return None, "URL dagi port noto‘g‘ri."
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(labels) < 2 or not all(_HOST_LABEL_RE.match(x) for x in labels) or not _TLD_RE.match(labels[-1]):
            return None, "Domen nomi yaroqsiz (masalan: example.com). Lotin harflaridagi domen kiriting."
    else:
        if not ip.is_global:
            return None, "Ichki yoki maxsus IP manzillar qabul qilinmaydi."
    return url, None


def post_markup(post: dict) -> InlineKeyboardMarkup:
    b = post["button"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b["title"], url=b["url"])]])


async def send_post_to(bot: Bot, chat_id: int, post: dict) -> Message:
    """Media + caption + inline tugma BITTA xabar sifatida."""
    kb = post_markup(post)
    if post["media_type"] == "photo":
        return await bot.send_photo(chat_id, post["file_id"], caption=post["text_html"], reply_markup=kb)
    return await bot.send_video(chat_id, post["file_id"], caption=post["text_html"], reply_markup=kb)


def explain_post_error(e: Exception) -> str:
    """Telegram xatosini admin uchun tushunarli o'zbekcha izohga aylantiradi."""
    raw = str(getattr(e, "message", None) or e)
    low = raw.lower()
    if isinstance(e, TelegramForbiddenError) or any(k in low for k in (
            "not enough rights", "need administrator rights", "chat_write_forbidden", "bot was kicked", "have no rights")):
        why = "Botda bu kanalga post yuborish huquqi yo‘q yoki bot kanaldan chiqarilgan. Botni administrator qilib, «Xabarlarni joylash» huquqini bering."
    elif "chat not found" in low:
        why = "Kanal topilmadi. Chat ID to‘g‘riligini va bot kanalga qo‘shilganini tekshiring."
    elif "button_url_invalid" in low:
        why = "Telegram tugma URL manzilini qabul qilmadi. Boshqa URL kiriting."
    elif "wrong file identifier" in low or "wrong remote file" in low or "failed to get http url content" in low:
        why = "Media fayl yaroqsiz yoki eskirgan. Postni qaytadan yarating."
    elif "caption is too long" in low:
        why = "Post matni juda uzun (1024 belgigacha bo‘lishi kerak)."
    elif isinstance(e, (TelegramNetworkError, TelegramServerError)):
        why = "Telegram bilan aloqada xatolik."
    else:
        why = "Telegram postni qabul qilmadi."
    return f"{why}\n\n<i>Telegram javobi: {esc(trunc(raw, 200))}</i>"


async def check_can_post(bot: Bot, ref: int | str) -> tuple[Optional[dict], Optional[str]]:
    """Bot kanalda administrator ekanligini va post yuborish huquqini tekshiradi."""
    try:
        chat = await tg_retry(lambda: bot.get_chat(ref))
    except TelegramAPIError:
        return None, "Kanal/guruh topilmadi yoki bot unga qo‘shilmagan. Avval botni kanalga administrator qilib qo‘shing."
    if chat.type == ChatType.PRIVATE:
        return None, "Bu kanal yoki guruh emas."
    try:
        me = await tg_retry(lambda: bot.get_chat_member(chat.id, bot.id))
    except TelegramAPIError:
        return None, "Botning kanaldagi huquqlarini aniqlab bo‘lmadi. Botni administrator qiling."
    if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return None, "Bot bu kanalda administrator emas. Botni administrator qilib, «Xabarlarni joylash» huquqini bering."
    if chat.type == ChatType.CHANNEL and me.status == ChatMemberStatus.ADMINISTRATOR and not getattr(me, "can_post_messages", False):
        return None, "Botda bu kanalga post yuborish huquqi yo‘q. Administrator huquqlarida «Xabarlarni joylash» (Post messages) ni yoqing."
    return {"id": chat.id, "title": chat.title or str(chat.id), "username": chat.username, "type": getattr(chat.type, "value", str(chat.type))}, None


def post_link(post: dict) -> Optional[str]:
    mid = post.get("message_id")
    if not mid:
        return None
    if post.get("channel_username"):
        return f"https://t.me/{post['channel_username']}/{mid}"
    cid = str(post["channel_id"])
    return f"https://t.me/c/{cid[4:]}/{mid}" if cid.startswith("-100") else None


async def render_post_menu(target: Message | CallbackQuery, state: Optional[FSMContext] = None):
    """1/4 — kanalni tanlash. Har safar yangi jarayon: eski ma'lumotlar tozalanadi."""
    if not await guard(target, "channel_post", state):
        return
    if state is not None:
        await state.clear()
        await state.set_state(PostSt.channel)
    chans = list(APP.subs.channels.values())[:20]
    rows = [[(trunc(f"{'🔒' if c['channel_type'] == 'private' else '📢'} {c['title']}", 60), f"adm:pst_ch:{c['channel_id']}")] for c in chans]
    rows.append(CANCEL_ROW)
    text = (
        "📢 <b>Kanalga post joylash</b>\n\n"
        "1/4 — Post yuboriladigan kanalni tanlang yoki kanal <b>chat ID</b> (yoki @username) ini yuboring.\n\n"
        "ℹ️ Bot kanalda administrator bo‘lishi va «Xabarlarni joylash» huquqiga ega bo‘lishi kerak."
    )
    if not chans:
        text += "\n\nHozircha botga qo‘shilgan kanallar yo‘q — chat ID yuboring."
    await show(target, text, ikb(rows))
    await ack(target)


async def _post_select_channel(target: Message | CallbackQuery, state: FSMContext, bot: Bot, ref: int | str) -> None:
    chat, err = await check_can_post(bot, ref)
    if err:
        return await show(target, f"❗ {err}\n\nBoshqa kanalni tanlang yoki chat ID yuboring.", ikb([CANCEL_ROW]))
    await state.update_data(channel=chat)
    await state.set_state(PostSt.media)
    await show(target, f"✅ Kanal: <b>{esc(chat['title'])}</b>\n\n2/4 — Postga <b>rasm</b> yoki <b>video</b> yuboring (faqat bitta).", ikb([CANCEL_ROW]))


@admin_r.callback_query(PostSt.channel, F.data.startswith("adm:pst_ch:"))
async def post_pick_channel(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not await guard(cb, "channel_post", state):
        return
    c = APP.subs.channels.get(cb.data.rsplit(":", 1)[1])
    if not c:
        return await cb.answer("Kanal topilmadi.", show_alert=True)
    await cb.answer()
    await _post_select_channel(cb, state, bot, c["channel_id"])


@admin_r.message(PostSt.channel, F.text)
async def post_channel_ref(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "channel_post", state):
        return
    ref = parse_chat_ref(message.text)
    if ref is None:
        return await message.answer("❗ Format noto‘g‘ri. Chat ID (masalan -1001234567890) yoki @username yuboring.", reply_markup=ikb([CANCEL_ROW]))
    await _post_select_channel(message, state, bot, ref)


@admin_r.message(PostSt.media, F.photo | F.video)
async def post_media(message: Message, state: FSMContext):
    if not await guard(message, "channel_post", state):
        return
    gid = message.media_group_id
    if gid:  # albom: bitta media qabul qilinadi, albom emas
        if (await state.get_data()).get("album_gid") != gid:
            await state.update_data(album_gid=gid)
            await message.answer("❗ Faqat <b>bitta</b> rasm yoki video yuboring (albom qabul qilinmaydi).", reply_markup=ikb([CANCEL_ROW]))
        return
    if message.photo:
        mtype, fid = "photo", message.photo[-1].file_id
    else:
        mtype, fid = "video", message.video.file_id
    await state.update_data(media_type=mtype, file_id=fid)
    await state.set_state(PostSt.text)
    await message.answer(
        f"✅ {'Rasm' if mtype == 'photo' else 'Video'} qabul qilindi.\n\n3/4 — Post matnini yuboring "
        f"(rasm/video ostida chiqadi, {POST_CAPTION_MAX} belgigacha).", reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(PostSt.media)
async def post_media_bad(message: Message):
    await message.answer("❗ Faqat <b>bitta rasm</b> yoki <b>video</b> yuboring. Matn, stiker, fayl va boshqa formatlar qabul qilinmaydi.", reply_markup=ikb([CANCEL_ROW]))


def post_kind_kb() -> InlineKeyboardMarkup:
    return ikb([[("🔗 Oddiy URL tugmasi", "adm:pst_k:url")], [("🎬 Kino havolasi (kino kodi)", "adm:pst_k:movie")], CANCEL_ROW])


@admin_r.message(PostSt.text, F.text)
async def post_text(message: Message, state: FSMContext):
    if not await guard(message, "channel_post", state):
        return
    plain = message.text.strip()
    if not plain:
        return await message.answer("❗ Matn bo‘sh bo‘lmasligi kerak.", reply_markup=ikb([CANCEL_ROW]))
    if len(plain) > POST_CAPTION_MAX:
        return await message.answer(f"❗ Matn juda uzun ({len(plain)} belgi). Rasm/video ostidagi matn {POST_CAPTION_MAX} belgidan oshmasligi kerak.", reply_markup=ikb([CANCEL_ROW]))
    await state.update_data(text_html=message.html_text)
    await state.set_state(PostSt.btn_kind)
    await message.answer("✅ Matn saqlandi.\n\n4/4 — Postning pastki tugmasi uchun qanday havola kerak?", reply_markup=post_kind_kb())


@admin_r.callback_query(PostSt.btn_kind, F.data.in_({"adm:pst_k:url", "adm:pst_k:movie"}))
async def post_pick_kind(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "channel_post", state):
        return
    if cb.data.endswith(":url"):
        await state.set_state(PostSt.url_title)
        await show(cb, "🔗 <b>Oddiy URL tugmasi</b>\n\nTugma nomini (title) yuboring.\nMasalan: <code>Batafsil ko‘rish</code>", ikb([CANCEL_ROW]))
    else:
        await state.set_state(PostSt.movie_code)
        await show(cb, "🎬 <b>Kino havolasi</b>\n\nTugma ochadigan kinoning <b>kodini</b> yuboring.\nMasalan: <code>11</code>", ikb([CANCEL_ROW]))
    await cb.answer()


def _clean_button_title(text: str) -> Optional[str]:
    t = " ".join(text.split())
    return t if 1 <= len(t) <= BUTTON_TITLE_MAX else None


@admin_r.message(PostSt.url_title, F.text)
async def post_url_title(message: Message, state: FSMContext):
    if not await guard(message, "channel_post", state):
        return
    title = _clean_button_title(message.text)
    if not title:
        return await message.answer(f"❗ Tugma nomi 1–{BUTTON_TITLE_MAX} belgidan iborat bo‘lishi kerak.", reply_markup=ikb([CANCEL_ROW]))
    await state.update_data(button={"kind": "url", "title": title})
    await state.set_state(PostSt.url_value)
    await message.answer("✅ Tugma nomi saqlandi.\n\nEndi tugma URL manzilini yuboring.\nMasalan: <code>https://example.com</code>", reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(PostSt.url_value, F.text)
async def post_url_value(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "channel_post", state):
        return
    url, err = validate_button_url(message.text)
    if err:
        return await message.answer(f"❗ {err}\n\nURL ni qaytadan yuboring.", reply_markup=ikb([CANCEL_ROW]))
    data = await state.get_data()
    await state.update_data(button={**data["button"], "url": url})
    await post_make_preview(message, state, bot)


@admin_r.message(PostSt.movie_code, F.text)
async def post_movie_code(message: Message, state: FSMContext):
    if not await guard(message, "channel_post", state):
        return
    movie = APP.movies.by_code(message.text.strip())
    if not movie:
        return await message.answer("❌ Bunday kodli kino topilmadi. Kino kodini to‘g‘ri kiriting va qaytadan yuboring.", reply_markup=ikb([CANCEL_ROW]))
    await state.update_data(button={"kind": "movie", "code": movie["code"]})
    await state.set_state(PostSt.movie_title)
    await message.answer(
        f"✅ Kino topildi: <b>{esc(movie['title'])}</b>\n\nEndi tugma nomini yuboring.\nMasalan: <code>🎬 Kinoni ko‘rish</code>",
        reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(PostSt.movie_title, F.text)
async def post_movie_title(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, "channel_post", state):
        return
    title = _clean_button_title(message.text)
    if not title:
        return await message.answer(f"❗ Tugma nomi 1–{BUTTON_TITLE_MAX} belgidan iborat bo‘lishi kerak.", reply_markup=ikb([CANCEL_ROW]))
    data = await state.get_data()
    movie = APP.movies.by_code(data["button"]["code"])
    if not movie:  # kod kiritilgandan keyin kino o'chirilgan bo'lishi mumkin
        await state.set_state(PostSt.movie_code)
        return await message.answer("❌ Bu kino o‘chirilgan. Boshqa kino kodini yuboring.", reply_markup=ikb([CANCEL_ROW]))
    url = await movie_link(bot, movie["code"])
    if not url:
        return await message.answer("⚠️ Bot usernamesi aniqlanmadi, kino havolasini yaratib bo‘lmadi. .env ga BOT_USERNAME qo‘shing.", reply_markup=ikb([CANCEL_ROW]))
    await state.update_data(button={**data["button"], "title": title, "url": url})
    await post_make_preview(message, state, bot)


@admin_r.message(StateFilter(PostSt.channel, PostSt.text, PostSt.url_title, PostSt.url_value, PostSt.movie_code, PostSt.movie_title))
async def post_text_expected(message: Message):
    await message.answer("❗ Iltimos, <b>matn</b> ko‘rinishida yuboring.", reply_markup=ikb([CANCEL_ROW]))


@admin_r.message(StateFilter(PostSt.btn_kind, PostSt.confirm))
async def post_buttons_expected(message: Message):
    await message.answer("👆 Iltimos, yuqoridagi tugmalardan birini tanlang.")


async def post_make_preview(message: Message, state: FSMContext, bot: Bot) -> None:
    """Preview: post aynan kanalga ketadigan ko'rinishda (media + matn + tugma) adminga yuboriladi."""
    d = await state.get_data()
    draft = {"media_type": d["media_type"], "file_id": d["file_id"], "text_html": d["text_html"], "button": d["button"]}
    try:
        pm = await send_post_to(bot, message.chat.id, draft)
    except TelegramAPIError as e:
        await state.set_state(PostSt.btn_kind)
        return await message.answer(f"❗ Postni ko‘rsatib bo‘lmadi.\n\n{explain_post_error(e)}\n\nTugmani qaytadan tanlang.", reply_markup=post_kind_kb())
    p = APP.posts.create(message.from_user.id, d["channel"], d["media_type"], d["file_id"], d["text_html"], d["button"], pm.message_id)
    await APP.save("channel_posts", "settings")
    await state.set_state(PostSt.confirm)
    await state.set_data({"pid": p["post_id"]})
    await message.answer(
        f"👆 Yuqorida post ko‘rinishi (preview).\n\n📢 Kanal: <b>{esc(p['channel_title'])}</b>\n\nKanalga yuborishni tasdiqlaysizmi?",
        reply_markup=ikb([[("✅ Kanalga yuborish", f"adm:pst_ok:{p['post_id']}"), ("❌ Bekor qilish", f"adm:pst_no:{p['post_id']}")]]))


def _own_post(cb: CallbackQuery) -> Optional[dict]:
    p = APP.posts.get(cb.data.rsplit(":", 1)[1])
    return p if p and p["admin_id"] == cb.from_user.id else None


@admin_r.callback_query(F.data.startswith("adm:pst_ok:"))
async def post_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not await guard(cb, "channel_post", state):
        return
    p = _own_post(cb)
    if not p:
        return await cb.answer("Post topilmadi (eskirgan bo‘lishi mumkin).", show_alert=True)
    # ---- takroriy yuborishdan himoya: holat tekshiruvi va 'sending' belgisi orasida await yo'q
    if p["status"] == "sending":
        return await cb.answer("⏳ Post hozir yuborilmoqda.", show_alert=True)
    if p["status"] == "sent":
        return await cb.answer("✅ Bu post allaqachon kanalga yuborilgan.", show_alert=True)
    if p["status"] == "uncertain":
        return await cb.answer("⚠️ Post holati noma'lum. Dublikat bo‘lmasligi uchun avval kanalni tekshiring, kerak bo‘lsa yangi post yarating.", show_alert=True)
    if p["status"] == "cancelled":
        return await cb.answer("Bu post bekor qilingan.", show_alert=True)
    if APP.posts.is_expired_draft(p):
        return await cb.answer("⌛ Preview eskirgan. Postni qaytadan yarating.", show_alert=True)
    p["status"] = "sending"
    p["attempts"] += 1
    p["error"] = None
    APP.db.touch("channel_posts")
    await cb.answer("Yuborilmoqda…")
    await APP.save("channel_posts")  # 'sending' diskka yoziladi: restart bo'lsa takroriy yuborilmaydi
    try:
        chat, err = await check_can_post(bot, p["channel_id"])
        if err:
            return await _post_failed(cb, p, f"❗ {err}")
        for _ in range(3):
            try:
                sent = await send_post_to(bot, p["channel_id"], p)
                break
            except TelegramRetryAfter as e:  # Telegram rad etgan: xavfsiz qayta urinish
                await asyncio.sleep(e.retry_after + 0.5)
        else:
            return await _post_failed(cb, p, "❗ Telegram so‘rovlarni vaqtincha cheklagan (FloodWait). Birozdan so‘ng qayta urinib ko‘ring.")
    except (TelegramNetworkError, TelegramServerError) as e:
        # So'rov yetib borgan-bormaganligi noma'lum: avtomatik qayta yuborilmaydi (dublikat xavfi)
        p["status"] = "uncertain"
        p["error"] = str(e)[:200]
        await APP.save("channel_posts")
        return await show(cb, "⚠️ Telegram bilan aloqa uzildi — post kanalga yetib borgan-bormaganligi noma'lum.\n\n"
                              "Dublikat bo‘lmasligi uchun <b>kanalni tekshiring</b>. Post yo‘q bo‘lsa, yangi post yarating.",
                          ikb([[("⬅️ Orqaga", "adm:home")]]))
    except TelegramAPIError as e:
        logger.warning("Kanalga post yuborilmadi (%s): %s", p["post_id"], e)
        return await _post_failed(cb, p, f"❗ Post yuborilmadi.\n\n{explain_post_error(e)}")
    p.update(status="sent", message_id=sent.message_id, sent_at=iso(utcnow()), error=None)
    await APP.save("channel_posts")
    APP.stats.log_admin(cb.from_user.id, "channel_post", f"{p['post_id']} -> {p['channel_id']}")
    await state.clear()
    link = post_link(p)
    rows = [[("🔗 Postni ochish", "url", link)]] if link else []
    await show(cb, f"✅ Post kanalga yuborildi!\n\n📢 Kanal: <b>{esc(p['channel_title'])}</b>", ikb(rows) if rows else None)


async def _post_failed(cb: CallbackQuery, p: dict, text: str) -> None:
    p["status"] = "failed"
    p["error"] = re.sub(r"<[^>]+>", "", text)[:300]
    await APP.save("channel_posts")
    await show(cb, text, ikb([[("🔁 Qayta urinish", f"adm:pst_ok:{p['post_id']}"), ("❌ Bekor qilish", f"adm:pst_no:{p['post_id']}")]]))


@admin_r.callback_query(F.data.startswith("adm:pst_no:"))
async def post_cancel(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not await guard(cb, "channel_post", state):
        return
    await state.clear()  # eski media/matn/tugma keyingi postga aralashmaydi
    p = _own_post(cb)
    if p and p["status"] == "sending":
        return await cb.answer("⏳ Post hozir yuborilmoqda — bekor qilib bo‘lmaydi.", show_alert=True)
    if p and p["status"] == "sent":
        return await cb.answer("Post allaqachon yuborilgan — bekor qilib bo‘lmaydi.", show_alert=True)
    if p and p["status"] in ("draft", "failed"):
        p["status"] = "cancelled"
        await APP.save("channel_posts")
        if p.get("preview_message_id"):
            with contextlib.suppress(TelegramAPIError):
                await bot.delete_message(cb.from_user.id, p["preview_message_id"])
    await show(cb, "❌ Post yaratish bekor qilindi.")
    await cb.answer()


# =====================================================================
# 20. ESKIRGAN / RUXSATSIZ ADMIN CALLBACKLARI (admin_r dagi oxirgi handler)
# =====================================================================
ADMIN_SECTIONS: dict[str, tuple[str, Callable[..., Awaitable[Any]]]] = {
    ADM_BTN_MOVIES: ("movie_menu", render_mv_menu),
    ADM_BTN_CHANNELS: ("channels", render_ch_menu),
    ADM_BTN_STATS: ("stats", render_st_menu),
    ADM_BTN_ADMINS: ("admins", render_ad_list),
    ADM_BTN_VIP: ("vip", render_vip_menu),
    ADM_BTN_POST: ("channel_post", render_post_menu),
    ADM_BTN_BACKUP: ("backup", render_bk_menu),
    ADM_BTN_SETTINGS: ("settings", render_set_menu),
    ADM_BTN_BROADCAST: ("broadcast", render_bc_menu),
}

_CB_PERM_PREFIX = (
    ("adm:bc", "broadcast"), ("adm:bk", "backup"), ("adm:rs", "restore"), ("adm:set", "settings"),
    ("adm:pst", "channel_post"), ("adm:st", "stats"), ("adm:ch", "channels"), ("adm:ad", "admins"), ("adm:vip", "vip"), ("adm:mv", "movie_menu"),
)


@admin_r.callback_query(F.data.startswith("adm:"))
async def adm_stale(cb: CallbackQuery):
    """Hech bir handlerga mos kelmagan adm:* callback: ruxsat yo'q bo'lsa ⛔, aks holda 'eskirgan' xabari."""
    role = APP.admins.role_of(cb.from_user.id)
    for prefix, perm in _CB_PERM_PREFIX:
        if cb.data.startswith(prefix):
            if not role or not has_perm(role, perm):
                return await cb.answer("⛔ Sizda bu amal uchun ruxsat yo‘q.", show_alert=True)
            break
    else:
        if not role:
            return await cb.answer("⛔ Sizda bu amal uchun ruxsat yo‘q.", show_alert=True)
    await cb.answer("⚠️ Bu tugma eskirgan. Admin panelni qayta oching (/admin).", show_alert=True)


# =====================================================================
# 20b. HODISALAR: JOIN REQUEST, BLOKLASH
# =====================================================================
events_r = Router(name="events")


@events_r.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    """Private kanalga qo'shilish so'rovi: faqat QAYD etiladi (bu a'zolik emas!).
    Haqiqiy a'zolik kanal admini tasdiqlagandan keyin get_chat_member orqali alohida tekshiriladi."""
    ch = APP.subs.channels.get(str(req.chat.id))
    if ch:
        APP.subs.record_join_request(req.chat.id, req.from_user.id)


@events_r.my_chat_member()
async def on_my_chat_member(ev: ChatMemberUpdated):
    if ev.chat.type != ChatType.PRIVATE:
        return
    status = ev.new_chat_member.status
    if status == ChatMemberStatus.KICKED:
        APP.users.set_blocked(ev.from_user.id, True)
    elif status == ChatMemberStatus.MEMBER:
        APP.users.set_blocked(ev.from_user.id, False)


# =====================================================================
# 21. FON VAZIFALARI
# =====================================================================
async def maintenance_tick(bot: Bot) -> None:
    """Muddati o'tgan to'lov so'rovlari va VIPlarni tozalaydi, o'zgarishlarni diskka yozadi."""
    if APP.payments.expire_due():
        APP.db.touch("payments")
    for p in APP.payments.unnotified_expired():
        p["expired_notified"] = True
        APP.db.touch("payments")
        await notify_user(
            bot, p["user_id"],
            f"⌛ To‘lov chekini yuborish muddati ({PAYMENT_TIMEOUT_MIN} daqiqa) tugadi va so‘rov bekor qilindi.\n"
            "Xohlasangiz, 💎 VIP obuna bo‘limidan qaytadan tarif tanlang.")
    expired = APP.vip.sweep()
    if expired:
        APP.db.touch("users")
        APP.db.touch("vip")
        for uid in expired:
            await notify_user(bot, uid, "⌛ VIP obunangiz muddati tugadi. Davom ettirish uchun 💎 VIP obuna bo‘limiga kiring.")
    await APP.db.flush()


async def maintenance_loop(bot: Bot) -> None:
    while True:
        try:
            await asyncio.sleep(5)
            await maintenance_tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Maintenance xatosi")


# =====================================================================
# 22. ISHGA TUSHIRISH
# =====================================================================
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def setup_logging(data_dir: Path, token: str) -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    redact = RedactFilter([token])
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(RotatingFileHandler(data_dir / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    root = logging.getLogger()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(redact)
        root.addHandler(h)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(ActivityMiddleware())
    dp.callback_query.outer_middleware(ActivityMiddleware())
    for r in (events_r, common, admin_r, user_r, search_r):
        dp.include_router(r)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logger.error("Handler xatosi: %r", event.exception, exc_info=event.exception)
        upd = event.update
        with contextlib.suppress(Exception):
            if upd.callback_query:
                await upd.callback_query.answer("⚠️ Xatolik yuz berdi. Qayta urinib ko‘ring.", show_alert=True)
            elif upd.message:
                await upd.message.answer("⚠️ Xatolik yuz berdi. Qayta urinib ko‘ring.")
        return True

    return dp


async def main() -> None:
    global APP
    here = Path(__file__).resolve().parent
    load_env(here / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    main_id = os.getenv("MAIN_ADMIN_ID", "").strip()
    if not token or not main_id.isdigit():
        print("❌ .env faylida BOT_TOKEN va MAIN_ADMIN_ID (raqam) ko‘rsatilishi shart. .env.example ga qarang.")
        sys.exit(1)
    data_dir = Path(os.getenv("DATA_DIR", str(here / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(data_dir, token)

    APP = App(data_dir, int(main_id))
    await APP.db.flush()  # yangi o'rnatishda barcha JSON fayllar darhol yoziladi
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    tasks: list[asyncio.Task] = []
    try:
        me = await bot.get_me()
        if not APP.settings.get("bot_name"):
            APP.settings["bot_name"] = me.first_name
            await APP.save("settings")
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands([
            BotCommand(command="start", description="Botni boshlash"),
            BotCommand(command="cancel", description="Joriy amalni bekor qilish"),
        ])
        logger.info("Bot @%s ishga tushdi. Asosiy admin: %s", me.username, APP.main_admin_id)
        if APP.payments.expire_due():
            await APP.save("payments")
        tasks.append(asyncio.create_task(maintenance_loop(bot)))
        if APP.broadcast.is_running():
            logger.info("Yarim qolgan broadcast davom ettiriladi: %s", APP.broadcast.current["job_id"])
            APP.broadcast_task = asyncio.create_task(run_broadcast_task(bot, resumed=True))
            tasks.append(APP.broadcast_task)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await APP.save(*JsonDB.FILES)
        await bot.session.close()
        logger.info("Bot to‘xtatildi, ma'lumotlar saqlandi.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())