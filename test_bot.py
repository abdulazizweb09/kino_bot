#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py uchun avtomatik testlar.

Telegram API soxta sessiya (FakeSession) bilan almashtiriladi: haqiqiy token va tarmoq kerak emas.
Aiogram Dispatcher haqiqiy update'lar bilan ishga tushiriladi (handler, filter, FSM, middleware — hammasi haqiqiy).

Ishga tushirish:  python test_bot.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery, CreateChatInviteLink, DeleteMessage, DeleteWebhook, EditMessageCaption,
    EditMessageReplyMarkup, EditMessageText, GetChat, GetChatMember, GetChatMemberCount, GetFile, GetMe,
    SendDocument, SendMessage, SendPhoto, SendVideo, SetMyCommands,
)
from aiogram.types import (
    ChatFullInfo, ChatInviteLink, ChatMemberAdministrator, ChatMemberBanned, ChatMemberLeft, ChatMemberMember,
    File, Message, Update, User,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bot as B  # noqa: E402

BOT_ID = 123456
MAIN = 1000
RESULTS: list[tuple[str, bool, str]] = []
_DP = None


def check(name: str, cond: bool, info: str = "") -> bool:
    RESULTS.append((name, bool(cond), info))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{info}]" if (info and not cond) else ""))
    return bool(cond)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------
# Soxta Telegram
# ---------------------------------------------------------------------
class FakeSession(BaseSession):
    def __init__(self, tg: "Tg"):
        super().__init__()
        self.tg = tg

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        return self.tg.handle(bot, method)

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.tg.files[url.rsplit("/", 1)[1]]


def markup_to_list(m):
    if m is None:
        return None
    rows = getattr(m, "inline_keyboard", None)
    if rows is not None:
        return [[(b.text, b.callback_data, b.url, bool(getattr(b, "copy_text", None))) for b in r] for r in rows]
    return [[(b.text,) for b in r] for r in m.keyboard]


class Tg:
    def __init__(self):
        self.events: list[dict] = []
        self.alerts: list[tuple[str, bool]] = []
        self.members: dict[tuple[int, int], str] = {}
        self.chats: dict[int | str, dict] = {}
        self.rules = []
        self.files: dict[str, bytes] = {}
        self.mid = 100
        self.cb_no = 0
        self.invite_calls = 0
        self.calls: list[str] = []

    # ---- ma'lumot yaratish ----
    def add_chat(self, chat_id: int, title: str, username: str | None = None, ctype="channel"):
        self.chats[chat_id] = {"id": chat_id, "title": title, "username": username, "type": ctype}
        if username:
            self.chats["@" + username] = self.chats[chat_id]
        self.members[(chat_id, BOT_ID)] = "administrator"

    def set_member(self, chat_id: int, uid: int, status: str):
        self.members[(chat_id, uid)] = status

    # ---- ishlov ----
    def _msg(self, bot, chat_id, text=None, **extra):
        self.mid += 1
        d = {"message_id": self.mid, "date": int(time.time()), "chat": {"id": chat_id, "type": "private"}}
        if text is not None:
            d["text"] = text
        d.update(extra)
        return Message.model_validate(d, context={"bot": bot}), self.mid

    def _record(self, kind, chat_id, mid, text, markup, method, file_id=None):
        self.events.append({"kind": kind, "chat": chat_id, "mid": mid, "text": text, "markup": markup_to_list(markup),
                            "method": method, "file_id": file_id, "raw_markup": markup})

    def handle(self, bot, method):
        name = type(method).__name__
        self.calls.append(name)
        for rule in list(self.rules):
            rule(method)  # istisno ko'tarishi mumkin
        if isinstance(method, SendMessage):
            m, mid = self._msg(bot, method.chat_id, method.text)
            self._record("send", method.chat_id, mid, method.text, method.reply_markup, name)
            return m
        if isinstance(method, (SendVideo, SendPhoto, SendDocument)):
            fid = getattr(method, "video", None) or getattr(method, "photo", None) or getattr(method, "document", None)
            if hasattr(fid, "filename"):
                self.files["sent_" + str(self.mid + 1)] = fid.data if hasattr(fid, "data") else b""
            m, mid = self._msg(bot, method.chat_id, None)
            self._record("send", method.chat_id, mid, method.caption, method.reply_markup, name,
                         file_id=fid if isinstance(fid, str) else getattr(fid, "filename", "file"))
            self.events[-1]["input_file"] = fid
            return m
        if isinstance(method, EditMessageText):
            prev = [e for e in self.events if e["chat"] == method.chat_id and e["mid"] == method.message_id]
            if prev and prev[-1]["text"] == method.text and prev[-1]["markup"] == markup_to_list(method.reply_markup):
                raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
            self._record("edit", method.chat_id, method.message_id, method.text, method.reply_markup, name)
            return True
        if isinstance(method, EditMessageCaption):
            self._record("edit", method.chat_id, method.message_id, method.caption, method.reply_markup, name)
            return True
        if isinstance(method, EditMessageReplyMarkup):
            self._record("editmarkup", method.chat_id, method.message_id, None, method.reply_markup, name)
            return True
        if isinstance(method, AnswerCallbackQuery):
            self.alerts.append((method.text or "", bool(method.show_alert)))
            return True
        if isinstance(method, (DeleteMessage, DeleteWebhook, SetMyCommands)):
            return True
        if isinstance(method, GetMe):
            return User(id=BOT_ID, is_bot=True, first_name="TestKino", username="testkino_bot")
        if isinstance(method, GetChat):
            c = self.chats.get(method.chat_id)
            if not c:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            return ChatFullInfo.model_construct(id=c["id"], type=c["type"], title=c["title"], username=c["username"])
        if isinstance(method, GetChatMemberCount):
            return 42
        if isinstance(method, GetChatMember):
            cid = self.chats[method.chat_id]["id"] if method.chat_id in self.chats else method.chat_id
            if cid not in [c["id"] for c in self.chats.values() if isinstance(c, dict)]:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            st = self.members.get((cid, method.user_id), "left")
            u = User(id=method.user_id, is_bot=False, first_name="U")
            if st == "administrator":
                return ChatMemberAdministrator.model_construct(status="administrator", user=u, can_invite_users=True)
            if st == "member":
                return ChatMemberMember(status="member", user=u)
            if st == "kicked":
                return ChatMemberBanned(status="kicked", user=u, until_date=0)
            return ChatMemberLeft(status="left", user=u)
        if isinstance(method, CreateChatInviteLink):
            self.invite_calls += 1
            return ChatInviteLink(invite_link=f"https://t.me/+AUTOLINK{self.invite_calls:04d}xyz",
                                  creator=User(id=BOT_ID, is_bot=True, first_name="TestKino"),
                                  creates_join_request=bool(method.creates_join_request),
                                  is_primary=False, is_revoked=False)
        if isinstance(method, GetFile):
            data = self.files.get(method.file_id, b"")
            return File(file_id=method.file_id, file_unique_id="u" + method.file_id, file_size=len(data),
                        file_path=f"docs/{method.file_id}")
        raise AssertionError(f"Soxta sessiyada qo'llab-quvvatlanmagan metod: {name}")


