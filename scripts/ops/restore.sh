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
# SCRIPT_DIR ist <repo>/scripts/ops — der Repo-Root (mit docker-compose.prod.yml)
# liegt ZWEI Ebenen darueber. Ein einfaches ``dirname "$SCRIPT_DIR"`` zeigte
# faelschlich auf <repo>/scripts, sodass COMPOSE_FILE ins Leere lief (Refs #1336).
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

# Fruehzeitiger, klarer Abbruch statt kryptischem docker-Fehler (Refs #1336).
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "FEHLER: Compose-Datei nicht gefunden: ${COMPOSE_FILE}" >&2
    echo "  Erwartet im Repo-Root — liegt restore.sh unter <repo>/scripts/ops/?" >&2
    exit 1
fi

# A4.3: Encrypt-then-MAC-Helfer (backup_verify_hmac).
# shellcheck source=scripts/ops/_backup_common.sh
source "${SCRIPT_DIR}/_backup_common.sh"

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
backup_require_real_key

# Der Plain-SQL-Dump enthaelt OWNER-TO-/FORCE-RLS-Statements und COPY in
# FORCE-RLS-Tabellen — das scheitert unter der App-Rolle (NOBYPASSRLS, kein
# DDL-Owner-Recht). Bootstrap-Superuser via Local-Socket-Trust im
# db-Container — wie scripts/ops/restore-drill.sh (Review N4).
SU_USER="${POSTGRES_SUPERUSER:-postgres}"

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

# DB restore — A4.3: Integrität VOR dem Entschlüsseln prüfen.
echo "Prüfe Integrität des DB-Backups (HMAC)..."
backup_verify_hmac "$BACKUP_FILE"
echo "Stelle DB-Backup wieder her..."
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$BACKUP_FILE" \
    | gunzip \
    | docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$SU_USER" "$POSTGRES_DB"
echo "DB-Wiederherstellung abgeschlossen."

# Medien-Restore (Refs #720)
if [[ -n "$MEDIA_FILE" ]]; then
    MEDIA_ROOT_CONTAINER="${MEDIA_ROOT_CONTAINER:-/data/media}"
    MEDIA_PARENT="$(dirname "$MEDIA_ROOT_CONTAINER")"
    MEDIA_NAME="$(basename "$MEDIA_ROOT_CONTAINER")"

    echo "Loesche bestehenden Medien-Inhalt unter ${MEDIA_ROOT_CONTAINER} ..."
    docker compose -f "$COMPOSE_FILE" exec -T web \
        sh -c "rm -rf '${MEDIA_ROOT_CONTAINER}'/* '${MEDIA_ROOT_CONTAINER}'/.[!.]* 2>/dev/null || true"

    echo "Prüfe Integrität des Medien-Backups (HMAC)..."
    backup_verify_hmac "$MEDIA_FILE"
    echo "Entpacke Medien-Backup ..."
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$MEDIA_FILE" \
        | gunzip \
        | docker compose -f "$COMPOSE_FILE" exec -T web \
            tar -C "$MEDIA_PARENT" -xf -
    echo "Medien-Wiederherstellung abgeschlossen."
fi
