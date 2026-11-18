# Kiwi Music API — Backend Engine

High-speed asynchronous music search, Yukisbox audio caching, metadata indexing, and delivery service.

## Architecture
- **Framework:** FastAPI (async) + Uvicorn
- **Database:** PostgreSQL (SQLAlchemy 2.0 Async + asyncpg + Alembic)
- **Central Storage:** Yukisbox (`https://yukiapi.site`)
  - **Method A:** `POST /v1/upload-url` (preferred direct URL acquisition)
  - **Method B:** `POST /v1/upload` (streamed multipart fallback)
- **Music Provider:** JioSaavn (direct gateway + optional Sumanjay API adapter)
- **Single-Flight Lock:** In-flight concurrent request deduplication preventing duplicate uploads
- **API Key Quota:** Atomic 200 lifetime requests per key

## Local Development
```bash
cd backend
cp .env.example .env
# Edit .env with your settings (defaults to SQLite for instant local dev)
python -m pip install -r requirements.txt
python -m alembic -c alembic.ini upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

## Running Tests
```bash
cd backend
pytest tests/ -v
```

## Deployment (Render)
- **Service Name:** `kiwi-backend`
- **Assigned Public URL:** `https://kiwi-backend.onrender.com`
- **Deploy with Blueprint:** Connect your GitHub repo to Render; Render automatically detects `render.yaml`.
- **Manual Web Service Setup:**
  - **Runtime:** Python 3
  - **Root Directory:** `backend`
  - **Build Command:** `pip install --upgrade pip && pip install -r requirements.txt`
  - **Start Command:** `alembic -c alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - **Health Check Path:** `/v1/health`
  - **Environment Variables:**
    - `DATABASE_URL`: `postgresql+asyncpg://...` (Your Neon or Postgres DB URL)
    - `INTERNAL_API_SECRET`: Secure shared secret string
    - `SECRET_KEY`: Random 32+ character string
    - `YUKISBOX_BASE_URL`: `https://yukiapi.site`
    - `JIOSAAVN_API_URL`: `https://saavn.dev`
    - `YUKISBOX_TIMEOUT`: `300`
    - `MAX_UPLOAD_CONCURRENCY`: `3`
    - `MAX_DOWNLOAD_SIZE_MB`: `100`