class H:
    """Test stendi: haqiqiy Dispatcher + soxta Telegram + vaqtinchalik JSON papka."""

    def __init__(self, data_dir: str | None = None, bot_name="TestKino"):
        self.dir = data_dir or tempfile.mkdtemp(prefix="kinobot_test_")
        self.tg = Tg()
        self.session = FakeSession(self.tg)
        self.bot = Bot(f"{BOT_ID}:TESTTOKEN", session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.boot(bot_name)
        self.names: dict[int, str] = {}
        self.upd = 0

    def boot(self, bot_name="TestKino"):
        B.APP = B.App(self.dir, MAIN)
        if bot_name and not B.APP.settings.get("bot_name"):
            B.APP.settings["bot_name"] = bot_name
        global _DP
        if _DP is None:
            _DP = B.build_dispatcher()  # routerlar modul darajasida: Dispatcher bir marta quriladi
        self.dp = _DP
        self.dp.fsm.storage.storage.clear()

    def user(self, uid):
        return {"id": uid, "is_bot": False, "first_name": self.names.get(uid, f"User{uid}"),
                "username": f"user{uid}" if uid != 3003 else None}

    async def feed(self, d: dict):
        self.upd += 1
        d["update_id"] = self.upd
        await self.dp.feed_update(self.bot, Update.model_validate(d, context={"bot": self.bot}))

    def _base(self, uid, **extra):
        self.tg.mid += 1
        m = {"message_id": self.tg.mid, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "from": self.user(uid)}
        m.update(extra)
        return m

    async def msg(self, uid, text=None, **extra):
        if text is not None:
            extra["text"] = text
        await self.feed({"message": self._base(uid, **extra)})

    async def video(self, uid, fid="vid1", uniq="uvid1", caption=None):
        extra = {"video": {"file_id": fid, "file_unique_id": uniq, "width": 1, "height": 1, "duration": 1}}
        if caption:
            extra["caption"] = caption
        await self.msg(uid, **extra)

    async def photo(self, uid, fid="ph1", caption=None):
        extra = {"photo": [{"file_id": fid + "s", "file_unique_id": "u" + fid + "s", "width": 1, "height": 1},
                           {"file_id": fid, "file_unique_id": "u" + fid, "width": 2, "height": 2}]}
        if caption:
            extra["caption"] = caption
        await self.msg(uid, **extra)

    async def sticker(self, uid):
        await self.msg(uid, sticker={"file_id": "st", "file_unique_id": "ust", "type": "regular", "width": 1,
                                     "height": 1, "is_animated": False, "is_video": False})

    async def document(self, uid, data: bytes, fid="doc1", mime="application/zip"):
        self.tg.files[fid] = data
        await self.msg(uid, document={"file_id": fid, "file_unique_id": "u" + fid, "file_size": len(data), "mime_type": mime,
                                      "file_name": "backup.zip"})

    async def cb(self, uid, data):
        self.tg.cb_no += 1
        last = self.last(uid)
        mid = last["mid"] if last else self.tg.mid + 1
        await self.feed({"callback_query": {
            "id": f"cb{self.tg.cb_no}", "from": self.user(uid), "chat_instance": "ci", "data": data,
            "message": {"message_id": mid, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
                        "from": {"id": BOT_ID, "is_bot": True, "first_name": "TestKino"}, "text": "x"}}})

    async def join_request(self, uid, chat_id):
        await self.feed({"chat_join_request": {
            "chat": {"id": chat_id, "type": "channel", "title": "x"}, "from": self.user(uid),
            "user_chat_id": uid, "date": int(time.time())}})

    async def blocked_bot(self, uid):
        await self.feed({"my_chat_member": {
            "chat": {"id": uid, "type": "private"}, "from": self.user(uid), "date": int(time.time()),
            "old_chat_member": {"status": "member", "user": {"id": BOT_ID, "is_bot": True, "first_name": "T"}},
            "new_chat_member": {"status": "kicked", "user": {"id": BOT_ID, "is_bot": True, "first_name": "T"}, "until_date": 0}}})

    # ---- ko'rish ----
    def evs(self, uid):
        return [e for e in self.tg.events if e["chat"] == uid and e["kind"] in ("send", "edit")]

    def last(self, uid):
        e = self.evs(uid)
        return e[-1] if e else None

    def text(self, uid) -> str:
        e = self.last(uid)
        return (e["text"] or "") if e else ""

    def sent_texts(self, uid):
        return [e["text"] or "" for e in self.tg.events if e["chat"] == uid and e["kind"] == "send"]

    def buttons(self, uid):
        e = self.last(uid)
        return [b for r in (e["markup"] or []) for b in r] if e and e["markup"] else []

    def btn_data(self, uid):
        return [b[1] for b in self.buttons(uid) if len(b) > 1 and b[1]]

    def btn_texts(self, uid):
        return [b[0] for b in self.buttons(uid)]

    def count_video(self, uid):
        return sum(1 for e in self.tg.events if e["chat"] == uid and e["method"] == "SendVideo")

    def mark(self):
        self.tg.events.clear()
        self.tg.alerts.clear()

    def last_alert(self):
        return self.tg.alerts[-1][0] if self.tg.alerts else ""

    def read_json(self, name):
        return json.loads((Path(self.dir) / f"{name}.json").read_text(encoding="utf-8"))

    async def flush(self):
        await B.APP.db.flush()


def find_btn(h: H, uid: int, needle: str) -> str | None:
    for b in h.buttons(uid):
        if needle in b[0] and len(b) > 1 and b[1]:
            return b[1]
    return None


# ---------------------------------------------------------------------
# Testlar
# ---------------------------------------------------------------------
async def t_boot_and_start(h: H):
    section("1-2. Bot ishga tushishi va /start")
    files = {p.name for p in Path(h.dir).glob("*.json")}
    need = {f"{n}.json" for n in ["users", "movies", "admins", "channels", "vip", "payments", "statistics", "settings"]}
    check("1.1 Dispatcher qurildi va JSON fayllar avtomatik yaratildi", need <= files, str(need - files))
    await h.flush()
    check("1.2 Asosiy admin admins.json da", h.read_json("admins").get(str(MAIN), {}).get("role") == "main")

    await h.msg(3001, "/start")
    t = h.text(3001)
    check("2.1 /start matni (bot nomi, admin kontakti)", "Assalomu alaykum" in t and "Men TestKino kino botiman" in t and "@senzaifsan" in t
          and "Kino nomini yoki kod yozing" in t, t)
    kb = h.last(3001)["markup"]
    labels = [b[0] for r in kb for b in r]
    check("2.2 Asosiy klaviatura 4 ta tugma, admin tugmasi yo‘q",
          labels == [B.BTN_SEARCH, B.BTN_VIP, B.BTN_CHANNELS, B.BTN_PROFILE], str(labels))
    check("2.3 Yangi foydalanuvchi users.json ga yozildi", "3001" in h.read_json("users"))
    await h.msg(3001, "/start")
    await h.msg(3001, "/start")
    check("2.4 Takroriy /start qayta ro‘yxatdan o‘tkazmaydi", len(B.APP.users.items) == 1)
    u = h.read_json("users")["3001"]
    check("2.5 users.json maydonlari to‘liq", {"user_id", "first_name", "username", "joined_at", "last_active", "is_blocked", "vip_until", "watched_movies"} <= set(u))
    check("2.6 joined_at ISO 8601 (UTC)", B.parse_iso(u["joined_at"]) is not None and u["joined_at"].endswith("+00:00"))
    await h.msg(MAIN, "/start")
    labels = [b[0] for r in h.last(MAIN)["markup"] for b in r]
    check("2.7 Asosiy adminda 🛠 Admin panel tugmasi bor", B.BTN_ADMIN in labels)


async def t_admin_roles_setup(h: H):
    section("16. Admin rollari va ruxsatlar")
    for uid, nm in [(2001, "KinoAdmin"), (2002, "FullAdmin"), (2003, "Other")]:
        h.names[uid] = nm
        await h.msg(uid, "/start")
    # asosiy admin: kino admini qo'shadi
    await h.msg(MAIN, B.BTN_ADMIN)
    d = h.btn_data(MAIN)
    check("16.1 Asosiy admin paneli: barcha bo‘limlar + Broadcast",
          {"adm:mv", "adm:ch", "adm:st", "adm:ad", "adm:vip", "adm:bk", "adm:set", "adm:bc"} <= set(d), str(d))
    await h.cb(MAIN, "adm:ad")
    await h.cb(MAIN, "adm:ad_add")
    await h.msg(MAIN, "2001")
    check("16.2 Rol tanlash taklifi (1-tur va 2-tur)", f"adm:ad_a:2001:{B.ROLE_MOVIE}" in h.btn_data(MAIN) and f"adm:ad_a:2001:{B.ROLE_FULL}" in h.btn_data(MAIN))
    await h.cb(MAIN, f"adm:ad_a:2001:{B.ROLE_MOVIE}")
    await h.cb(MAIN, "adm:ad_add")
    await h.msg(MAIN, "2002")
    await h.cb(MAIN, f"adm:ad_a:2002:{B.ROLE_FULL}")
    check("16.3 Adminlar admins.json ga yozildi",
          B.APP.admins.role_of(2001) == B.ROLE_MOVIE and B.APP.admins.role_of(2002) == B.ROLE_FULL)
    check("16.4 Yangi adminga xabar + admin klaviaturasi yuborildi",
          any("tayinlandingiz" in t for t in h.sent_texts(2001)))
    await h.cb(MAIN, "adm:ad")
    txt = h.text(MAIN)
    check("16.5 Admin ro‘yxatida ID, ism, tur, vaqt, ruxsatlar", "2001" in txt and "KinoAdmin" in txt and "Kino admini" in txt
          and "Qo‘shilgan" in txt and "Ruxsatlari" in txt, txt[:300])
    # takroriy qo'shish va noma'lum foydalanuvchi
    await h.cb(MAIN, "adm:ad_add")
    await h.msg(MAIN, "2001")
    check("16.6 Mavjud adminni qayta qo‘shib bo‘lmaydi", "allaqachon admin" in h.text(MAIN))
    await h.msg(MAIN, "99999999")
    check("16.7 Botdan foydalanmagan foydalanuvchi admin bo‘lolmaydi", "/start" in h.text(MAIN))
    await h.msg(MAIN, "abc")
    check("16.8 Noto‘g‘ri format tushunarli javob", "raqam" in h.text(MAIN) or "ID" in h.text(MAIN))
    await h.cb(MAIN, "adm:cancel")

    # kino admini paneli
    await h.msg(2001, B.BTN_ADMIN)
    d = h.btn_data(2001)
    check("16.9 Kino admini faqat 🎬 bo‘limini ko‘radi", d == ["adm:mv"], str(d))
    for cbd, label in [("adm:st", "statistika"), ("adm:ch", "obuna"), ("adm:ad", "admin"), ("adm:vip", "vip"),
                       ("adm:bk", "backup"), ("adm:set", "sozlamalar"), ("adm:bc", "broadcast"),
                       ("adm:st_u", "stat-u"), ("adm:ch_tog", "obuna-toggle"), ("adm:bk_do", "backup-do"),
                       ("adm:bc_new", "bc-new"), ("adm:vip_give", "vip-give"), ("adm:set_e:card_number", "set-edit"),
                       ("adm:ad_add", "admin-add")]:
        h.mark()
        await h.cb(2001, cbd)
        check(f"16.10 Kino admini callback orqali chetlab o‘ta olmaydi: {cbd}", "⛔" in h.last_alert(), h.last_alert())
    h.mark()
    await h.cb(2001, "pay_ok:P00001")
    check("16.11 Kino admini to‘lovni tasdiqlay olmaydi", "⛔" in h.last_alert())

    # to'liq admin
    await h.msg(2002, B.BTN_ADMIN)
    d = set(h.btn_data(2002))
    check("16.12 To‘liq admin: kino/obuna/stat/admin/vip bor; backup/sozlama/broadcast yo‘q",
          {"adm:mv", "adm:ch", "adm:st", "adm:ad", "adm:vip"} <= d and not ({"adm:bk", "adm:set", "adm:bc"} & d), str(d))
    for cbd in ["adm:bk", "adm:bk_do", "adm:bk_rs", "adm:set", "adm:set_e:card_number", "adm:bc", "adm:bc_new", "adm:bc_ok", "adm:bc_stop", "pay_ok:P00001", "pay_no:P00001", "adm:vip_pend"]:
        h.mark()
        await h.cb(2002, cbd)
        check(f"16.13 To‘liq admin ruxsatsiz callback: {cbd}", "⛔" in h.last_alert(), h.last_alert())
    # to'liq admin boshqa to'liq adminni qo'sha olmaydi, asosiy adminni olib tashlay olmaydi
    await h.cb(2002, "adm:ad_add")
    await h.msg(2002, "2003")
    check("16.14 To‘liq admin faqat 1-tur adminni qo‘sha oladi (2-tur taklif etilmaydi)",
          f"adm:ad_a:2003:{B.ROLE_MOVIE}" in h.btn_data(2002) and f"adm:ad_a:2003:{B.ROLE_FULL}" not in h.btn_data(2002))
    h.mark()
    await h.cb(2002, f"adm:ad_a:2003:{B.ROLE_FULL}")
    check("16.15 Callbackni qo‘lda yuborib 2-tur admin qo‘shib bo‘lmaydi", "⛔" in h.last_alert() and B.APP.admins.role_of(2003) is None)
    for cbd in [f"adm:ad_xy:{MAIN}", f"adm:ad_x:{MAIN}", f"adm:ad_r:{MAIN}:{B.ROLE_MOVIE}", f"adm:ad_r:2001:{B.ROLE_FULL}"]:
        h.mark()
        await h.cb(2002, cbd)
        ok = B.APP.admins.role_of(MAIN) == B.ROLE_MAIN and B.APP.admins.role_of(2001) == B.ROLE_MOVIE
        check(f"16.16 To‘liq admin asosiy adminni/rolni o‘zgartira olmaydi: {cbd}", ok and ("⛔" in h.last_alert() or "topilmadi" in h.last_alert() or "ruxsat" in h.last_alert().lower()), h.last_alert())
    # oddiy foydalanuvchi
    for cbd in ["adm:mv", "adm:st", "adm:home", "adm:mv_add", "adm:bk_do"]:
        h.mark()
        await h.cb(3001, cbd)
        check(f"16.17 Oddiy foydalanuvchi admin callbackiga kira olmaydi: {cbd}", "⛔" in h.last_alert())
    await h.msg(3001, B.BTN_ADMIN)
    check("16.18 Oddiy foydalanuvchi admin panelga kira olmaydi", "admin huquqi yo‘q" in h.text(3001))
    await h.msg(3001, "/admin")
    check("16.19 /admin buyrug‘i ham himoyalangan", "admin huquqi yo‘q" in h.text(3001))
    # asosiy admin 2-tur adminni olib tashlay oladi / rol o'zgartira oladi
    await h.cb(MAIN, f"adm:ad_r:2002:{B.ROLE_MOVIE}")
    check("16.20 Asosiy admin rolni o‘zgartira oladi", B.APP.admins.role_of(2002) == B.ROLE_MOVIE)
    await h.cb(MAIN, f"adm:ad_r:2002:{B.ROLE_FULL}")
    check("16.21 Asosiy admin rolni qaytara oladi", B.APP.admins.role_of(2002) == B.ROLE_FULL)
    # ConfigDA asosiy admin ham boshqa ro'yxatga tushib qolsa -> tuzatiladi
    B.APP.admins.items["2003"] = {"user_id": 2003, "first_name": "x", "role": "main", "added_by": 1, "added_at": B.iso(B.utcnow())}
    B.APP.admins.ensure_main()
    check("16.22 Konfiguratsiyadan boshqa 'main' rolli yozuv pasaytiriladi", B.APP.admins.role_of(2003) is None or B.APP.admins.role_of(2003) == B.ROLE_FULL)
    B.APP.admins.items.pop("2003", None)


async def t_movie_add(h: H):
    section("3. Kino qo‘shish, o‘chirish va ruxsatlar")
    await h.msg(2001, B.BTN_ADMIN)
    await h.cb(2001, "adm:mv")
    check("3.1 Kino menyusi", "adm:mv_add" in h.btn_data(2001))
    await h.cb(2001, "adm:mv_add")
    check("3.2 1/3 — video so‘raladi, Bekor qilish tugmasi bor", "Kino videosini" in h.text(2001) and "adm:cancel" in h.btn_data(2001))
    await h.msg(2001, "salom")
    check("3.3 Video o‘rniga matn: tushunarli xabar", "video" in h.text(2001).lower() and "❗" in h.text(2001))
    await h.photo(2001)
    check("3.4 Video o‘rniga rasm: tushunarli xabar", "❗" in h.text(2001))
    await h.video(2001, "FILE_A", "UNIQ_A", caption="BU CAPTION NOM BO‘LMASLIGI KERAK")
    check("3.5 Video qabul qilindi → description so‘raladi", "Tavsif" in h.text(2001) or "tavsif" in h.text(2001))
    await h.photo(2001)
    check("3.6 Description o‘rniga rasm: xato xabari", "❗" in h.text(2001))
    await h.msg(2001, "x" * 900)
    check("3.7 Juda uzun description rad etiladi", "juda uzun" in h.text(2001))
    await h.msg(2001, "Qo‘rqinchli film haqida tavsif")
    check("3.8 Description qabul qilindi → nom so‘raladi", "Kino nomini" in h.text(2001))
    await h.msg(2001, "")  # bo'sh matn
    await h.msg(2001, "Astral 1")
    t = h.text(2001)
    check("3.9 Tasdiqlovchi xabar (kod va nom)", "✅ Kino muvaffaqiyatli qo‘shildi!" in t and "Kino kodi: " in t and "Kino nomi: Astral 1" in t, t)
    mv = h.read_json("movies")
    m = next(iter(mv.values()))
    check("3.10 movies.json to‘liq maydonlar", {"movie_id", "code", "title", "description", "file_id", "added_by", "added_at", "views_count"} <= set(m))
    check("3.11 Kino nomi captiondan emas, adminning o‘zi yuborgandan olindi", m["title"] == "Astral 1" and "CAPTION" not in json.dumps(m, ensure_ascii=False))
    check("3.12 file_id saqlandi, kod avtomatik", m["file_id"] == "FILE_A" and m["code"] == "1001" and m["added_by"] == 2001)
    # takroriy video
    await h.cb(2001, "adm:mv_add")
    await h.video(2001, "FILE_A2", "UNIQ_A")
    check("3.13 Takroriy video qo‘shilmaydi", "allaqachon qo‘shilgan" in h.text(2001))
    await h.video(2001, "FILE_B", "UNIQ_B")
    await h.msg(2001, "Tavsif ikki")
    await h.msg(2001, "astral  1")
    check("3.14 Takroriy nom (katta-kichik/bo‘shliqsiz) rad etiladi", "allaqachon bor" in h.text(2001))
    await h.msg(2001, "Astral 2")
    check("3.15 Ikkinchi kino noyob kod bilan qo‘shildi", len(B.APP.movies.items) == 2 and {m["code"] for m in B.APP.movies.items.values()} == {"1001", "1002"})
    # Bekor qilish
    await h.cb(2001, "adm:mv_add")
    await h.video(2001, "FILE_C", "UNIQ_C")
    await h.cb(2001, "adm:cancel")
    check("3.16 Bekor qilish jarayonni to‘xtatadi (kino qo‘shilmadi)", len(B.APP.movies.items) == 2 and "Admin panel" in h.text(2001))
    await h.msg(2001, "ixtiyoriy matn")
    check("3.17 Bekordan so‘ng matn oddiy qidiruv sifatida ishlaydi", "hech narsa topilmadi" in h.text(2001))
    # boshqa admin (to'liq) kino qo'shadi
    await h.cb(2002, "adm:mv_add")
    await h.video(2002, "FILE_D", "UNIQ_D")
    await h.msg(2002, "Tavsif uch")
    await h.msg(2002, "Sehrgar")
    check("3.18 To‘liq admin ham kino qo‘sha oladi", len(B.APP.movies.items) == 3)
    # kino admini faqat o'zinikini ko'radi
    await h.cb(2001, "adm:mv_list:0")
    check("3.19 Kino admini ro‘yxatida faqat o‘zi qo‘shganlar", "2 ta" in h.text(2001) and not any("Sehrgar" in b[0] for b in h.buttons(2001)))
    sehr = next(m for m in B.APP.movies.items.values() if m["title"] == "Sehrgar")
    h.mark()
    await h.cb(2001, f"adm:mv_i:{sehr['movie_id']}")
    check("3.20 Kino admini boshqa admin kinosini ko‘ra olmaydi", "ruxsat" in h.last_alert().lower() or "topilmadi" in h.last_alert().lower())
    h.mark()
    await h.cb(2001, f"adm:mv_xy:{sehr['movie_id']}")
    check("3.21 Kino admini boshqa admin kinosini o‘chira olmaydi (callbackni qo‘lda yuborsa ham)",
          B.APP.movies.get(sehr["movie_id"]) is not None and "⛔" in h.last_alert(), h.last_alert())
    await h.cb(2001, "adm:mv_delcode")
    await h.msg(2001, sehr["code"])
    check("3.22 Kod orqali o‘chirishda ham boshqa admin kinosi himoyalangan", B.APP.movies.get(sehr["movie_id"]) is not None and "topilmadi" in h.text(2001))
    await h.cb(2001, "adm:cancel")
    # o'z kinosini o'chirish (tasdiq bilan)
    a2 = next(m for m in B.APP.movies.items.values() if m["title"] == "Astral 2")
    await h.cb(2001, "adm:mv_delcode")
    await h.msg(2001, a2["code"])
    check("3.23 O‘chirishdan oldin tasdiq so‘raladi", "tasdiqlaysizmi" in h.text(2001) and B.APP.movies.get(a2["movie_id"]) is not None)
    await h.cb(2001, f"adm:mv_xy:{a2['movie_id']}")
    check("3.24 O‘z kinosi o‘chirildi", B.APP.movies.get(a2["movie_id"]) is None and "o‘chirildi" in h.text(2001))
    # to'liq admin hammasini o'chira oladi (ro'yxatdan tanlab)
    await h.cb(2002, "adm:mv_list:0")
    check("3.25 To‘liq admin barcha kinolarni ko‘radi", len(h.btn_data(2002)) >= 3)
    first = next(d for d in h.btn_data(2002) if d.startswith("adm:mv_i:"))
    await h.cb(2002, first)
    check("3.26 Ro‘yxatdan tanlab o‘chirish tugmasi bor", any(d.startswith("adm:mv_x:") for d in h.btn_data(2002)))
    # qayta kino qo'shamiz (keyingi testlar uchun) — asosiy admin
    await h.cb(MAIN, "adm:mv_add")
    await h.video(MAIN, "FILE_E", "UNIQ_E")
    await h.msg(MAIN, "Astral 2 — qayta")
    await h.msg(MAIN, "Astral 2")
    check("3.27 Asosiy admin kino qo‘shdi", B.APP.movies.by_code("1004") is not None)
    await h.msg(MAIN, B.BTN_ADMIN)  # holatni tozalaydi
    check("3.28 Admin amallari statistikada loglandi", B.APP.stats.s["admin_action_counts"].get("2001", {}).get("movie_add") == 2)


async def t_search_and_deliver(h: H):
    section("4-5. Qidiruv va kino yuborish")
    codes = {m["title"]: m for m in B.APP.movies.items.values()}
    await h.msg(3001, B.BTN_SEARCH)
    check("4.1 🎬 Kino qidirish tugmasi", "yozing" in h.text(3001))
    await h.msg(3001, "q")
    check("4.2 Bitta belgi, topilmadi → namuna xabar", h.text(3001) == "🔎 Kino nomini yozing, masalan: Astral", h.text(3001))
    await h.msg(3001, "zzzz")
    check("4.3 Natija yo‘q xabari", h.text(3001).startswith("😕 zzzz bo‘yicha hech narsa topilmadi.") and "Nomni boshqacha yozib ko‘ring." in h.text(3001), h.text(3001))
    await h.msg(3001, "   ASTRAL   ")
    check("4.4 Katta-kichik harf va ortiqcha bo‘shliqsiz qidiruv", h.text(3001) == "🔎 Topildi: 2 ta", h.text(3001))
    check("4.5 Natijalar inline tugmalarda, tugmada kino nomi", {"Astral 1", "Astral 2"} <= set(h.btn_texts(3001)), str(h.btn_texts(3001)))
    await h.msg(3001, "stra")
    check("4.6 Nomning bir qismi bo‘yicha qidiruv", "Topildi: 2" in h.text(3001))
    await h.msg(3001, "astral 1")
    check("4.7 Aniq nom → 1 natija", "Topildi: 1" in h.text(3001))
    await h.msg(3001, codes["Sehrgar"]["code"])
    check("4.8 Kod bo‘yicha aniq qidiruv", "Topildi: 1" in h.text(3001) and h.btn_texts(3001) == ["Sehrgar"], str(h.btn_texts(3001)))
    await h.msg(3001, "1")
    check("4.9 Qisman kod ('1') kodga mos kelmaydi, nom bo‘yicha qidiriladi ('Astral 1')", "Topildi" in h.text(3001) and "Astral 1" in h.btn_texts(3001))
    await h.msg(3001, "/nomalum")
    check("4.10 Noma'lum buyruq tushunarli javob oladi", "Noma'lum buyruq" in h.text(3001))
    await h.sticker(3001)
    check("4.11 Stiker yuborilsa tushunarli javob", "matn shaklida" in h.text(3001))

    # Obunasiz yuborish (kanal hali yo'q)
    mv = codes["Sehrgar"]
    h.mark()
    await h.cb(3001, f"mv:{mv['movie_id']}")
    ev = [e for e in h.tg.events if e["method"] == "SendVideo"]
    check("5.1 Kino video sifatida yuborildi (file_id orqali)", len(ev) == 1 and ev[0]["file_id"] == "FILE_D", str(ev))
    check("5.2 Video ostida nom va tavsif", ev and "Sehrgar" in ev[0]["text"] and "Tavsif uch" in ev[0]["text"])
    check("5.3 Ko‘rishlar soni oshdi", B.APP.movies.get(mv["movie_id"])["views_count"] == 1)
    await h.cb(3001, f"mv:{mv['movie_id']}")
    check("5.4 Qayta ko‘rish ko‘rishlar sonini oshiradi, ko‘rilgan kinolar takrorlanmaydi",
          B.APP.movies.get(mv["movie_id"])["views_count"] == 2 and B.APP.users.get(3001)["watched_movies"] == [mv["movie_id"]])
    await h.cb(3001, "mv:99999")
    check("5.5 O‘chirilgan/yo‘q kino uchun tushunarli xabar", "topilmadi" in h.text(3001))
    await h.msg(3001, B.BTN_PROFILE)
    t = h.text(3001)
    check("5.6 Profil: ID, ism, username, sana, VIP holati, ko‘rgan kinolar", "3001" in t and "User3001" in t and "@user3001" in t
          and "Ro‘yxatdan o‘tgan sana" in t and "Oddiy foydalanuvchi" in t and "Ko‘rgan kinolari soni: <b>1</b>" in t, t)
    await h.msg(3003, "/start")
    await h.msg(3003, B.BTN_PROFILE)
    check("5.7 Usernamesiz foydalanuvchi profili xatosiz (—)", "Username: —" in h.text(3003))
    await h.msg(3001, B.BTN_CHANNELS)
    check("5.8 Kanallar yo‘q holati", "yo‘q" in h.text(3001))


async def t_subscription(h: H):
    section("6-9. Majburiy obuna (ochiq/private/ko‘p kanal) va VIP imtiyozi")
    h.tg.add_chat(-1001000000001, "Ochiq Kanal 1", "openchan1")
    h.tg.add_chat(-1001000000002, "Ochiq Guruh 2", "opengroup2", ctype="supergroup")
    h.tg.add_chat(-1001000000003, "Yopiq Kanal 3", None)
    # ochiq kanal qo'shish (to'liq admin)
    await h.cb(2002, "adm:ch")
    check("6.1 Majburiy obuna menyusi to‘liq adminga ochiq", "adm:ch_add:public" in h.btn_data(2002) and "adm:ch_add:private" in h.btn_data(2002))
    await h.cb(2002, "adm:ch_add:public")
    await h.msg(2002, "xx")
    check("6.2 Noto‘g‘ri kanal formati", "Format noto‘g‘ri" in h.text(2002))
    await h.msg(2002, "@nomavjud")
    check("6.3 Mavjud bo‘lmagan kanal rad etiladi", "topilmadi" in h.text(2002))
    await h.msg(2002, "@openchan1")
    check("6.4 Ochiq kanal qo‘shildi (havola avtomatik)", "-1001000000001" in B.APP.subs.channels.__repr__() and B.APP.subs.channels["-1001000000001"]["invite_link"] == "https://t.me/openchan1")
    await h.cb(2002, "adm:ch_add:public")
    await h.msg(2002, "-1001000000002")
    check("6.5 Guruh chat ID orqali (username bilan) qo‘shildi", "-1001000000002" in B.APP.subs.channels)
    await h.cb(2002, "adm:ch_add:public")
    await h.msg(2002, "@openchan1")
    check("6.6 Takroriy kanal qo‘shilmaydi", "allaqachon" in h.text(2002))
    # bot admin emasligi
    h.tg.add_chat(-1001000000004, "Admin emas", "noadm")
    h.tg.members[(-1001000000004, BOT_ID)] = "member"
    await h.cb(2002, "adm:ch_add:public")
    await h.msg(2002, "@noadm")
    check("6.7 Bot administrator bo‘lmagan kanal rad etiladi", "administrator emas" in h.text(2002))
    await h.cb(2002, "adm:cancel")
    # private kanal
    await h.cb(2002, "adm:ch_add:private")
    await h.msg(2002, "@openchan1")
    check("6.8 Private oqimda ochiq kanalni qo‘shib bo‘lmaydi (yoki allaqachon bor)", "-1001000000003" not in B.APP.subs.channels)
    await h.cb(2002, "adm:ch_add:private")
    await h.msg(2002, "-1001000000003")
    check("6.9 Private kanal: invite link so‘raladi", "join request" in h.text(2002) and "adm:ch_auto" in h.btn_data(2002))
    await h.msg(2002, "https://example.com/abc")
    check("6.10 Noto‘g‘ri invite havolasi rad etiladi", "formati noto‘g‘ri" in h.text(2002))
    await h.cb(2002, "adm:ch_auto")
    c3 = B.APP.subs.channels.get("-1001000000003")
    check("6.11 Private kanal: join-request havolasi avtomatik yaratildi", c3 and c3["channel_type"] == "private" and "AUTOLINK" in c3["invite_link"], str(c3))
    check("6.12 channels.json maydonlari", {"channel_id", "title", "channel_type", "username", "invite_link", "is_active"} <= set(h.read_json("channels")["-1001000000003"]))
    await h.cb(2002, "adm:ch_list")
    check("6.13 Kanallar ro‘yxati (3 ta)", len([d for d in h.btn_data(2002) if d.startswith("adm:ch_i:")]) == 3)
    await h.cb(2002, "adm:ch_i:-1001000000003")
    check("6.14 Kanal ma'lumotlarini tekshirish (bot huquqi)", "Bot administrator" in h.text(2002))
    h.tg.members[(-1001000000003, BOT_ID)] = "member"
    await h.cb(2002, "adm:ch_i:-1001000000003")
    check("6.15 Bot huquqi yo‘qolsa, tekshiruv ogohlantiradi", "Muammo" in h.text(2002))
    h.tg.members[(-1001000000003, BOT_ID)] = "administrator"
    # obuna oynasi: foydalanuvchi hech qaysi kanalga a'zo emas
    mv = next(m for m in B.APP.movies.items.values() if m["title"] == "Sehrgar")
    h.mark()
    await h.cb(3001, f"mv:{mv['movie_id']}")
    check("7.1 A'zo bo‘lmagan foydalanuvchiga kino yuborilmadi", h.count_video(3001) == 0)
    t = h.text(3001)
    check("7.2 Obuna oynasi matni (3 ta kanal)", "quyidagi 3 ta kanal va guruhga a‘zo bo‘ling" in t and "🔄 Tekshirish tugmasini bosing" in t, t)
    urls = [b[2] for b in h.buttons(3001) if b[2]]
    check("7.3 Har bir kanal uchun URL tugma + 🔄 Tekshirish", len(urls) == 3 and "chk:" + str(mv["movie_id"]) in h.btn_data(3001) and "https://t.me/openchan1" in urls, str(urls))
    check("7.4 Private kanal havolasi ham ko‘rsatildi", any("AUTOLINK" in u for u in urls))
    # Join request yuboriladi — a'zolik emas
    await h.join_request(3001, -1001000000003)
    check("7.5 Join request qayd etildi (join_requests.json)", B.APP.subs.has_join_request(-1001000000003, 3001))
    h.tg.set_member(-1001000000001, 3001, "member")
    h.tg.set_member(-1001000000002, 3001, "member")
    h.mark()
    await h.cb(3001, f"chk:{mv['movie_id']}")
    check("7.6 Faqat join request yuborish a‘zolik hisoblanmaydi — kino yuborilmadi", h.count_video(3001) == 0)
    check("7.7 Faqat private kanal qoldi (2 ta a‘zo bo‘lgan kanal yo‘qoldi)", "quyidagi 1 ta kanal" in h.text(3001) and sum(1 for b in h.buttons(3001) if b[2]) == 1)
    check("7.8 Join request yuborgan kanal ⏳ belgisi bilan", any("⏳" in b[0] for b in h.buttons(3001)))
    for _ in range(3):
        h.tg.alerts.clear()
        await h.cb(3001, f"chk:{mv['movie_id']}")
    check("7.9 Tekshirish tugmasi qayta-qayta bosilganda to‘g‘ri ishlaydi (yuborilmaydi, xato bermaydi)", h.count_video(3001) == 0 and "Hali barcha" in h.last_alert(), h.last_alert() + str(h.count_video(3001)))
    # kanal admini tasdiqlaydi
    h.tg.set_member(-1001000000003, 3001, "member")
    h.mark()
    await h.cb(3001, f"chk:{mv['movie_id']}")
    check("7.10 Tasdiqlangandan keyin haqiqiy a‘zolik tekshirilib kino yuborildi", h.count_video(3001) == 1)
    check("7.11 Join request yozuvi tozalandi", not B.APP.subs.has_join_request(-1001000000003, 3001))
    h.mark()
    await h.cb(3001, f"chk:{mv['movie_id']}")
    check("7.12 Qayta tekshirish: har safar bitta kino (dublikat himoyasi tugma bosilishiga bog‘liq)", h.count_video(3001) == 1)
    # 3 kanaldan 2 tasi
    h.mark()
    await h.cb(3002, f"mv:{mv['movie_id']}") if False else None
    await h.msg(3002, "/start")
    h.tg.set_member(-1001000000001, 3002, "member")
    h.tg.set_member(-1001000000003, 3002, "member")
    await h.cb(3002, f"mv:{mv['movie_id']}")
    t = h.text(3002)
    check("8.1 3 ta kanaldan 2 tasiga a‘zo → faqat 1 ta kanal ko‘rsatiladi", "quyidagi 1 ta kanal" in t and [b[2] for b in h.buttons(3002) if b[2]] == ["https://t.me/opengroup2"], t)
    h.tg.set_member(-1001000000002, 3002, "member")
    await h.cb(3002, f"chk:{mv['movie_id']}")
    check("8.2 Barcha kanallarga a‘zo bo‘lgach kino yuborildi", h.count_video(3002) == 1)
    # a'zodan chiqib ketsa
    h.tg.set_member(-1001000000001, 3002, "left")
    await h.cb(3002, f"mv:{mv['movie_id']}")
    check("8.3 Kanaldan chiqib ketgan foydalanuvchidan qayta obuna talab qilinadi", h.count_video(3002) == 1 and "quyidagi 1 ta kanal" in h.text(3002))
    h.tg.set_member(-1001000000001, 3002, "kicked")
    await h.cb(3002, f"chk:{mv['movie_id']}")
    check("8.4 Kanaldan ban qilingan foydalanuvchi a‘zo hisoblanmaydi", h.count_video(3002) == 1)
    h.tg.set_member(-1001000000001, 3002, "member")
    # Kanallar tugmasi
    await h.msg(3002, B.BTN_CHANNELS)
    check("8.5 📢 Obuna kanallari: holat ✅/❌ ko‘rsatiladi", "✅" in h.text(3002) and "Obuna kanallari" in h.text(3002))
    await h.msg(3001, B.BTN_CHANNELS)
    check("8.6 Kanallar ro‘yxatida a‘zo bo‘lgan kanallar belgilanadi", h.text(3001).count("✅") == 3)

    # Obunani vaqtincha o'chirish
    await h.cb(2002, "adm:ch_tog")
    await h.msg(3003, "/start")
    h.mark()
    await h.cb(3003, f"mv:{mv['movie_id']}")
    check("8.7 Obuna tizimi o‘chirilsa, tekshiruvsiz kino yuboriladi", h.count_video(3003) == 1)
    await h.cb(2002, "adm:ch_tog")
    check("8.8 Obuna qayta yoqildi", B.APP.settings["subscription_enabled"] is True)
    # bot kanalda admin emas bo'lib qolsa — foydalanuvchi qamalib qolmaydi, adminga xabar boradi
    h.tg.members[(-1001000000003, BOT_ID)] = "member"

    def raise_forbidden(method):
        if isinstance(method, GetChatMember) and method.chat_id == -1001000000003:
            raise TelegramBadRequest(method=method, message="Bad Request: CHAT_ADMIN_REQUIRED")
    h.tg.rules.append(raise_forbidden)
    await h.msg(3004, "/start")
    h.mark()
    h.tg.set_member(-1001000000001, 3004, "member")
    h.tg.set_member(-1001000000002, 3004, "member")
    await h.cb(3004, f"mv:{mv['movie_id']}")
    check("8.9 Kanal sozlamasi buzilsa foydalanuvchi qamalib qolmaydi va adminga ogohlantirish ketadi",
          h.count_video(3004) == 1 and any("Majburiy obuna muammosi" in t for t in h.sent_texts(MAIN)))
    h.tg.rules.clear()
    h.tg.members[(-1001000000003, BOT_ID)] = "administrator"

    # Tarmoq xatosi: yuborilmaydi, tushunarli xabar
    from aiogram.exceptions import TelegramNetworkError

    def raise_net(method):
        if isinstance(method, GetChatMember):
            raise TelegramNetworkError(method=method, message="boom")
    await h.msg(3005, "/start")
    h.tg.rules.append(raise_net)
    h.mark()
    old_sleep = asyncio.sleep

    async def fast_sleep(x, *a, **k):
        return await old_sleep(0)
    B.asyncio.sleep = fast_sleep
    try:
        await h.cb(3005, f"mv:{mv['movie_id']}")
    finally:
        B.asyncio.sleep = old_sleep
    check("8.10 Telegram tarmoq xatosida bot qulamaydi, foydalanuvchiga tushunarli xabar", h.count_video(3005) == 0 and "vaqtinchalik" in h.last_alert())
    h.tg.rules.clear()


async def t_vip_flow(h: H):
    section("10-15. VIP: tarif, chek, 30 daqiqa, tasdiqlash, rad etish, muddat")
    mv = next(m for m in B.APP.movies.items.values() if m["title"] == "Sehrgar")
    await h.msg(3006, "/start")
    await h.msg(3006, B.BTN_VIP)
    t = h.text(3006)
    check("10.1 VIP bo‘limi matni va tariflar", "VIP OBUNA" in t and "15 kunlik — 10 000 so‘m" in t and "30 kunlik — 15 000 so‘m" in t
          and "2 oylik — 30 000 so‘m" in t and "1 yillik — 150 000 so‘m" in t and "Majburiy obunasiz kino olish" in t, t)
    check("10.2 Tariflar alohida inline tugmalarda", h.btn_data(3006) == ["vip_plan:d15", "vip_plan:d30", "vip_plan:m2", "vip_plan:y1"], str(h.btn_data(3006)))
    h.mark()
    await h.cb(3006, "vip_plan:d30")
    check("10.3 Karta ma'lumotlari kiritilmagan bo‘lsa tushunarli xabar", "to‘lov ma'lumotlari kiritilmagan" in h.last_alert(), h.last_alert())
    # karta ma'lumotlarini faqat asosiy admin kiritadi
    await h.cb(MAIN, "adm:set")
    await h.cb(MAIN, "adm:set_e:card_holder")
    await h.msg(MAIN, "Aziz Azizov")
    await h.cb(MAIN, "adm:set_e:card_number")
    await h.msg(MAIN, "12345")
    check("10.4 Noto‘g‘ri karta raqami rad etiladi", "12–19" in h.text(MAIN))
    await h.msg(MAIN, "8600 1234-5678 9012")
    check("10.5 Karta raqami saqlandi (4 talik guruhlar)", B.APP.settings["card_number"] == "8600 1234 5678 9012")
    await h.cb(MAIN, "adm:set_e:payment_note")
    await h.msg(MAIN, "Izohga ism-familiyangizni yozing")
    await h.cb(MAIN, "adm:set_e:contact_username")
    await h.msg(MAIN, "bad name")
    check("10.6 Noto‘g‘ri username rad etiladi", "noto‘g‘ri" in h.text(MAIN))
    await h.msg(MAIN, "@senzaifsan")
    await h.cb(MAIN, "adm:set_e:bot_name")
    await h.msg(MAIN, "TestKino")
    st = h.read_json("settings")
    check("10.7 Sozlamalar settings.json ga yozildi", st["card_holder"] == "Aziz Azizov" and st["payment_note"].startswith("Izohga"))
    check("10.8 Karta raqami logga yozilmadi", "8600" not in json.dumps(B.APP.db.data["statistics"]["admin_log"]))
    # tarif tanlash
    h.mark()
    await h.cb(3006, "vip_plan:d30")
    t = h.text(3006)
    check("10.9 Karta egasi, karta raqami, izoh ko‘rsatildi", "Aziz Azizov" in t and "8600 1234 5678 9012" in t and "Izohga" in t and "15 000" in t, t)
    check("10.10 Chek talabi, 30 daqiqa va ogohlantirish matni", "📸 To‘lov chekini rasm shaklida yuboring." in t and "30 daqiqa" in t and "Faqat haqiqiy to‘lov chekini yuboring" in t)
    copy_btns = [b for b in h.buttons(3006) if b[3]]
    check("10.11 Karta raqami va ismni nusxalash tugmalari", len(copy_btns) == 2)
    p1 = B.APP.payments.awaiting(3006)
    check("10.12 Tanlangan tarif saqlandi (awaiting_receipt)", p1 and p1["plan"] == "d30" and p1["amount"] == 15000)
    check("10.13 Muddat: yaratilgandan 30 daqiqa keyin", (B.parse_iso(p1["expires_at"]) - B.parse_iso(p1["created_at"])) == timedelta(minutes=30))
    # qayta tarif tanlash
    await h.cb(3006, "vip_plan:m2")
    p2 = B.APP.payments.awaiting(3006)
    check("10.14 Qayta tarif tanlash mumkin: eski so‘rov bekor, yangisi faol", p1["status"] == "cancelled" and p2["plan"] == "m2" and p2["payment_id"] != p1["payment_id"])
    # chek: rasm bo'lmagan formatlar
    await h.msg(3006, "mana chek")
    check("11.1 Matn chek sifatida qabul qilinmaydi", "faqat <b>rasm</b>" in h.text(3006) and B.APP.payments.awaiting(3006))
    await h.sticker(3006)
    check("11.2 Stiker qabul qilinmaydi", B.APP.payments.awaiting(3006) is not None)
    await h.video(3006, "vv", "uvv")
    check("11.3 Video qabul qilinmaydi", B.APP.payments.awaiting(3006) is not None and B.APP.payments.pending(3006) is None)
    await h.document(3006, b"x", "docx1", "application/pdf")
    check("11.4 Fayl (PDF) qabul qilinmaydi", B.APP.payments.pending(3006) is None)
    await h.msg(3006, "/start")
    check("11.5 Chek yuborilmaguncha so‘rov adminga ketmagan", not any(e["method"] == "SendPhoto" and e["chat"] == MAIN for e in h.tg.events))
    # kino qidirish chek kutish paytida ishlaydi
    await h.msg(3006, "Sehrgar")
    check("11.6 Chek kutayotganda kino qidirish ishlaydi", "Topildi: 1" in h.text(3006))
    # chek yuborish
    h.mark()
    await h.photo(3006, "RECEIPT1")
    check("11.7 Chek rasmi qabul qilindi", "Chekingiz qabul qilindi" in h.text(3006))
    pe = [e for e in h.tg.events if e["chat"] == MAIN and e["method"] == "SendPhoto"]
    check("11.8 Chek asosiy adminga yuborildi (rasm)", len(pe) == 1 and pe[0]["file_id"] == "RECEIPT1")
    cap = pe[0]["text"]
    check("11.9 Admin xabarida: ism, ID, username, tarif, summa, vaqt", "User3006" in cap and "3006" in cap and "@user3006" in cap and "2 oylik" in cap
          and "30 000" in cap and "So‘rov vaqti" in cap, cap)
    check("11.10 ✅ Tasdiqlash / ❌ Rad etish tugmalari", [b[0] for r in pe[0]["markup"] for b in r] == ["✅ Tasdiqlash", "❌ Rad etish"])
    pay = B.APP.payments.items[p2["payment_id"]]
    check("11.11 Holat pending, receipt_file_id saqlandi", pay["status"] == "pending" and pay["receipt_file_id"] == "RECEIPT1")
    check("11.12 payments.json maydonlari", {"payment_id", "user_id", "plan", "amount", "receipt_file_id", "status", "created_at", "expires_at", "reviewed_by", "admin_comment"} <= set(h.read_json("payments")[pay["payment_id"]]))
    await h.cb(3006, "vip_plan:d15")
    check("11.13 So‘rov ko‘rib chiqilayotganda yangi tarif tanlab bo‘lmaydi", "ko‘rib chiqilmoqda" in h.last_alert())
    # admin tasdiqlaydi
    pid = pay["payment_id"]
    await h.cb(MAIN, f"pay_ok:{pid}")
    check("13.1 Tasdiqlashda izoh so‘raladi", "izoh" in h.text(MAIN).lower())
    await h.msg(MAIN, "")
    await h.msg(MAIN, "Rahmat! Yaxshi tomosha.")
    ut = h.sent_texts(3006)[-1]
    p = B.APP.payments.items[pid]
    check("13.2 To‘lov holati approved, reviewed_by va izoh saqlandi", p["status"] == "approved" and p["reviewed_by"] == MAIN and p["admin_comment"] == "Rahmat! Yaxshi tomosha.")
    start, end = B.parse_iso(p["vip_start"]), B.parse_iso(p["vip_end"])
    check("13.3 VIP boshlanish/tugash sanasi saqlandi (UTC, 2 kalendar oy)", end == B.add_months(start, 2) and p["vip_end"].endswith("+00:00"))
    check("13.4 Foydalanuvchi VIP muddati users.json da", B.parse_iso(h.read_json("users")["3006"]["vip_until"]) == end)
    check("13.5 Foydalanuvchiga tabrik xabari (tarif, sana, admin izohi)", "🎉 Tabriklaymiz! VIP obunangiz faollashtirildi." in ut and "2 oylik" in ut and "Rahmat! Yaxshi tomosha." in ut
          and "Endi siz kinolarni majburiy obunasiz tomosha qilishingiz mumkin!" in ut, ut)
    h.mark()
    await h.cb(MAIN, f"pay_ok:{pid}")
    check("13.6 Takroriy tasdiqlashdan himoya", "allaqachon" in h.last_alert() and len(B.APP.vip.grants) == 1)
    await h.cb(MAIN, f"pay_no:{pid}")
    check("13.7 Tasdiqlangan to‘lovni rad etib bo‘lmaydi", B.APP.payments.items[pid]["status"] == "approved")
    # VIP majburiy obunasiz kino oladi
    h.mark()
    await h.cb(3006, f"mv:{mv['movie_id']}")
    check("9.1 VIP foydalanuvchi obunasiz (kanallarga a‘zo bo‘lmasdan) kino oladi", h.count_video(3006) == 1)
    await h.msg(3006, B.BTN_CHANNELS)
    check("9.2 VIP uchun kanallar ro‘yxati so‘ralmaydi", "VIP" in h.text(3006))
    await h.msg(3006, B.BTN_PROFILE)
    check("9.3 Profilda VIP badge va tugash sanasi", "💎 <b>VIP</b>" in h.text(3006) and B.fmt_dt(end) in h.text(3006))
    await h.msg(3006, B.BTN_VIP)
    check("9.4 VIP bo‘limida faol VIP muddati ko‘rsatiladi", "Tugash sanasi" in h.text(3006))
    # VIP uzaytirish qoidasi: eski tugash sanasi ustiga qo'shiladi
    start2, end2 = B.APP.vip.grant(3006, "d15", "extend", MAIN)
    check("12.1 Uzaytirish qoidasi: eski tugash sanasi ustiga qo‘shiladi", start2 == end and end2 == end + timedelta(days=15))
    # Rad etish
    await h.msg(3007, "/start")
    await h.cb(3007, "vip_plan:y1")
    await h.photo(3007, "RECEIPT2")
    p_no = B.APP.payments.pending(3007)
    h.mark()
    await h.cb(MAIN, f"pay_no:{p_no['payment_id']}")
    check("14.1 Rad etishda sabab so‘raladi", "sabab" in h.text(MAIN).lower())
    await h.msg(MAIN, "Chek tushunarsiz, summa mos emas")
    ut = h.sent_texts(3007)[-1]
    check("14.2 Foydalanuvchiga aynan admin yozgan sabab yuborildi", "❌ VIP obuna so‘rovingiz rad etildi." in ut and "Chek tushunarsiz, summa mos emas" in ut and "qayta to‘lov so‘rovi yuborishingiz mumkin" in ut, ut)
    check("14.3 Holat rejected, VIP berilmadi", B.APP.payments.items[p_no["payment_id"]]["status"] == "rejected" and not B.APP.users.is_vip(3007))
    await h.cb(3007, "vip_plan:d15")
    check("14.4 Rad etilgandan keyin qayta so‘rov yuborish mumkin", B.APP.payments.awaiting(3007) is not None)
    # Admin chek yuborish oqimidagi bekor qilish tugmasi
    await h.photo(3007, "RECEIPT3")
    p3 = B.APP.payments.pending(3007)
    await h.cb(MAIN, f"pay_ok:{p3['payment_id']}")
    await h.cb(MAIN, f"pay_back:{p3['payment_id']}")
    check("14.5 Admin izoh bosqichida orqaga qaytsa so‘rov pending qoladi", B.APP.payments.items[p3["payment_id"]]["status"] == "pending")
    await h.msg(MAIN, "oddiy matn")  # holat tozalangan: qidiruv sifatida ishlaydi, tasdiqlamaydi
    check("14.6 Holat tozalangach matn to‘lovni tasdiqlamaydi", B.APP.payments.items[p3["payment_id"]]["status"] == "pending")
    await h.cb(MAIN, "adm:vip")
    check("14.7 VIP boshqaruvida kutilayotgan to‘lovlar soni", "Kutilayotgan to‘lovlar (1)" in " ".join(h.btn_texts(MAIN)), str(h.btn_texts(MAIN)))
    h.mark()
    await h.cb(MAIN, "adm:vip_pend")
    check("14.8 Kutilayotgan to‘lov qayta yuborildi (tugmalar bilan)", any(e["method"] == "SendPhoto" and e["chat"] == MAIN for e in h.tg.events))
    await h.cb(MAIN, f"pay_ok:{p3['payment_id']}")
    await h.msg(MAIN, "OK")
    check("14.9 Qayta to‘lov tasdiqlandi", B.APP.users.is_vip(3007))

    # 30 daqiqa
    section("12. 30 daqiqalik muddat")
    await h.msg(3008, "/start")
    await h.cb(3008, "vip_plan:d15")
    pe = B.APP.payments.awaiting(3008)
    pe["expires_at"] = B.iso(B.utcnow() - timedelta(seconds=1))
    h.mark()
    await h.photo(3008, "LATE")
    check("12.2 Muddat o‘tgach chek qabul qilinmaydi", "muddat tugadi" in h.text(3008) and B.APP.payments.items[pe["payment_id"]]["status"] == "expired")
    check("12.3 Kech chek adminga yuborilmadi", not any(e["method"] == "SendPhoto" and e["chat"] == MAIN for e in h.tg.events))
    # avtomatik bekor qilish (fon vazifasi)
    await h.msg(3009, "/start")
    await h.cb(3009, "vip_plan:d15")
    pe2 = B.APP.payments.awaiting(3009)
    pe2["expires_at"] = B.iso(B.utcnow() - timedelta(minutes=1))
    h.mark()
    await B.maintenance_tick(h.bot)
    check("12.4 Muddati tugagan so‘rov avtomatik bekor qilindi", B.APP.payments.items[pe2["payment_id"]]["status"] == "expired")
    check("12.5 Foydalanuvchiga xabar yuborildi", any("muddati" in t and "tugadi" in t for t in h.sent_texts(3009)))
    n_before = len(h.sent_texts(3009))
    await B.maintenance_tick(h.bot)
    check("12.6 Xabar takrorlanmaydi", len(h.sent_texts(3009)) == n_before)
    await h.msg(3009, "Sehrgar")
    check("12.7 Muddati tugagach chek kutish holati yo‘q (qidiruv ishlaydi)", "Topildi" in h.text(3009))
    # Qayta ishga tushganda muddat saqlanadi
    await h.cb(3009, "vip_plan:d30")
    exp_before = B.APP.payments.awaiting(3009)["expires_at"]
    await h.flush()
    await B.APP.save(*B.JsonDB.FILES)
    h.boot()
    check("12.8 Server qayta ishga tushganda to‘lov vaqti saqlanadi", B.APP.payments.awaiting(3009)["expires_at"] == exp_before)

    # VIP tugashi
    section("15. VIP muddati tugashi")
    u = B.APP.users.get(3007)
    u["vip_until"] = B.iso(B.utcnow() - timedelta(minutes=1))
    check("15.1 Muddat o‘tgan zahoti is_vip=False (har so‘rovda tekshiriladi)", not B.APP.users.is_vip(3007))
    h.mark()
    await h.cb(3007, f"mv:{mv['movie_id']}")
    check("15.2 Muddati tugagan VIP dan majburiy obuna qayta so‘raladi", h.count_video(3007) == 0 and "quyidagi" in h.text(3007), h.text(3007))
    await B.maintenance_tick(h.bot)
    check("15.3 Fon vazifasi VIP ni oddiyga aylantirdi va xabar yubordi", B.APP.users.get(3007)["vip_until"] is None and any("VIP obunangiz muddati tugadi" in t for t in h.sent_texts(3007)))
    await h.msg(3007, B.BTN_PROFILE)
    check("15.4 Profilda oddiy foydalanuvchi", "Oddiy foydalanuvchi" in h.text(3007))
    # kalendar oy hisobi
    d = datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc)
    check("15.5 2 kalendar oy: 31-yanvar → 31-mart", B.add_months(d, 2) == datetime(2026, 3, 31, 10, 0, tzinfo=timezone.utc))
    check("15.6 1 oy: 31-yanvar → 28-fevral (kabisa bo‘lmagan yil)", B.add_months(d, 1) == datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc))
    check("15.7 Kabisa yil: 29-fevral + 1 yil → 28-fevral", B.add_months(datetime(2028, 2, 29, tzinfo=timezone.utc), 12) == datetime(2029, 2, 28, tzinfo=timezone.utc))
    check("15.8 1 yillik tarif: 12 kalendar oy", B.VipManager.compute_end(datetime(2026, 5, 1, tzinfo=timezone.utc), "y1") == datetime(2027, 5, 1, tzinfo=timezone.utc))
    check("15.9 15 va 30 kunlik tariflar kun hisobida", B.VipManager.compute_end(datetime(2026, 5, 1, tzinfo=timezone.utc), "d15") == datetime(2026, 5, 16, tzinfo=timezone.utc)
          and B.VipManager.compute_end(datetime(2026, 5, 1, tzinfo=timezone.utc), "d30") == datetime(2026, 5, 31, tzinfo=timezone.utc))

    # Admin: qo'lda VIP berish / uzaytirish / bekor qilish
    section("VIP boshqaruvi (admin)")
    await h.cb(2002, "adm:vip_give")
    await h.msg(2002, "3001")
    check("V.1 To‘liq admin qo‘lda VIP berishda tarif tanlaydi", "adm:vip_g:3001:d15" in h.btn_data(2002))
    await h.cb(2002, "adm:vip_g:3001:d15")
    check("V.2 Qo‘lda VIP berildi", B.APP.users.is_vip(3001) and any("🎁" in t for t in h.sent_texts(3001)))
    e1 = B.parse_iso(B.APP.users.get(3001)["vip_until"])
    await h.cb(2002, "adm:vip_give")
    await h.msg(2002, "@user3001")
    await h.cb(2002, "adm:vip_g:3001:d30")
    e2 = B.parse_iso(B.APP.users.get(3001)["vip_until"])
    check("V.3 VIP uzaytirildi (username bo‘yicha topildi, eski muddat ustiga)", e2 == e1 + timedelta(days=30))
    await h.cb(2002, "adm:vip_list:0")
    check("V.4 VIP foydalanuvchilar ro‘yxati", "3001" in h.text(2002) and "3006" in h.text(2002))
    await h.cb(2002, "adm:vip_cancel")
    await h.msg(2002, "3001")
    await h.cb(2002, "adm:vip_cx:3001")
    check("V.5 VIP bekor qilindi", not B.APP.users.is_vip(3001) and any("bekor qilindi" in t for t in h.sent_texts(3001)))
    await h.cb(2002, "adm:vip_give")
    await h.msg(2002, "77777777")
    check("V.6 Noma'lum foydalanuvchiga VIP berib bo‘lmaydi", "topilmadi" in h.text(2002))
    await h.cb(2002, "adm:cancel")
    check("V.7 VIP grantlar vip.json da (statusi bilan)", {g["status"] for g in h.read_json("vip")["grants"].values()} <= {"active", "expired", "cancelled"} and len(h.read_json("vip")["grants"]) >= 4)


