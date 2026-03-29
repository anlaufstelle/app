#!/usr/bin/env bash
set -euo pipefail

# Anlaufstelle Restore-Skript
# Stellt eine verschlüsselte Backup-Datei wieder her

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

if [[ $# -lt 1 ]]; then
    echo "Verwendung: $0 <backup-datei.sql.gz.enc>"
    exit 1
fi

BACKUP_FILE="$1"

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "Fehler: Datei nicht gefunden: $BACKUP_FILE"
    exit 1
fi

# .env laden falls vorhanden
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

: "${POSTGRES_USER:?POSTGRES_USER nicht gesetzt}"
: "${POSTGRES_DB:?POSTGRES_DB nicht gesetzt}"
: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY nicht gesetzt}"

echo "WARNUNG: Die Datenbank '${POSTGRES_DB}' wird überschrieben!"
echo "Backup-Datei: ${BACKUP_FILE}"
read -rp "Fortfahren? (ja/nein): " CONFIRM

if [[ "$CONFIRM" != "ja" ]]; then
    echo "Abgebrochen."
    exit 0
fi

echo "Stelle Backup wieder her..."

# Decrypt → decompress → psql
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$BACKUP_FILE" \
    | gunzip \
    | docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$POSTGRES_USER" "$POSTGRES_DB"

echo "Wiederherstellung abgeschlossen."
