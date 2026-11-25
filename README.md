# Telegram Kino Bot (Aiogram 3, bitta `bot.py`, JSON saqlash)

## 1. O‘rnatish va ishga tushirish
```bash
python3 -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # BOT_TOKEN va MAIN_ADMIN_ID ni to‘ldiring
python bot.py
```
Python 3.10+ kerak. Birinchi ishga tushishda `data/` papkasida barcha JSON fayllar avtomatik yaratiladi
(`users, movies, admins, channels, vip, payments, statistics, settings` + `join_requests, broadcast`).
Loglar `data/bot.log` da (token avtomatik yashiriladi).

**Doimiy ishlatish (Linux, systemd)** — `/etc/systemd/system/kinobot.service`:
```ini
[Service]
WorkingDirectory=/opt/kinobot
ExecStart=/opt/kinobot/venv/bin/python bot.py
Restart=always
User=kinobot
[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now kinobot`. Faqat **bitta nusxa** ishga tushiring (bir token — bitta jarayon).

## 2. BotFather sozlamalari
1. `/newbot` — nom va username bering, tokenni `.env` ga yozing.
2. `/setdescription`, `/setabouttext` — ixtiyoriy (o‘zbekcha).
3. `/setprivacy` — bot faqat shaxsiy chatda ishlaydi, sozlash shart emas.
4. `/setjoingroups` — Disable (tavsiya).
5. Buyruqlarni bot o‘zi o‘rnatadi (`/start`, `/cancel`).
Asosiy admin `/admin` yoki «🛠 Admin panel» tugmasi orqali panelga kiradi.

## 3. Botni kanal/guruhga administrator qilish (majburiy obuna)
**Ochiq kanal/guruh:** botni kanalga qo‘shing → Administrators → botga admin huquqi bering
(a’zolarni ko‘rish uchun admin bo‘lishi kifoya). Admin panel → 📢 Majburiy obuna → «Ochiq kanal» → `@username` yoki chat ID.

**Private kanal/guruh:**
1. Botni kanalga administrator qiling, **«Foydalanuvchilarni taklif qilish» (Invite users)** huquqi yoqilgan bo‘lsin.
2. Chat ID ni bilib oling (kanaldan xabarni @getidsbot ga forward qiling), format: `-100xxxxxxxxxx`.
3. Panel → «Private kanal» → chat ID → **«🤖 So‘rovli havolani avtomatik yaratish»** (join request yoqilgan havola) yoki o‘zingiz yaratgan havolani yuboring
   (kanalda: Invite links → «Request admin approval» yoqilgan bo‘lsin).
4. Foydalanuvchi havola orqali so‘rov yuboradi → bot buni alohida qayd etadi, lekin **a’zo hisoblamaydi**.
   Kanal admini so‘rovni tasdiqlagach, bot `getChatMember` orqali haqiqiy a’zolikni tekshiradi va kino yuboriladi.
> Telegram faqat kanal admini bo‘lgan bot uchun a’zolikni tekshirishga ruxsat beradi. Bot admin bo‘lmay qolsa,
> foydalanuvchi qamalib qolmaydi (kanal tekshiruvdan o‘tkazib yuboriladi) va asosiy adminga ogohlantirish yuboriladi.

## 4. Admin rollari
| Huquq | 1-tur Kino admini | 2-tur To‘liq admin | 3-tur Asosiy admin |
|---|---|---|---|
| Kino qo‘shish / o‘z kinolarini ko‘rish va o‘chirish | ✅ | ✅ | ✅ |
| Barcha kinolar | ❌ | ✅ | ✅ |
| Majburiy obuna, Statistika, VIP boshqaruvi (qo‘lda berish/uzaytirish/bekor) | ❌ | ✅ | ✅ |
| Admin boshqaruvi | ❌ | faqat 1-tur adminlar | ✅ (hamma, rol o‘zgartirish) |
| To‘lovni tasdiqlash/rad etish, Sozlamalar (karta), Backup/Tiklash, **Broadcast** | ❌ | ❌ | ✅ |

Asosiy admin ID `.env` dagi `MAIN_ADMIN_ID` da; uni hech kim olib tashlay olmaydi. Huquqlar har bir callback va handler ichida server tomonida qayta tekshiriladi.

## 5. Qarorlar (aniqlik uchun)
- **Kino qo‘shish:** video → description → nom (video captioni ishlatilmaydi) → avtomatik kod (1001, 1002, …).
- **VIP uzaytirish qoidasi:** faol VIP bo‘lsa yangi muddat **eski tugash sanasi ustiga** qo‘shiladi, aks holda hozirdan (UTC). 2 oy / 1 yil — kalendar oy (31-yanvar + 1 oy = 28/29-fevral).
- **To‘lov faqat qo‘lda** tasdiqlanadi; avtomatik tasdiqlash yo‘q. Chek vaqti 30 daqiqa (JSON da saqlanadi, restartdan keyin ham amal qiladi).
- **Broadcast:** matn / rasm+caption / video+caption, preview → tasdiq; alohida task (bot ishlashda davom etadi), tezlik ~20 xabar/s, FloodWait kutiladi; holat `broadcast.json` da, restartdan keyin qolgan joyidan davom etadi; yakunda hisobot.
- **Backup:** barcha JSON → ZIP → faqat asosiy adminga. Tiklash: ZIP yuboriladi, tasdiqdan oldin joriy holat backupi yuboriladi.
- FSM holati xotirada (MemoryStorage): restartda admin jarayonlari (masalan, yarim qolgan kino qo‘shish) qaytadan boshlanadi; VIP, to‘lov, broadcast holati esa JSON da saqlanadi.

## 6. Testlar
```bash
python test_bot.py        # natija test_results.txt ga ham yoziladi
```
Testlar haqiqiy Dispatcher/handler/FSM ni soxta Telegram sessiyasi bilan ishga tushiradi (token va internet kerak emas).
# kino_bot
