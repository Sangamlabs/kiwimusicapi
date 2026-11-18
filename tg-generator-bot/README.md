# Kiwi Music API — Telegram Key Generator Bot

This is the independent Telegram bot responsible for user API key generation, quota tracking, documentation delivery, and key revocation for the **Kiwi Music API** platform.

## Features
- `/start` — Clean interactive welcome with inline button menus.
- `/generate` — Cryptographically secure `KIWI-XXXXXXXX` key issuance with a lifetime quota of 200 requests.
- `/myapi` — Lists all issued keys with masked identifiers and remaining quota.
- `/usage` — Real-time consumption statistics per key.
- `/revoke` — Safe revocation with inline confirmation dialog.
- `/docs` — Built-in Python and Go integration examples.
- `/status` — Real-time backend and storage health verification.
- `/stats` — Owner-only operational and platform metrics.

## Local Run
```bash
cd tg-generator-bot
cp .env.example .env
# Edit .env with your BOT_TOKEN and BACKEND_API_URL
python -m pip install -r requirements.txt
python bot.py
```

## Deployment (Railway)
1. In Railway, click **New Project** -> **Deploy from GitHub repo** and select this repository.
2. Go to **Settings** -> **General** -> **Root Directory**, and set it to:
   ```
   tg-generator-bot
   ```
3. Go to **Variables** and add:
   - `BOT_TOKEN`: Your Telegram Bot Token from `@BotFather`
   - `BACKEND_API_URL`: `https://kiwi-backend.onrender.com`
   - `BACKEND_INTERNAL_SECRET`: Same value as `INTERNAL_API_SECRET` configured on Render
   - `OWNER_ID`: Your numerical Telegram user ID (for `/stats` access)
4. Railway will automatically detect `railway.toml` / `Dockerfile` / `Procfile`, install requirements, and run `python bot.py` 24/7 with automatic restart policies.