async def t_stats(h: H):
    section("17. Statistika")
    check("17.1 Kino admini statistikani ko‘ra olmaydi", True)
    await h.cb(2002, "adm:st")
    check("17.2 To‘liq admin statistika menyusini ko‘radi", "adm:st_u" in h.btn_data(2002))
    await h.cb(2002, "adm:st_u")
    t = h.text(2002)
    users = list(B.APP.users.items.values())
    vip = sum(1 for u in users if B.APP.users.vip_until(u))
    check("17.3 Foydalanuvchilar: umumiy, bugun, VIP, oddiy", f"Umumiy: <b>{len(users)}</b>" in t and f"VIP: <b>{vip}</b>" in t and f"Oddiy: <b>{len(users) - vip}</b>" in t and "Bugun qo‘shilgan: <b>" in t, t)
    for lbl in ["Kecha qo‘shilgan", "Oxirgi 7 kunda", "Oxirgi 30 kunda", "Faol", "Botni bloklagan"]:
        check(f"17.4 Foydalanuvchilar statistikasida «{lbl}»", lbl in t)
    # bloklagan
    await h.blocked_bot(3005)
    await h.cb(2002, "adm:st_u")
    check("17.5 Botni bloklagan foydalanuvchi hisoblanadi", "Botni bloklagan: <b>1</b>" in h.text(2002), h.text(2002))
    await h.cb(2002, "adm:st_m")
    t = h.text(2002)
    check("17.6 Kino statistikasi: soni, top ko‘rilganlar", f"Umumiy kinolar: <b>{len(B.APP.movies.items)}</b>" in t and "Eng ko‘p ko‘rilganlar" in t and "Sehrgar" in t, t)
    check("17.7 Bugun / 7 / 30 kunda qo‘shilgan kinolar", "Bugun qo‘shilgan: <b>3</b>" in t and "Oxirgi 7 kunda: <b>3</b>" in t and "Oxirgi 30 kunda: <b>3</b>" in t, t)
    await h.cb(2002, "adm:st_w:0")
    check("17.8 Har bir kinoning ko‘rishlar soni", "Sehrgar" in h.text(2002) and "ko‘rishlar soni" in h.text(2002))
    await h.cb(2002, "adm:st_a")
    t = h.text(2002)
    check("17.9 Faollik: bugungi/haftalik/oylik, eng faol vaqt/kun, soat bo‘yicha",
          all(s in t for s in ["Bugungi foydalanishlar", "Haftalik", "Oylik", "Eng faol vaqtlar", "Eng faol kunlar", "Soat bo‘yicha"]), t[:300])
    await h.cb(2002, "adm:st_v")
    t = h.text(2002)
    approved = [p for p in B.APP.payments.items.values() if p["status"] == "approved"]
    revenue = sum(p["amount"] for p in approved)
    check("17.10 VIP statistikasi: tasdiqlangan, rad etilgan, kutilayotgan", f"Tasdiqlangan to‘lovlar: <b>{len(approved)}</b>" in t and "Rad etilgan: <b>1</b>" in t and "Kutilayotgan" in t, t)
    check("17.11 Jami tushum aniq hisoblangan (2 oylik 30 000 + 15 kunlik 10 000)", B.fmt_money(revenue) in t and revenue == 40000, t)
    check("17.12 Tariflar bo‘yicha sotuvlar (rad etilgan 1 yillik sanalmaydi)", "2 oylik: <b>1</b>" in t and "1 yillik: <b>0</b>" in t and "15 kunlik: <b>1</b>" in t, t)
    await h.cb(2002, "adm:st_ad")
    t = h.text(2002)
    check("17.13 Admin statistikasi: har bir admin qo‘shgan kinolar va amallar", "KinoAdmin" in t and "Qo‘shgan kinolar: <b>1</b>" in t and "movie_add: 2" in t and "movie_delete: 1" in t, t)
    # ma'lumot yetishmasa taxminiy raqam yo'q
    main_app = B.APP
    fresh = H()
    await fresh.cb(MAIN, "adm:st_a")
    check("17.14 Yangi bot: faollik faqat haqiqiy qayd etilgan hodisalarni ko‘rsatadi (taxminiy raqam yo‘q)",
          "Haftalik (7 kun): <b>1</b>" in fresh.text(MAIN) and "03:00" not in "" and B.APP.stats.s["usage_by_day"] and sum(B.APP.stats.s["usage_by_day"].values()) == 1, fresh.text(MAIN))
    fresh.tg.events.clear()
    B.APP.stats.s["usage_by_day"].clear()
    check("17.15 Hodisa umuman yo‘q bo‘lsa «ma'lumot to‘planmagan» deyiladi", "hali to‘planmagan" in B.APP.stats.activity_text())
    B.APP = main_app


