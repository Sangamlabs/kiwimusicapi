# Anaya Music API - Setup, Changes aur Naye Server par Chalane ki Guide

Ye file batati hai ki (1) API ko fast karne ke liye kya kya badla gaya, aur (2) kisi **naye / dusre server** par
ise shuru se kaise chalana hai, purana data kaise lana hai, aur server mar jaye to kya karna hai.

> `YOUR.DOMAIN` = tumhara domain (jaise `anayaapi.duckdns.org`), `NEW_IP` = naye server ka IP,
> `OLD_IP` = purane server ka IP. Ubuntu 22.04 / 24.04 maan kar chala hai.

---

## 1. Poora structure (kaun kisse baat karta hai)

```
Telegram user -> Music bot (ANAYAMUSIC) -> Anaya API (ye repo) -> JioSaavn (gaana)
                                              |
                                              +-> Postgres (isi server par: API keys, users, usage)
```

- **Music bot** ke paas sirf `ANAYA_API_URL` aur `ANAYAAPI` (key) hoti hai.
- **API** search karta hai aur `/v1/resolve` par gaane ka stream URL deta hai.
- **Key generator bot** (`tg-generator-bot`) API ke `/internal/...` endpoint se keys banata hai
  (isliye `INTERNAL_API_SECRET` dono jagah same hona chahiye).

---

## 2. Kya kya change kiya (aur kyun)

| # | Dikkat jo mili | Fix | Kahan |
|---|---|---|---|
| 1 | Remote DB (Neon, ~190 ms door) ki wajah se har request 1-5 second leti thi (`/v1/health` bhi 1.1 s) | Postgres isi server par chalaya | `docker-compose.yml` |
| 2 | Rust media engine har resolve me 40-60 s hang karta tha | Default me band (`RUST_MEDIA_ENGINE_URL` khaali) aur Rust ke bina lite build | `Dockerfile.lite`, `docker-compose.yml` |
| 3 | Yukisbox upload `524` deta tha aur 2+ minute lagata tha | `STORAGE_UPLOAD_ENABLED=false` -> JioSaavn ka direct URL | `config.py`, `music.py` |
| 4 | Upload fail hone par `500 RESOLUTION_FAILED` aata tha, fallback chalta hi nahi tha | Ab upload fail ho to hamesha direct URL par fallback | `music.py` |
| 5 | nginx 60 s me request kaat deta tha (`504`) | Timeout 300 s | `deploy/nginx-anaya.conf` |
| 6 | Container `rm` karne par SQLite DB chala gaya tha | Data named volume `anaya-pgdata` me, aur roz backup | `docker-compose.yml`, `deploy/backup-db.sh` |

**Nateeja:** `/v1/resolve` 5.5 second se **~0.27 second**, `/v1/health` 2 s se ~0.2 s.

### Code me kya badla (sirf 2 files)
- `backend/app/config.py`: naya setting `STORAGE_UPLOAD_ENABLED` (default `True`, yaani purana behaviour).
- `backend/app/services/music.py`: setting ko maanta hai, aur Yukisbox upload fail ho to error ki jagah
  JioSaavn ka direct URL return karta hai.

### Naye files
`docker-compose.yml`, `Dockerfile.lite`, `.env.example`, `deploy/nginx-anaya.conf`, `deploy/backup-db.sh`, `README-DEPLOY.md`.

### Dhyan rakho
- **Direct URL permanent nahi hota.** JioSaavn ka link Yukisbox jitna stable nahi hota. Bot har baar naya link maangta hai, isliye dikkat nahi aati.
- **`/v1/stream` (Rust engine wala route) band rehta hai.** Bot isko use nahi karta.
- **Yukisbox wapas chalana ho:** `.env` me `STORAGE_UPLOAD_ENABLED=true` karo, phir `sudo docker compose up -d`.
  Pehle test karo ki Yukisbox jaldi jawab de raha hai, warna resolve phir se slow ho jayega.

---

## 3. Naye server par setup

### 3.1 Server taiyaar karo
```bash
sudo apt update && sudo apt -y upgrade
curl -fsSL https://get.docker.com | sudo sh
sudo apt -y install nginx certbot python3-certbot-nginx ufw git
```
Cloud panel (AWS Security Group wagairah) me ports **22, 80, 443** kholo, aur server par:
```bash
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw --force enable
```

### 3.2 Code lo aur `.env` banao
```bash
git clone <TUMHARA-REPO-URL> Anayamusicapi && cd Anayamusicapi
cp .env.example .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
sed -i "s|^INTERNAL_API_SECRET=.*|INTERNAL_API_SECRET=$(openssl rand -hex 32)|" .env
chmod 600 .env
```
- Agar **purana server chal raha hai / data le jana hai**, to `INTERNAL_API_SECRET` purani value hi rakho
  (`grep '^INTERNAL_API_SECRET=' .env` purane server par). Nayi value rakhi to generator bot ka
  `BACKEND_INTERNAL_SECRET` bhi badalna padega.
