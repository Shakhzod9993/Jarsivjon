# Vercel'ga o'rnatish (o'zbekcha)

Bot kodi (`bot/`) o'zgarmagan. Vercel uchun faqat yupqa qatlam qo'shilgan:

- `api/webhook.py` – Telegram xabarlarni shu yerga yuboradi
- `api/setup.py` – bir martalik sozlash (webhook + baza)
- `api/cron.py` – kunlik qarz eslatmalari (Vercel Cron)
- `serverless/` – SQLite bazani Supabase Storage'da saqlash va FSM holati

## 1. Supabase
https://supabase.com → New project. **SQL yozish shart emas.** Faqat:
- Project URL → `SUPABASE_URL` (`https://<id>.supabase.co`)
- Project Settings → API Keys → Secret key (`sb_secret_...`) → `SUPABASE_SERVICE_ROLE_KEY`

## 2. Vercel → Settings → Environment Variables

| Nomi | Qiymat |
|---|---|
| `BOT_TOKEN` | BotFather'dan **yangi** token |
| `OWNER_ID` | Sizning Telegram ID |
| `CARD_NUMBER` | Karta raqami (bir nechta bo'lsa `\n` bilan) |
| `SUPABASE_URL` | `https://<id>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | `sb_secret_...` |
| `CRON_SECRET` | istalgan uzun tasodifiy satr |
| `TIMEZONE` | `Asia/Tashkent` |

Ixtiyoriy: `DELETE_TRIGGER_MESSAGE`, `COOLDOWN_SECONDS`, `NOTIFICATION_HOUR`, `NOTIFICATION_MINUTE`, `LOG_LEVEL`.

## 3. Deploy → keyin bir marta oching

```
https://<loyiha>.vercel.app/api/setup
```
Javobda `"ok": true` va `"webhook"` ichida sizning manzilingiz bo'lishi kerak.

## Kunlik eslatma vaqti
`vercel.json` → `"schedule": "0 7 * * *"` (UTC) = Toshkent vaqti 12:00.
Boshqa soat kerak bo'lsa: Toshkent soati minus 5. Masalan 18:00 → `"0 13 * * *"`.
Vercel Hobby rejasida cron belgilangan soat ichida (±59 daqiqa) ishga tushadi.
`NOTIFICATION_HOUR` ni ham shu soatga moslang (u faqat xabar matnida ko'rinadi).

## Muhim
- Botni kompyuterda `python bot/main.py` bilan ishga tushirmang: u webhook'ni o'chirib qo'yadi.
  Shunday bo'lsa, `/api/setup` ni qayta oching.
- `.env`, `.venv`, `*.sqlite3` GitHub'ga yuklanmaydi (`.gitignore`).