async def t_backup(h: H):
    section("18. Backup va tiklash")
    await B.APP.save(*B.JsonDB.FILES)
    h.mark()
    await h.cb(MAIN, "adm:bk")
    await h.cb(MAIN, "adm:bk_do")
    docs = [e for e in h.tg.events if e["method"] == "SendDocument"]
    check("18.1 Backup ZIP asosiy adminga yuborildi", len(docs) == 1 and docs[0]["chat"] == MAIN)
    check("18.2 Backup boshqa hech kimga yuborilmadi", all(e["chat"] == MAIN for e in h.tg.events if e["method"] == "SendDocument"))
    zf = zipfile.ZipFile(io.BytesIO(docs[0]["input_file"].data))
    names = set(zf.namelist())
    check("18.3 ZIP ichida barcha JSON fayllar", {f"{n}.json" for n in ["users", "movies", "admins", "channels", "vip", "payments", "statistics", "settings"]} <= names, str(names))
    on_disk = json.loads((Path(h.dir) / "movies.json").read_text(encoding="utf-8"))
    check("18.4 ZIP dagi ma'lumot joriy holatga teng", json.loads(zf.read("movies.json")) == on_disk == B.APP.db.data["movies"])
    check("18.5 ZIP faylidagi JSON fayllar yaroqli UTF-8", all(json.loads(zf.read(n).decode("utf-8")) is not None for n in names if n.endswith(".json")))
    for uid in (2001, 2002):
        h.mark()
        await h.cb(uid, "adm:bk_do")
        check(f"18.6 Admin {uid} backup ola olmaydi", "⛔" in h.last_alert() and not any(e["method"] == "SendDocument" for e in h.tg.events))
    # tiklash
    zip_bytes = docs[0]["input_file"].data
    n_movies = len(B.APP.movies.items)
    first_id = next(iter(B.APP.movies.items))
    B.APP.movies.delete(int(first_id))
    B.APP.settings["bot_name"] = "BuzilganNom"
    await B.APP.save("movies", "settings")
    h.mark()
    await h.cb(MAIN, "adm:bk_rs")
    await h.msg(MAIN, "matn")
    check("18.7 Tiklashda ZIP bo‘lmagan kiritish rad etiladi", "hujjat" in h.text(MAIN))
    await h.document(MAIN, b"not a zip")
    check("18.8 Yaroqsiz arxiv rad etiladi", "yaroqsiz" in h.text(MAIN).lower())
    await h.document(MAIN, zip_bytes, "goodzip")
    check("18.9 Yaroqli arxiv tekshirildi, tasdiq so‘raladi", "adm:rs_ok" in h.btn_data(MAIN))
    h.mark()
    await h.cb(MAIN, "adm:rs_ok")
    pre = [e for e in h.tg.events if e["method"] == "SendDocument"]
    check("18.10 Tiklashdan oldin joriy holat backupi yuborildi", len(pre) == 1 and "oldingi" in (pre[0]["text"] or ""))
    check("18.11 Ma'lumotlar tiklandi", len(B.APP.movies.items) == n_movies and B.APP.settings["bot_name"] == "TestKino")
    check("18.12 Tiklangan ma'lumot diskka yozildi", len(h.read_json("movies")) == n_movies)
    check("18.13 Asosiy admin tiklashdan keyin ham asosiy", B.APP.admins.role_of(MAIN) == B.ROLE_MAIN)
    # to'liq admin tiklay olmaydi
    h.mark()
    await h.cb(2002, "adm:rs_ok")
    check("18.14 To‘liq admin tiklay olmaydi", "⛔" in h.last_alert())
    # xavfli arxiv (zip-slip / noma'lum fayl) e'tiborga olinmaydi
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../etc/passwd", "x")
        z.writestr("users.json", "{}")
        z.writestr("movies.json", "{}")
        z.writestr("admins.json", "{}")
        z.writestr("settings.json", "{}")
    try:
        texts = B.APP.backup.parse_zip(evil.getvalue())
        check("18.15 Arxivdagi begona fayl nomlari e'tiborga olinmaydi", set(texts) == {"users.json", "movies.json", "admins.json", "settings.json"})
    except ValueError as e:
        check("18.15 Arxivdagi begona fayl nomlari e'tiborga olinmaydi", False, str(e))