- `.env` kabhi GitHub par commit mat karna aur kisi ko bhejna mat.

### 3.3 Case A - bilkul naya (koi purana data nahi)
```bash
sudo docker compose up -d --build        # ~2 minute, Rust compile nahi hota
sleep 20 && curl -s http://127.0.0.1:8000/v1/health
```
Nayi key banane ke liye generator bot use karo, ya:
```bash
curl -s -X POST http://127.0.0.1:8000/internal/api-keys/generate \
  -H "X-Internal-Secret: $(grep '^INTERNAL_API_SECRET=' .env | cut -d= -f2-)" \
  -H "Content-Type: application/json" -d '{"telegram_user_id": TUMHARA_TG_ID}'
```
Jawab me `api_key` sirf ek baar dikhti hai, turant copy kar lo.

### 3.4 Case B - purana data lana hai (server badal rahe ho)

**Purane server par:**
```bash
sudo docker stop anaya                     # dump ke baad nayi keys/usage na likhi jayein
sudo docker exec anaya-db pg_dump -U anaya -d anaya --no-owner | gzip > ~/anaya-db.sql.gz
ls -lh ~/anaya-db.sql.gz
scp ~/anaya-db.sql.gz ubuntu@NEW_IP:~/
```

**Naye server par** (3.2 ke baad). **Pehle sirf DB chalao, API baad me**, warna restore me "already exists" errors aate hain:
```bash
cd ~/Anayamusicapi
sudo docker compose up -d db
sleep 12
gunzip -c ~/anaya-db.sql.gz | sudo docker exec -i anaya-db psql -U anaya -d anaya -q
sudo docker exec anaya-db psql -U anaya -d anaya -c "select (select count(*) from api_keys) keys, (select count(*) from users) users;"
sudo docker compose up -d --build
```
`keys` aur `users` purane server jitne aane chahiye. **API keys wahi chalengi**, kyunki backend key ko seedha SHA-256 se hash karta hai (koi secret nahi lagta).

Agar galti se API pehle chal gayi aur restore me errors aaye (sirf naye server par):
```bash
sudo docker compose down -v      # naya khaali DB hata deta hai
# phir 3.4 ke steps dobara
```

### 3.5 Case C - purana server mar gaya (sirf backup file hai)
Backup file (`anaya-DATE.sql.gz`) Telegram se ya laptop se naye server par laao (`scp`), phir **Case B ke "naye server par" wale steps** chalao
(`anaya-db.sql.gz` ki jagah apni file ka naam). Agar backup bhi nahi hai: Neon wali purani DB ki `DATABASE_URL` se `pg_dump` lo
(Neon ke liye `postgres:18` image use karo, version mismatch ka error aaye to jo version bataye wahi).

### 3.6 nginx + SSL + domain
```bash
sudo cp deploy/nginx-anaya.conf /etc/nginx/sites-available/anaya
sudo sed -i 's/YOUR.DOMAIN/tumhara.domain.com/' /etc/nginx/sites-available/anaya
sudo ln -sf /etc/nginx/sites-available/anaya /etc/nginx/sites-enabled/anaya
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```
Domain naye IP par ghumao (DuckDNS ho to):
```bash
curl "https://www.duckdns.org/update?domains=TUMHARA_SUBDOMAIN&token=TUMHARA_TOKEN&ip=NEW_IP"
getent hosts tumhara.domain.com            # NEW_IP dikhna chahiye
sudo certbot --nginx -d tumhara.domain.com   # HTTP->HTTPS redirect ke liye yes
```
`getent` me naya IP dikhne ke **baad** hi `certbot` chalana.

### 3.7 Test
```bash
read -rsp "API key: " K; echo
curl -s -o /dev/null -w "health: %{time_total}s\n" https://tumhara.domain.com/v1/health
curl -s -o /dev/null -w "search: %{http_code} %{time_total}s\n" -H "X-API-Key: $K" "https://tumhara.domain.com/v1/search?q=believer&limit=1"
curl -s -o /dev/null -w "resolve: %{http_code} %{time_total}s\n" -H "X-API-Key: $K" https://tumhara.domain.com/v1/resolve/anaya_saavn_uJKyhC19
unset K
```
Theek hone par `health` ~0.05-0.2 s aur `resolve` `200` ke saath 1 second se kam.

---

## 4. Bot side (ANAYAMUSIC) naye server / naye API ke saath

Bot ki `.env` me:
```
ANAYA_API_URL=https://tumhara.domain.com      # bina trailing slash
ANAYAAPI=ANAYA-XXXXXXXX                       # tumhari API key
```
- Domain wahi rakha to bot me kuch badalna nahi padta.
- Naya domain liya to bot me `ANAYA_API_URL` aur key-generator bot me `BACKEND_API_URL` badal ke dono restart karo.
- Bot me ye files badli thi: `youtube.py` (sirf Anaya API, YouTube/Spotify/yt-dlp hata diya), `tools.py`, `config.py`, `main.py`,
  `plugins/{playback,controls,playlist,start,_common}.py`, `utils/button.py`, `requirements.txt`, `Dockerfile`,
  `.env.example`, `app.json`, `README.md`, aur 3 tests.
