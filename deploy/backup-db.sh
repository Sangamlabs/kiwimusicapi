#!/bin/bash
set -euo pipefail

# Configuration
BACKUP_DIR="${HOME}/backups/anaya-db"
mkdir -p "${BACKUP_DIR}"

DATE=$(date +"%Y-%m-%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/anaya-${DATE}.sql.gz"
DOCKER_CMD="${DOCKER:-docker}"

# Load Telegram notification credentials if present
if [ -f "${HOME}/.anaya_backup.env" ]; then
    # shellcheck disable=SC1091
    source "${HOME}/.anaya_backup.env"
fi

echo "[$(date)] Starting Anaya DB Backup..."

# 1. Dump database from Docker container
${DOCKER_CMD} exec anaya-db pg_dump -U anaya -d anaya --no-owner | gzip > "${BACKUP_FILE}"

FILESIZE=$(ls -lh "${BACKUP_FILE}" | awk '{print $5}')
echo "[$(date)] Backup completed: ${BACKUP_FILE} (${FILESIZE})"

# 2. Send to Telegram if configured
if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
    echo "[$(date)] Sending backup to Telegram..."
    curl -s -F "chat_id=${TG_CHAT_ID}" \
         -F "caption=Anaya DB Backup: ${DATE} (${FILESIZE})" \
         -F "document=@${BACKUP_FILE}" \
         "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendDocument" > /dev/null || echo "[WARNING] Telegram upload failed"
fi

# 3. Clean up backups older than 14 days
find "${BACKUP_DIR}" -name "anaya-*.sql.gz" -mtime +14 -delete
echo "[$(date)] Cleaned backups older than 14 days."