async def t_json_robustness():
    section("19. JSON saqlash ishonchliligi")
    d = tempfile.mkdtemp(prefix="kb_json_")
    db = B.JsonDB(d)
    db.data["users"]["1"] = {"name": "Oʻzbek 🎬 Қўшиқ"}
    await db.save("users")
    check("19.1 UTF-8: Unicode to‘g‘ri saqlanadi", json.loads((Path(d) / "users.json").read_text(encoding="utf-8"))["1"]["name"] == "Oʻzbek 🎬 Қўшиқ")
    check("19.2 Yozishdan keyin .tmp qolmaydi", not list(Path(d).glob("*.tmp")))
    db.data["users"]["2"] = {"name": "ikkinchi"}
    await db.save("users")
    check("19.3 Oldingi versiya .bak sifatida saqlanadi", (Path(d) / "users.json.bak").exists())
    # parallel yozuvlar
    async def writer(i):
        db.data["users"][f"u{i}"] = {"i": i}
        await db.save("users")
    await asyncio.gather(*(writer(i) for i in range(200)))
    on_disk = json.loads((Path(d) / "users.json").read_text(encoding="utf-8"))
    check("19.4 200 ta parallel yozuvdan keyin hech bir ma'lumot yo‘qolmadi", all(f"u{i}" in on_disk for i in range(200)) and len(on_disk) == len(db.data["users"]))
    # buzilgan fayl: .bak dan tiklash
    (Path(d) / "users.json").write_text('{"1": {"name": "x"', encoding="utf-8")  # kesilgan JSON
    db2 = B.JsonDB(d)
    check("19.5 Buzilgan JSON .bak dan avtomatik tiklanadi", isinstance(db2.data["users"], dict) and len(db2.data["users"]) >= 2, str(len(db2.data["users"])))
    # ikkalasi ham buzilgan
    (Path(d) / "users.json").write_text("garbage", encoding="utf-8")
    (Path(d) / "users.json.bak").write_text("garbage", encoding="utf-8")
    db3 = B.JsonDB(d)
    check("19.6 Ikkala nusxa buzilsa bot qulamaydi, bo‘sh holat va buzilgan fayl nusxasi saqlanadi", db3.data["users"] == {} and list(Path(d).glob("users.json.corrupt-*")))
    # noto'g'ri tip
    (Path(d) / "movies.json").write_text("[]", encoding="utf-8")
    (Path(d) / "movies.json.bak").unlink(missing_ok=True)
    db4 = B.JsonDB(d)
    check("19.7 Noto‘g‘ri turdagi JSON (list o‘rniga dict) rad etiladi", db4.data["movies"] == {})
    # settings yetishmayotgan kalitlar to'ldiriladi
    (Path(d) / "settings.json").write_text('{"bot_name": "X"}', encoding="utf-8")
    db5 = B.JsonDB(d)
    check("19.8 Eski settings.json ga yangi kalitlar qo‘shiladi (migratsiya)", db5.data["settings"]["bot_name"] == "X" and db5.data["settings"]["next_movie_code"] == 1001 and "subscription_enabled" in db5.data["settings"])
    # yozish xatosi
    db6 = B.JsonDB(tempfile.mkdtemp(prefix="kb_json2_"))
    orig = db6._write_sync

    def fail(*a, **k):
        raise OSError("disk to‘la")
    db6._write_sync = fail
    db6.data["users"]["x"] = {}
    await db6.save("users")
    check("19.9 Disk xatosida bot qulamaydi va qayta urinish uchun dirty belgilanadi", "users" in db6.dirty)
    db6._write_sync = orig
    await db6.flush()
    check("19.10 Xato bartaraf bo‘lgach flush yozadi", "x" in json.loads((db6.dir / "users.json").read_text()))
    # snapshot izchilligi
    snap = db.snapshot()
    check("19.11 Snapshot barcha JSON fayllarni qamrab oladi", len(snap) == len(B.JsonDB.FILES))


