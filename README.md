# Kiwi Music API 🥝

**Kiwi Music API** is an independent, high-speed music search, Yukisbox storage cache, and delivery infrastructure service built for Telegram music bots and external HTTP clients (Python & Go).

---

## 🏛️ Two-Folder Architecture

```text
KIWI-MUSIC-API/
├── tg-generator-bot/          # Independent Telegram API Key Generator Bot (Railway)
│   ├── bot.py                # Main Aiogram 3 bot entry point
│   ├── config.py             # Bot environment settings
│   ├── backend_client.py     # Secure internal API communication client
│   ├── handlers.py           # Command & inline keyboard handlers
│   ├── keyboards.py          # Interactive inline keyboards
│   ├── requirements.txt      # Bot dependencies
│   ├── railway.toml          # Railway deployment config
│   └── README.md
│
├── backend/                   # Independent Music Backend Service (Render)
│   ├── app/
│   │   ├── main.py           # FastAPI application entry & CORS middleware
│   │   ├── config.py         # Pydantic v2 BaseSettings
│   │   ├── database.py       # SQLAlchemy 2.0 async engine & sessionmaker
│   │   ├── models.py         # Track, User, ApiKey, ApiUsage, UploadJob models
│   │   ├── schemas.py        # Pydantic request/response models
│   │   ├── utils.py          # Cryptographic key generator & slug sanitizer
│   │   ├── api/
│   │   │   ├── routes.py     # Public /v1 endpoints (search, resolve, stream, usage, health)
│   │   │   ├── auth.py       # API key authentication & atomic quota enforcement
│   │   │   └── internal.py   # Internal management endpoints (/internal/api-keys/...)
│   │   └── services/
│   │       ├── music.py      # Acquisition orchestrator & single-flight lock
│   │       ├── providers.py  # Abstract MusicProvider base interface
│   │       ├── jiosaavn.py   # Async JioSaavn provider adapter
│   │       ├── storage.py    # Abstract StorageBackend base interface
│   │       ├── yukysbox.py   # Yukisbox adapter (Method A URL + Method B file fallback)
│   │       └── cache.py      # Hot in-memory / Redis cache
│   ├── migrations/           # Alembic asynchronous migrations
│   ├── tests/                # Pytest unit & integration test suite (20 passing tests)
│   ├── requirements.txt      # Backend production dependencies
│   ├── Dockerfile            # Container definition
│   ├── render.yaml           # Render deployment blueprint
│   └── README.md
│
├── examples/                 # Client SDK examples
│   ├── client.py             # Python example client
│   └── client.go             # Go example client
├── .gitignore
└── README.md
```

---

## ⚡ Core Music Acquisition Pipeline

```text
Incoming Track Request (/v1/resolve/{track_id})
       │
       ▼
Validate API Key & Check Lifetime Quota
       │
       ▼
Check In-Memory Cache & PostgreSQL
       ├── [Cache Hit] ───────► Return Stored Yukisbox URL (0ms external latency)
       │
       ▼ [Cache Miss]
Acquire In-Flight Single-Flight Lock (100 simultaneous requests -> 1 upload)
       │
       ▼
Search / Fetch Track Details from JioSaavn
       │
       ▼
Obtain & Verify Authorized Audio URL
       │
       ▼
Upload to Yukisbox:
       ├── Method A: POST /v1/upload-url (Form-urlencoded direct URL)
       └── Method B: POST /v1/upload (Streamed chunked multipart fallback)
       │
       ▼
Persist Metadata & Yukisbox URL in PostgreSQL
       │
       ▼
Return Response & Atomically Deduct 1 Billable Quota Unit
```

---

## 🔑 API Key & Quota Rules
- **One User, One Active API Key:** Each Telegram user is permitted at most **ONE active API key** at a time, strictly enforced at both the Telegram bot and database level (via a partial unique index on `api_keys(user_id) WHERE status = 'active'`).
- **Generation Policy:**
  - **Case A (New User):** Generates a fresh key with 200 lifetime requests.
  - **Case B (Existing Active Key):** Returns existing key details (key prefix, usage, remaining quota). Prevents creating duplicate active keys.
  - **Case C (Exhausted Key):** Once a key reaches 200/200 used requests, it is marked `exhausted`, allowing the user to generate a replacement key with a fresh 200 quota.
  - **Case D (Revoked Key):** A user can revoke their active key anytime (with confirmation in the bot). Once revoked, the key is permanently disabled, allowing the user to generate a fresh replacement key.
- **Lifetime Cap:** Every active API key receives exactly **200 successful billable requests**.
- **Failed Requests:** Never consume quota.
- **Billable Endpoints:** `/v1/resolve/{id}` and `/v1/stream/{id}`.
- **Free Endpoints:** `/v1/search`, `/v1/track/{id}`, `/v1/usage`, and `/v1/health`.
- **Atomic Enforcement:** Enforced in a single atomic SQL statement preventing concurrent quota bypass:
  ```sql
  UPDATE api_keys 
  SET used_requests = used_requests + 1 
  WHERE id = :id AND used_requests < total_quota AND status = 'active' 
  RETURNING used_requests, total_quota;
  ```