- Bot ka apna data (playlists, settings) **MongoDB** me hai (`MONGODB_URI`), SQL backup me nahi aata. Naye server par bas wahi URI rakho.
- Audio smooth rakhne ke liye bot `.env` me `AUDIO_QUALITY=HIGH`, aur bot ko Telegram ke Asia servers ke paas chalao.

---

## 5. Backup (server mar jaye to bhi data bachna chahiye)

Backup usi server par rakhne se server ke saath chala jata hai, isliye **off-server copy** zaroori hai.

```bash
# Private bot banao (@BotFather), usse /start karo, phir:
cat > ~/.anaya_backup.env << 'EOF'
TG_BOT_TOKEN=yahan_bot_token
TG_CHAT_ID=yahan_apni_user_id
EOF
chmod 600 ~/.anaya_backup.env

chmod +x deploy/backup-db.sh
DOCKER="sudo docker" deploy/backup-db.sh          # test: Telegram par .sql.gz aani chahiye
(crontab -l 2>/dev/null; echo "30 3 * * * DOCKER=\"sudo docker\" $HOME/Anayamusicapi/deploy/backup-db.sh") | crontab -
```
- Roz raat 3:30 par backup banta hai, 14 din purane delete hote hain, aur ek copy Telegram par jaati hai.
- Cloud panel se **server ka snapshot** bhi lo (AWS: EC2 -> Volumes -> Create snapshot, ya Data Lifecycle Manager se roz).
- **UptimeRobot** (free) me `https://tumhara.domain.com/v1/health` ka monitor lagao, taaki server band hone par turant alert mile.
- Restore: `gunzip -c anaya-DATE.sql.gz | sudo docker exec -i anaya-db psql -U anaya -d anaya -q` (API band ho tab).

---

## 6. Roz ke kaam ke commands

```bash
sudo docker ps --format '{{.Names}}  {{.Status}}'      # kya chal raha hai
sudo docker logs -f --tail 50 anaya                    # API ke live logs
sudo docker compose restart api                        # API restart
git pull && sudo docker compose up -d --build          # naya code lagao
sudo docker exec anaya-db psql -U anaya -d anaya -c "select count(*) from api_keys;"
```

---

## 7. Dikkat aaye to (troubleshooting)

| Error / Dikkat | Matlab aur ilaaj |
|---|---|
| `504 Gateway Time-out` | nginx timeout. `deploy/nginx-anaya.conf` ke `proxy_read_timeout 300s` lines `location /` me hone chahiye, phir `sudo nginx -t && sudo systemctl reload nginx`. |
| `401 INVALID_API_KEY` | Key DB me nahi mili. Galat DB se container chala (jaise khaali/purani DB), ya restore nahi hua. `select count(*) from api_keys;` check karo. |
| `500 RESOLUTION_FAILED` | Purana code (fix se pehle ka) chal raha hai, ya JioSaavn dikkat. `git pull && sudo docker compose up -d --build`, phir `docker logs anaya`. |
| `health` bhi 1 second+ leta hai | DB door hai. Postgres isi server par (compose) hona chahiye, remote DB nahi. |
| `relation "api_keys" does not exist` | Restore nahi hua, ya galat DB. Restore ke baad hi API chalao. |
| Restore me "already exists" errors | API restore se pehle chal gayi thi. `sudo docker compose down -v` karke dobara. |
| `container name "/anaya" is already in use` | Purane manual containers hain. `sudo docker rm -f anaya anaya-db` (volume `anaya-pgdata` bachta hai), phir compose chalao. |
| `database is locked` | Sirf SQLite use karte waqt aata hai. Postgres me nahi aata. |
| `pg_dump: server version mismatch` | `postgres:17` ki jagah error me bataya version use karo (Neon abhi 18). |
| Bot me "Music server took too long" | API 80 s me jawab nahi de saki. `docker logs anaya` dekho aur `/v1/resolve` ko curl se time karo. |

---

## 8. Suraksha (security)

- `.env`, `.env.neon-backup`, `~/.anaya_backup.env` aur backup files **kabhi GitHub par ya kisi ko mat bhejo**.
- Code me `INTERNAL_API_SECRET` ka default (`anaya_secret_change_me_in_production`) kamzor hai. Compose bina set kiye start hi nahi hone deta, par
  agar kisi aur tareeke se chalao to zaroor random value set karo, warna koi bhi keys bana/revoke kar sakta hai.
- Jo API key kabhi chat ya screenshot me dikh gayi ho, use generator bot se nayi banake badal do.
- Port 8000 internet par mat kholo; compose use sirf `127.0.0.1` par rakhta hai aur nginx se hi baat hoti hai.