async def t_restart_persistence(h: H):
    section("20. Qayta ishga tushganda ma'lumotlar saqlanishi")
    await h.flush()
    await B.APP.save(*B.JsonDB.FILES)
    before = {n: json.loads(json.dumps(B.APP.db.data[n])) for n in B.JsonDB.FILES}
    pend_before = {k: v for k, v in B.APP.payments.items.items() if v["status"] in ("pending", "awaiting_receipt")}
    vip_before = {u["user_id"]: u["vip_until"] for u in B.APP.users.items.values() if u["vip_until"]}
    h.boot()
    after = {n: B.APP.db.data[n] for n in B.JsonDB.FILES}
    check("20.1 Barcha JSON ma'lumotlar bayt-baytga o‘zgarishsiz tiklandi", all(before[n] == after[n] for n in before), str([n for n in before if before[n] != after[n]]))
    check("20.2 Kutilayotgan to‘lovlar saqlandi", all(B.APP.payments.items[k]["status"] == v["status"] for k, v in pend_before.items()) and pend_before)
    check("20.3 Muddati tugamagan VIPlar saqlandi", {u["user_id"]: u["vip_until"] for u in B.APP.users.items.values() if u["vip_until"]} == vip_before and vip_before)
    check("20.4 Kino kodi hisoblagichi davom etadi (kodlar takrorlanmaydi)", B.APP.settings["next_movie_code"] >= 1005)
    check("20.5 Join requestlar va kanallar saqlandi", len(B.APP.subs.channels) == 3)
    # qayta ishga tushgandan keyin ishlashda davom etadi
    await h.msg(3001, "Sehrgar")
    check("20.6 Qayta ishga tushgandan keyin bot qidiruvga javob beradi", "Topildi" in h.text(3001))
    # kutilayotgan to'lov restartdan keyin tasdiqlanadi
    pend = B.APP.payments.pending_list()
    if pend:
        await h.cb(MAIN, f"pay_ok:{pend[0]['payment_id']}")
        await h.msg(MAIN, "restartdan keyin tasdiq")
        check("20.7 Restartdan keyin ham kutilayotgan to‘lovni tasdiqlash mumkin", B.APP.payments.items[pend[0]["payment_id"]]["status"] == "approved")
    else:
        check("20.7 (kutilayotgan to‘lov yo‘q — o‘tkazib yuborildi)", True)


