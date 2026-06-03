#!/usr/bin/env bash
set -euo pipefail

# Anlaufstelle Restore-Skript
# Stellt verschlüsselte DB- und Medien-Backups wieder her.
#
# Verwendung:
#   $0 <db-backup.sql.gz.enc>                       # Nur DB
#   $0 <db-backup.sql.gz.enc> <media.tar.gz.enc>    # DB + Medien (Refs #720)
#
# Medien-Restore loescht den existierenden /data/media-Inhalt im web-Container
# und entpackt das Archiv frisch. Erwartet die im backup.sh erzeugte Struktur
# (`media/...` relativ zu `/data`).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Verwendung: $0 <db-backup.sql.gz.enc> [media-backup.tar.gz.enc]"
    exit 1
fi

BACKUP_FILE="$1"
MEDIA_FILE="${2:-}"

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "Fehler: DB-Backup-Datei nicht gefunden: $BACKUP_FILE"
    exit 1
fi

if [[ -n "$MEDIA_FILE" && ! -f "$MEDIA_FILE" ]]; then
    echo "Fehler: Medien-Backup-Datei nicht gefunden: $MEDIA_FILE"
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
echo "DB-Backup:    ${BACKUP_FILE}"
if [[ -n "$MEDIA_FILE" ]]; then
    echo "WARNUNG: Der MEDIA_ROOT-Inhalt wird ueberschrieben!"
    echo "Medien-Backup: ${MEDIA_FILE}"
fi
read -rp "Fortfahren? (ja/nein): " CONFIRM

if [[ "$CONFIRM" != "ja" ]]; then
    echo "Abgebrochen."
    exit 0
fi

# DB restore
echo "Stelle DB-Backup wieder her..."
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$BACKUP_FILE" \
    | gunzip \
    | docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$POSTGRES_USER" "$POSTGRES_DB"
echo "DB-Wiederherstellung abgeschlossen."

# Medien-Restore (Refs #720)
if [[ -n "$MEDIA_FILE" ]]; then
    MEDIA_ROOT_CONTAINER="${MEDIA_ROOT_CONTAINER:-/data/media}"
    MEDIA_PARENT="$(dirname "$MEDIA_ROOT_CONTAINER")"
    MEDIA_NAME="$(basename "$MEDIA_ROOT_CONTAINER")"

    echo "Loesche bestehenden Medien-Inhalt unter ${MEDIA_ROOT_CONTAINER} ..."
    docker compose -f "$COMPOSE_FILE" exec -T web \
        sh -c "rm -rf '${MEDIA_ROOT_CONTAINER}'/* '${MEDIA_ROOT_CONTAINER}'/.[!.]* 2>/dev/null || true"

    echo "Entpacke Medien-Backup ..."
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$MEDIA_FILE" \
        | gunzip \
        | docker compose -f "$COMPOSE_FILE" exec -T web \
            tar -C "$MEDIA_PARENT" -xf -
    echo "Medien-Wiederherstellung abgeschlossen."
fi