- **Plaintext Security:** Plaintext key `KIWI-XXXXXXXX` is displayed **only once** upon generation. The database stores only the SHA-256 hash.

---

## 📡 API Reference

### Public API (`/v1`)
**Authentication (Flexible):**
Clients can authenticate using any of the following:
- Environment Variable: `KIWIAPI=YOUR_API_KEY`
- Header: `KIWIAPI: YOUR_API_KEY`
- Header: `X-API-Key: YOUR_API_KEY`
- Header: `Authorization: Bearer YOUR_API_KEY`
- Query Parameter: `?kiwiapi=YOUR_API_KEY`

**Endpoints:**
- `GET /v1/search?q={query}&limit=10` — Search for songs by title or artist. *(Free)*
- `GET /v1/track/{track_id}` — Inspect track details and storage metadata. *(Free)*
- `GET /v1/resolve/{track_id}` — Resolve to permanent Yukisbox storage URL. *(Billable — 1 quota)*
- `GET /v1/stream/{track_id}` — 302 Redirect to direct stream with HTTP Range support. *(Billable — 1 quota)*
- `GET /v1/usage` — Check your key's quota and request stats. *(Free)*
- `GET /v1/health` — System health and upstream status. *(Free, no key required)*

### Internal Management API (`/internal`, requires header `X-Internal-Secret: SECRET`)
- `POST /internal/api-keys/generate` — Generate a new 200-quota key for a Telegram user ID.
- `GET /internal/api-keys/list?telegram_user_id={id}` — List all keys owned by user.
- `GET /internal/api-keys/usage?telegram_user_id={id}` — Get usage metrics.
- `POST /internal/api-keys/revoke` — Revoke a key immediately.
- `GET /internal/stats` — System-wide platform metrics (Owner-only).

---

## 🚀 Running Locally

### 1. Run the Backend API
```bash
cd backend
cp .env.example .env
python -m pip install -r requirements.txt
python -m alembic -c alembic.ini upgrade head
python -m uvicorn app.main:app --reload --port 8000
```
API Documentation will be available at: `http://localhost:8000/docs`

### 2. Run the Telegram Generator Bot
```bash
cd tg-generator-bot
cp .env.example .env
# Edit .env with your BOT_TOKEN and BACKEND_API_URL=http://localhost:8000
python -m pip install -r requirements.txt
python bot.py
```

### 3. Run Automated Tests
```bash
cd backend
python -m pytest tests/ -v -c pytest.ini
```

---

## 🚢 Production Deployment

### 1. Backend Deployment (Render)
- **Service Name:** `kiwi-backend`
- **Public URL:** `https://kiwi-backend.onrender.com`
- **Method A (Blueprint — Recommended):**
  1. In Render, select **New Blueprint** and connect this repository.
  2. Render detects the root `render.yaml` automatically.
  3. Enter `DATABASE_URL` and `INTERNAL_API_SECRET`.
  4. Click **Apply**. Render builds the service, runs database migrations, and exposes `https://kiwi-backend.onrender.com`.
- **Method B (Web Service Manual):**
  1. Select **New Web Service** pointing to this repository.
  2. Set **Root Directory** to `backend`.
  3. Build command: `pip install --upgrade pip && pip install -r requirements.txt`
  4. Start command: `alembic -c alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  5. Set Health Check path to `/v1/health`.

### 2. Telegram Bot Deployment (Railway)
1. In Railway, click **New Project** -> **Deploy from GitHub repo** and select this repository.
2. In **Settings** -> **General** -> **Root Directory**, set:
   ```
   tg-generator-bot
   ```
3. In **Variables**, add:
   - `BOT_TOKEN`: Your Telegram Bot token from `@BotFather`
   - `BACKEND_API_URL`: `https://kiwi-backend.onrender.com`
   - `BACKEND_INTERNAL_SECRET`: Same value as `INTERNAL_API_SECRET` on Render
   - `OWNER_ID`: Your Telegram numeric User ID
4. Railway will automatically build via `railway.toml`, start `python bot.py`, and run 24/7 with automatic restart policies.
3. Railway automatically uses `railway.toml`.
4. Configure variables: `BOT_TOKEN`, `BACKEND_API_URL` (your Render URL), `BACKEND_INTERNAL_SECRET`, and `OWNER_ID`.

---

## 💻 Client Examples

### Python Client
```python
import httpx

headers = {"X-API-Key": "KIWI-YOURKEY"}
with httpx.Client(base_url="https://kiwi-music-api.onrender.com", headers=headers) as client:
    # Search
    results = client.get("/v1/search", params={"q": "Believer"}).json()
    track_id = results["results"][0]["track_id"]
    
    # Resolve URL
    resolved = client.get(f"/v1/resolve/{track_id}").json()
    print("Yukisbox URL:", resolved["music_url"])
```

### Go Client
```go
req, _ := http.NewRequest("GET", "https://kiwi-music-api.onrender.com/v1/search?q=Believer", nil)
req.Header.Set("X-API-Key", "KIWI-YOURKEY")
resp, _ := (&http.Client{}).Do(req)
```
Full working client scripts are available in [examples/client.py](file:///d:/PC/music%20api/examples/client.py) and [examples/client.go](file:///d:/PC/music%20api/examples/client.go).