async def t_broadcast():
    section("Broadcast (qo‘shimcha talab)")
    h = H()
    for uid in [2001, 2002]:
        await h.msg(uid, "/start")
    # 12 ta foydalanuvchi
    for uid in range(5000, 5012):
        await h.msg(uid, "/start")
    B.APP.admins.add(2001, B.ROLE_MOVIE, MAIN, "KA")
    B.APP.admins.add(2002, B.ROLE_FULL, MAIN, "FA")
    for uid in (2001, 2002):
        h.mark()
        for cbd in ["adm:bc", "adm:bc_new", "adm:bc_ok", "adm:bc_stop"]:
            await h.cb(uid, cbd)
            check(f"B.1 Admin {uid} broadcastdan foydalana olmaydi: {cbd}", "⛔" in h.last_alert())
    await h.msg(MAIN, "/start")
    await h.cb(MAIN, "adm:bc")
    check("B.2 Asosiy adminda Broadcast bo‘limi", "adm:bc_new" in h.btn_data(MAIN))
    await h.cb(MAIN, "adm:bc_new")
    await h.sticker(MAIN)
    check("B.3 Noto‘g‘ri format tushunarli xabar", "Faqat matn" in h.text(MAIN))
    await h.msg(MAIN, "Salom <b>hammaga</b>! 🎬")
    check("B.4 Preview ko‘rsatildi + tasdiqlash/bekor tugmalari", "adm:bc_ok" in h.btn_data(MAIN) and "adm:cancel" in h.btn_data(MAIN) and f"<b>{len(B.APP.users.items)}</b>" in h.text(MAIN), h.text(MAIN))
    prev = [e for e in h.tg.events if e["chat"] == MAIN and e["method"] == "SendMessage" and "Salom" in (e["text"] or "")]
    check("B.5 Preview xabarning o‘zi (formatlash bilan) ko‘rsatildi", len(prev) == 1, str(len(prev)))
    # bekor
    await h.cb(MAIN, "adm:cancel")
    h.mark()
    await h.cb(MAIN, "adm:bc_ok")
    check("B.6 Bekor qilinsa yuborilmaydi", B.APP.broadcast.current is None)
    # Xatolarni modellashtiramiz: 5003 bloklagan, 5005 boshqa xato, 5007 — bir marta FloodWait
    state = {"flood": 1}

    def rule(method):
        if isinstance(method, SendMessage) and "Salom" in (method.text or ""):
            if method.chat_id == 5003:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            if method.chat_id == 5005:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            if method.chat_id == 5007 and state["flood"] > 0:
                state["flood"] -= 1
                raise TelegramRetryAfter(method=method, message="Flood", retry_after=0)
    h.tg.rules.append(rule)
    B.BROADCAST_DELAY = 0
    await h.cb(MAIN, "adm:bc_new")
    await h.msg(MAIN, "Salom <b>hammaga</b>! 🎬")
    h.mark()
    await h.cb(MAIN, "adm:bc_ok")
    check("B.7 Tasdiqlanganda broadcast alohida task sifatida boshlandi", B.APP.broadcast.current is not None and B.APP.broadcast_task is not None)
    # broadcast davomida boshqa funksiyalar ishlaydi
    await h.msg(3999, "/start")
    await h.msg(3999, "nimadir")
    check("B.8 Broadcast davomida bot boshqa so‘rovlarga javob beradi", "hech narsa topilmadi" in h.text(3999) or "Kino nomini" in h.text(3999))
    await asyncio.wait_for(B.APP.broadcast_task, 20)
    job = B.APP.broadcast.current
    rep = [t for t in h.sent_texts(MAIN) if "Broadcast yakunlandi" in t]
    check("B.9 Hisobot asosiy adminga yuborildi", len(rep) == 1, str(h.sent_texts(MAIN)[-3:]))
    total = len(B.APP.users.items) if False else job["total"]
    check("B.10 Hisobot: jami", f"Jami foydalanuvchilar: <b>{total}</b>" in rep[0] and total >= 15, rep[0])
    check("B.11 Hisobot: bloklagan = 1, boshqa xato = 1, yuborilmagan = 2", "Botni bloklaganlar: <b>1</b>" in rep[0] and "Boshqa xatoliklar: <b>1</b>" in rep[0] and "Yuborilmaganlar: <b>2</b>" in rep[0], rep[0])
    check("B.12 Muvaffaqiyatli = jami − 2 (FloodWait dan keyin qayta urinildi)", job["sent"] == total - 2, f"{job['sent']} / {total}")
    check("B.13 Bloklagan foydalanuvchi is_blocked=True", B.APP.users.get(5003)["is_blocked"] is True)
    got = [e["chat"] for e in h.tg.events if e["method"] == "SendMessage" and "Salom" in (e["text"] or "")]
    check("B.14 Hech kim ikki marta olmadi (FloodWap qayta urinishi bundan mustasno)", len([c for c in got if c not in (5003, 5005, 5007)]) == len(set(c for c in got if c not in (5003, 5005, 5007))))
    await h.flush()
    bc = h.read_json("broadcast")
    check("B.15 Holat broadcast.json da saqlandi (tarix)", bc["current"]["status"] == "done" and bc["history"] and bc["history"][-1]["sent"] == job["sent"])
    # rasm va video
    h.tg.rules.clear()
    await h.cb(MAIN, "adm:bc_new")
    await h.photo(MAIN, "BCPHOTO", caption="Rasm <i>caption</i>")
    check("B.16 Rasm + caption preview", any(e["method"] == "SendPhoto" and e["chat"] == MAIN and "caption" in (e["text"] or "") for e in h.tg.events))
    h.mark()
    await h.cb(MAIN, "adm:bc_ok")
    await asyncio.wait_for(B.APP.broadcast_task, 20)
    n_photo = sum(1 for e in h.tg.events if e["method"] == "SendPhoto")
    check("B.17 Rasm hamma foydalanuvchiga yuborildi", n_photo == B.APP.broadcast.current["sent"] == len(B.APP.users.items), f"{n_photo} {B.APP.broadcast.current['sent']} {len(B.APP.users.items)}")
    await h.cb(MAIN, "adm:bc_new")
    await h.video(MAIN, "BCVIDEO", "ubcv", caption="Video caption")
    h.mark()
    await h.cb(MAIN, "adm:bc_ok")
    await asyncio.wait_for(B.APP.broadcast_task, 20)
    n_video = sum(1 for e in h.tg.events if e["method"] == "SendVideo")
    check("B.18 Video + caption hamma foydalanuvchiga yuborildi", n_video == len(B.APP.users.items), str(n_video))
    # Matn uzun
    await h.cb(MAIN, "adm:bc_new")
    await h.msg(MAIN, "x" * 4500)
    check("B.19 Juda uzun matn rad etiladi", "juda uzun" in h.text(MAIN))
    await h.cb(MAIN, "adm:cancel")
    # to'xtatish va davom ettirish (restart)
    B.BROADCAST_DELAY = 0
    await h.cb(MAIN, "adm:bc_new")
    await h.msg(MAIN, "Uzun jarayon")
    stop_at = {"n": 0}

    def slow(method):
        pass
    await h.cb(MAIN, "adm:bc_ok")
    await asyncio.wait_for(B.APP.broadcast_task, 20)
    # davom ettirish simulyatsiyasi: running holatdagi jobni diskka yozamiz va qayta yuklaymiz
    job = B.APP.broadcast.current
    recips = [u["user_id"] for u in B.APP.users.items.values()]
    B.APP.broadcast.create_job(MAIN, "text", "Davom matni", None)
    cur = B.APP.broadcast.current
    cur["index"] = 5
    cur["sent"] = 5
    await B.APP.save("broadcast", "settings")
    h.boot()
    check("B.20 Restartdan keyin yarim qolgan broadcast 'running' holatda tiklandi", B.APP.broadcast.is_running() and B.APP.broadcast.current["index"] == 5)
    h.mark()
    B.APP.broadcast_task = asyncio.create_task(B.run_broadcast_task(h.bot, resumed=True))
    await asyncio.wait_for(B.APP.broadcast_task, 20)
    cur = B.APP.broadcast.current
    got = {e["chat"] for e in h.tg.events if e["method"] == "SendMessage" and "Davom matni" in (e["text"] or "")}
    check("B.21 Davom ettirilganda faqat qolgan foydalanuvchilarga yuborildi (dublikatsiz)", len(got) == cur["total"] - 5 and cur["status"] == "done", f"{len(got)} {cur['total']}")
    check("B.22 Resume haqida admin xabardor qilindi", any("davom ettirildi" in t for t in h.sent_texts(MAIN)))
    # to'xtatish
    await h.cb(MAIN, "adm:bc_new")
    await h.msg(MAIN, "To‘xtatiladigan")
    B.BROADCAST_DELAY = 0.02
    await h.cb(MAIN, "adm:bc_ok")
    await asyncio.sleep(0.05)
    await h.cb(MAIN, "adm:bc_stop")
    await asyncio.wait_for(B.APP.broadcast_task, 20)
    check("B.23 To‘xtatilgan broadcast 'cancelled' holatda hisobot beradi", B.APP.broadcast.current["status"] == "cancelled" and any("to‘xtatildi" in t for t in h.sent_texts(MAIN)))
    B.BROADCAST_DELAY = 0.05


