#!/usr/bin/env bash
set -euo pipefail

# Anlaufstelle Backup-Skript
# Erstellt verschlüsselte PostgreSQL-Backups mit Rotation

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_DIR}/backups"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

# .env laden falls vorhanden
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

# Pflichtfelder prüfen
: "${POSTGRES_USER:?POSTGRES_USER nicht gesetzt}"
: "${POSTGRES_DB:?POSTGRES_DB nicht gesetzt}"
: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY nicht gesetzt}"

TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
DAILY_DIR="${BACKUP_DIR}/daily"
WEEKLY_DIR="${BACKUP_DIR}/weekly"
MONTHLY_DIR="${BACKUP_DIR}/monthly"

mkdir -p "$DAILY_DIR" "$WEEKLY_DIR" "$MONTHLY_DIR"

BACKUP_FILE="${DAILY_DIR}/anlaufstelle_${TIMESTAMP}.sql.gz.enc"

echo "Erstelle Backup: ${BACKUP_FILE}"

# pg_dump → gzip → openssl encrypt
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    | gzip \
    | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
    > "$BACKUP_FILE"

if [ ! -s "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file is empty or missing"
    exit 1
fi

echo "Backup erstellt: $(du -h "$BACKUP_FILE" | cut -f1)"

# Rotation
DOW=$(date +%u)  # 1=Montag, 7=Sonntag
DOM=$(date +%d)  # Tag des Monats

# Sonntags → Weekly kopieren
if [[ "$DOW" -eq 7 ]]; then
    cp "$BACKUP_FILE" "$WEEKLY_DIR/"
    echo "Weekly-Backup kopiert."
fi

# Am 1. des Monats → Monthly kopieren
if [[ "$DOM" -eq "01" ]]; then
    cp "$BACKUP_FILE" "$MONTHLY_DIR/"
    echo "Monthly-Backup kopiert."
fi

# Alte Backups löschen
find "$DAILY_DIR" -name "*.sql.gz.enc" -mtime +7 -delete
find "$WEEKLY_DIR" -name "*.sql.gz.enc" -mtime +28 -delete
find "$MONTHLY_DIR" -name "*.sql.gz.enc" -mtime +90 -delete

echo "Rotation abgeschlossen."