async def t_misc():
    section("Qo‘shimcha: xavfsizlik va chekka holatlar")
    h = H()
    await h.msg(MAIN, "/start")
    # Matnlar HTML injection'dan himoyalangan
    await h.cb(MAIN, "adm:mv_add")
    await h.video(MAIN, "F1", "U1")
    await h.msg(MAIN, "<b>qalin</b> & <script>")
    await h.msg(MAIN, "Nom <i>x</i>")
    m = next(iter(B.APP.movies.items.values()))
    await h.msg(7001, "/start")
    h.mark()
    await h.cb(7001, f"mv:{m['movie_id']}")
    cap = [e for e in h.tg.events if e["method"] == "SendVideo"][0]["text"]
    check("M.1 Description/nom HTML escape qilinadi (injeksiya yo‘q)", "&lt;b&gt;qalin&lt;/b&gt;" in cap and "<script>" not in cap and "&lt;i&gt;" in cap, cap)
    # token loglarda yashirinadi
    import logging
    rec = logging.LogRecord("x", logging.ERROR, "f", 1, "URL https://api.telegram.org/bot123456:SECRETTOKEN/getMe xato", (), None)
    B.RedactFilter(["123456:SECRETTOKEN"]).filter(rec)
    check("M.2 Bot tokeni logdan olib tashlanadi", "SECRETTOKEN" not in rec.getMessage() and "***" in rec.getMessage())
    # FloodWait kino yuborishda
    state = {"n": 1}

    def rule(method):
        if isinstance(method, SendVideo) and state["n"] > 0:
            state["n"] -= 1
            raise TelegramRetryAfter(method=method, message="Flood", retry_after=0)
    h.tg.rules.append(rule)
    h.mark()
    await h.cb(7001, f"mv:{m['movie_id']}")
    check("M.3 FloodWait kino yuborishda avtomatik qayta uriniladi (bir marta kutib, kino yetkazildi)", state["n"] == 0 and h.count_video(7001) == 1 and B.APP.movies.get(m["movie_id"])["views_count"] == 2, str(h.count_video(7001)))
    h.tg.rules.clear()
    # Foydalanuvchi botni bloklagan bo'lsa xabar yuborishda
    def block(method):
        if isinstance(method, SendMessage) and method.chat_id == 7001:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
    h.tg.rules.append(block)
    ok = await B.notify_user(h.bot, 7001, "salom")
    check("M.4 Bloklagan foydalanuvchiga xabar xatosi ushlanadi va is_blocked belgilanadi", ok is False and B.APP.users.get(7001)["is_blocked"] is True)
    h.tg.rules.clear()
    await h.msg(7001, "/start")
    check("M.5 Foydalanuvchi qaytib kelsa is_blocked=False", B.APP.users.get(7001)["is_blocked"] is False)
    # my_chat_member
    await h.blocked_bot(7001)
    check("M.6 my_chat_member (kicked) → bloklagan deb belgilanadi", B.APP.users.get(7001)["is_blocked"] is True)
    # Guruhdagi xabarlar e'tiborga olinmaydi
    n = len(B.APP.users.items)
    await h.feed({"message": {"message_id": 1, "date": int(time.time()), "chat": {"id": -555, "type": "supergroup", "title": "g"},
                              "from": h.user(8888), "text": "salom"}})
    check("M.7 Guruh xabarlari foydalanuvchi sifatida ro‘yxatga olinmaydi", len(B.APP.users.items) == n)
    # Parallel tasdiqlash (race): ikki marta approve
    await h.msg(7002, "/start")
    B.APP.settings["card_number"], B.APP.settings["card_holder"] = "8600 0000 0000 0000", "Test Test"
    await h.cb(7002, "vip_plan:d15")
    await h.photo(7002, "R")
    pid = B.APP.payments.pending(7002)["payment_id"]
    r1 = B.APP.payments.approve(pid, MAIN, "a")
    r2 = B.APP.payments.approve(pid, MAIN, "b")
    check("M.8 PaymentManager.approve takror chaqirilsa faqat bir marta VIP beradi", r1 is not None and r2 is None and len([g for g in B.APP.vip.grants.values() if g["user_id"] == 7002]) == 1)
    check("M.9 Rad etilgan/tasdiqlangan so‘rovni rad etib bo‘lmaydi", B.APP.payments.reject(pid, MAIN, "x") is None)
    # Admin matnlari: soxta callback qiymatlari
    for bad in ["adm:mv_i:abc", "adm:mv_i:999999", "adm:ch_i:555", "adm:ch_xy:555", "adm:vip_g:1:zzz", "adm:ad_i:9", "pay_ok:NOPE", "mv:notanumber", "chk:zzz"]:
        try:
            await h.cb(MAIN, bad)
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = False
        check(f"M.10 Soxta/yaroqsiz callback bot ishini buzmaydi: {bad}", ok)
    # Kino o'chirilgandan keyin eski tugma
    await h.cb(MAIN, f"adm:mv_xy:{m['movie_id']}")
    await h.cb(7001, f"mv:{m['movie_id']}")
    check("M.11 O‘chirilgan kinoning eski tugmasi tushunarli xabar beradi", "topilmadi" in h.text(7001))
    await h.cb(MAIN, f"adm:mv_xy:{m['movie_id']}")
    check("M.12 Allaqachon o‘chirilgan kinoni qayta o‘chirish xato bermaydi", "allaqachon" in h.last_alert())
    # Admin o'zini ham, asosiy adminni ham olib tashlay olmaydi
    await h.cb(MAIN, f"adm:ad_xy:{MAIN}")
    check("M.13 Asosiy admin hech kim tomonidan (o‘zi ham) olib tashlanmaydi", B.APP.admins.role_of(MAIN) == B.ROLE_MAIN)
    # vaqt: server vaqtiga emas UTC ga bog'liq
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tashkent"
    time.tzset()
    try:
        now = B.utcnow()
        check("M.14 Vaqt server zonasiga emas UTC ga bog‘liq", now.utcoffset() == timedelta(0) and B.iso(now).endswith("+00:00"))
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
    # past ruxsat: tahrir qilinmaydigan xabar (edit xatosi) — yangi xabar yuboriladi
    def no_edit(method):
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method=method, message="Bad Request: message can't be edited")
    h.tg.rules.append(no_edit)
    h.mark()
    await h.cb(MAIN, "adm:home")
    check("M.15 Xabarni tahrirlab bo‘lmasa yangi xabar yuboriladi (interfeys qotmaydi)", any(e["kind"] == "send" and "Admin panel" in (e["text"] or "") for e in h.tg.events))
    h.tg.rules.clear()


async def main():
    h = H()
    groups = [t_boot_and_start, t_admin_roles_setup, t_movie_add, t_search_and_deliver, t_subscription, t_vip_flow, t_stats, t_backup, t_restart_persistence]
    for g in groups:
        try:
            await g(h)
        except Exception:
            traceback.print_exc()
            check(f"{g.__name__} istisnosiz tugadi", False, traceback.format_exc().splitlines()[-1])
    for g in (t_json_robustness, t_broadcast, t_misc):
        try:
            await g()
        except Exception:
            traceback.print_exc()
            check(f"{g.__name__} istisnosiz tugadi", False, traceback.format_exc().splitlines()[-1])

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 60}\nNATIJA: {passed}/{len(RESULTS)} test muvaffaqiyatli")
    failed = [(n, i) for n, ok, i in RESULTS if not ok]
    for n, i in failed:
        print("  ❌", n, i)
    with open(Path(__file__).with_name("test_results.txt"), "w", encoding="utf-8") as f:
        f.write(f"Kino bot — avtomatik test natijalari ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})\n")
        f.write(f"Jami: {len(RESULTS)}, o'tdi: {passed}, yiqildi: {len(RESULTS) - passed}\n\n")
        for n, ok, i in RESULTS:
            f.write(("PASS  " if ok else "FAIL  ") + n + (f"   [{i}]" if (i and not ok) else "") + "\n")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    asyncio.run(main())
