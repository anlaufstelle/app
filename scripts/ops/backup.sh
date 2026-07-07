#!/usr/bin/env bash
set -euo pipefail

# Anlaufstelle Backup-Skript
# Erstellt verschlüsselte Backups (PostgreSQL + MEDIA_ROOT) mit Rotation
#
# Verwendung:
#   ./backup.sh            Backup erstellen mit Rotation (DB + Medien)
#   ./backup.sh --verify   Letztes DB-Backup in temporaere DB wiederherstellen
#                          und das letzte Medien-Backup auf Integritaet pruefen
#
# Medien-Backup (Refs #720): MEDIA_ROOT (=/data/media im Container) wird via
# `tar` aus dem web-Container heraus gelesen, gzippt und mit demselben
# BACKUP_ENCRYPTION_KEY verschluesselt wie das DB-Backup. Ohne das Volume
# waeren Anhaenge beim naechsten Container-Recreate verloren — der Backup-
# Pfad sichert sowohl gegen Volume-Verlust als auch gegen Pod-Neustart.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_DIR}/backups"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

# A4.3: Encrypt-then-MAC-Helfer (backup_write_hmac / backup_verify_hmac).
# shellcheck source=scripts/ops/_backup_common.sh
source "${SCRIPT_DIR}/_backup_common.sh"

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
backup_require_real_key

# pg_dump muss als POSTGRES_ADMIN_USER (BYPASSRLS) laufen — sonst scheitert
# der Dump an FORCE ROW LEVEL SECURITY auf den Facility-Tabellen (Migrationen
# 0084/0085): die App-Rolle (NOBYPASSRLS) matcht ohne Session-GUC null Zeilen,
# pg_dump bricht ab und unter `set -euo pipefail` entsteht KEIN Backup.
# Fallback auf POSTGRES_USER fuer Alt-Setups ohne 2-Rollen-Modell (Review N4).
DUMP_USER="${POSTGRES_ADMIN_USER:-$POSTGRES_USER}"
export PGPASSWORD="${POSTGRES_ADMIN_PASSWORD:-${POSTGRES_PASSWORD:-}}"

# Der --verify-Zweig braucht CREATE/DROP DATABASE: beide App-Rollen sind
# NOCREATEDB (deploy/postgres-init/01-app-role.sh). Bootstrap-Superuser via
# Local-Socket-Trust im db-Container — wie scripts/ops/restore-drill.sh.
SU_USER="${POSTGRES_SUPERUSER:-postgres}"

# --verify: Letztes Backup in temporaere Datenbank wiederherstellen und pruefen
if [[ "${1:-}" == "--verify" ]]; then
    DAILY_DIR="${BACKUP_DIR}/daily"

    # Neuestes DB-Backup finden
    LATEST_BACKUP=$(find "$DAILY_DIR" -name "*.sql.gz.enc" -type f 2>/dev/null \
        | sort -r | head -n1)

    if [[ -z "$LATEST_BACKUP" ]]; then
        echo "FEHLER: Kein DB-Backup in ${DAILY_DIR} gefunden."
        exit 1
    fi

    echo "Verifiziere DB-Backup: ${LATEST_BACKUP}"

    VERIFY_DB="anlaufstelle_verify_$$"

    # Aufraeum-Trap: temporaere DB bei Fehler oder Abbruch droppen
    cleanup() {
        echo "Raeume temporaere Datenbank '${VERIFY_DB}' auf..."
        docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U "$SU_USER" -d postgres \
            -c "DROP DATABASE IF EXISTS \"${VERIFY_DB}\";" 2>/dev/null || true
    }
    trap cleanup EXIT

    # Temporaere Datenbank erstellen
    echo "Erstelle temporaere Datenbank: ${VERIFY_DB}"
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$SU_USER" -d postgres \
        -c "CREATE DATABASE \"${VERIFY_DB}\";"

    # A4.3: Integrität VOR dem Entschlüsseln prüfen.
    backup_verify_hmac "$LATEST_BACKUP"

    # Backup entschluesseln, entpacken und wiederherstellen
    echo "Stelle Backup in temporaere Datenbank wieder her..."
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
        -in "$LATEST_BACKUP" \
        | gunzip \
        | docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U "$SU_USER" "$VERIFY_DB" > /dev/null

    # Einfache Pruefabfrage: eine RLS-Tabelle zaehlen. core_facility (ohne
    # RLS) wuerde einen RLS-leeren Dump nicht erkennen (Review N4). Live-
    # Vergleich nur als Plausibilitaet: Live>0 aber Restore=0 heisst, der
    # Dump war RLS-leer.
    echo "Pruefe wiederhergestellte Daten (RLS-Tabelle core_client)..."
    RESTORED_CLIENT_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$SU_USER" -d "$VERIFY_DB" -t -A \
        -c "SELECT COUNT(*) FROM core_client;")
    LIVE_CLIENT_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$SU_USER" -d "$POSTGRES_DB" -t -A \
        -c "SELECT COUNT(*) FROM core_client;")

    if [[ -z "$RESTORED_CLIENT_COUNT" ]]; then
        echo "FEHLER: Pruefabfrage fehlgeschlagen."
        exit 1
    fi
    if [[ "$LIVE_CLIENT_COUNT" -gt 0 && "$RESTORED_CLIENT_COUNT" -eq 0 ]]; then
        echo "FEHLER: Backup enthaelt 0 core_client-Zeilen, Live-DB hat ${LIVE_CLIENT_COUNT} — Dump war RLS-leer (falsche Rolle?)."
        exit 1
    fi
    echo "Verifikation erfolgreich: ${RESTORED_CLIENT_COUNT} Klient(en) in core_client (Live: ${LIVE_CLIENT_COUNT})."
    echo "DB-Backup ist gueltig: ${LATEST_BACKUP}"

    # Medien-Backup integritaetspruefen — kein Restore noetig, nur tar-listen.
    LATEST_MEDIA=$(find "$DAILY_DIR" -name "*_media.tar.gz.enc" -type f 2>/dev/null \
        | sort -r | head -n1)
    if [[ -z "$LATEST_MEDIA" ]]; then
        echo "WARNUNG: Kein Medien-Backup in ${DAILY_DIR} gefunden."
    else
        echo "Verifiziere Medien-Backup: ${LATEST_MEDIA}"
        backup_verify_hmac "$LATEST_MEDIA"
        FILE_COUNT=$(openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
            -in "$LATEST_MEDIA" \
            | gunzip \
            | tar -tf - 2>/dev/null | wc -l)
        if [[ "$FILE_COUNT" -lt 1 ]]; then
            echo "FEHLER: Medien-Backup enthaelt 0 Eintraege oder ist beschaedigt."
            exit 1
        fi
        echo "Medien-Backup ist gueltig: ${FILE_COUNT} Eintrag/Eintraege im Archiv."
    fi
    exit 0
fi

TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
DAILY_DIR="${BACKUP_DIR}/daily"
WEEKLY_DIR="${BACKUP_DIR}/weekly"
MONTHLY_DIR="${BACKUP_DIR}/monthly"

mkdir -p "$DAILY_DIR" "$WEEKLY_DIR" "$MONTHLY_DIR"

BACKUP_FILE="${DAILY_DIR}/anlaufstelle_${TIMESTAMP}.sql.gz.enc"
MEDIA_FILE="${DAILY_DIR}/anlaufstelle_${TIMESTAMP}_media.tar.gz.enc"

# DB-Backup
echo "Erstelle DB-Backup: ${BACKUP_FILE}"
docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD db \
    pg_dump -U "$DUMP_USER" "$POSTGRES_DB" \
    | gzip \
    | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
    > "$BACKUP_FILE"

if [ ! -s "$BACKUP_FILE" ]; then
    echo "ERROR: DB-Backup-Datei ist leer oder fehlt."
    exit 1
fi

# A4.3: detached HMAC für Integritätsschutz (Encrypt-then-MAC).
backup_write_hmac "$BACKUP_FILE"

echo "DB-Backup erstellt: $(du -h "$BACKUP_FILE" | cut -f1)"

# Medien-Backup (Refs #720)
# `tar -C /data media` erzeugt Pfade relativ zu /data, dh. das Archiv enthaelt
# `media/...`-Eintraege ohne fuehrenden Slash. Restore kann das Archiv direkt
# in `/data` einer recreated Volume entpacken.
echo "Erstelle Medien-Backup: ${MEDIA_FILE}"
MEDIA_ROOT_CONTAINER="${MEDIA_ROOT_CONTAINER:-/data/media}"
MEDIA_PARENT="$(dirname "$MEDIA_ROOT_CONTAINER")"
MEDIA_NAME="$(basename "$MEDIA_ROOT_CONTAINER")"
docker compose -f "$COMPOSE_FILE" exec -T web \
    tar -C "$MEDIA_PARENT" -cf - "$MEDIA_NAME" \
    | gzip \
    | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
    > "$MEDIA_FILE"

if [ ! -s "$MEDIA_FILE" ]; then
    echo "ERROR: Medien-Backup-Datei ist leer oder fehlt."
    exit 1
fi

# A4.3: detached HMAC für Integritätsschutz (Encrypt-then-MAC).
backup_write_hmac "$MEDIA_FILE"

echo "Medien-Backup erstellt: $(du -h "$MEDIA_FILE" | cut -f1)"

# Rotation
DOW=$(date +%u)  # 1=Montag, 7=Sonntag
DOM=$(date +%d)  # Tag des Monats

# Sonntags → Weekly kopieren (DB + Medien + HMAC-Sidecars)
if [[ "$DOW" -eq 7 ]]; then
    cp "$BACKUP_FILE" "$BACKUP_FILE.hmac" "$MEDIA_FILE" "$MEDIA_FILE.hmac" "$WEEKLY_DIR/"
    echo "Weekly-Backup kopiert."
fi

# Am 1. des Monats → Monthly kopieren (DB + Medien + HMAC-Sidecars)
if [[ "$DOM" -eq "01" ]]; then
    cp "$BACKUP_FILE" "$BACKUP_FILE.hmac" "$MEDIA_FILE" "$MEDIA_FILE.hmac" "$MONTHLY_DIR/"
    echo "Monthly-Backup kopiert."
fi

# Alte Backups löschen — DB + Medien + HMAC-Sidecars parallel (A4.3)
find "$DAILY_DIR" \( -name "*.sql.gz.enc" -o -name "*.sql.gz.enc.hmac" \) -mtime +7 -delete
find "$DAILY_DIR" \( -name "*_media.tar.gz.enc" -o -name "*_media.tar.gz.enc.hmac" \) -mtime +7 -delete
find "$WEEKLY_DIR" \( -name "*.sql.gz.enc" -o -name "*.sql.gz.enc.hmac" \) -mtime +28 -delete
find "$WEEKLY_DIR" \( -name "*_media.tar.gz.enc" -o -name "*_media.tar.gz.enc.hmac" \) -mtime +28 -delete
find "$MONTHLY_DIR" \( -name "*.sql.gz.enc" -o -name "*.sql.gz.enc.hmac" \) -mtime +90 -delete
find "$MONTHLY_DIR" \( -name "*_media.tar.gz.enc" -o -name "*_media.tar.gz.enc.hmac" \) -mtime +90 -delete

echo "Rotation abgeschlossen."

# Off-Site-Sync (Refs #738; State-File + Sentry: Refs #797).
# BACKUP_OFFSITE_TARGET: rclone-Remote ("rclone:remote:bucket"),
# S3-URL ("s3://bucket/path") oder SCP-Target ("user@host:/path").
#
# Failure-Mode: jeder Fehler erhoeht einen persistenten Counter im
# State-File ``$BACKUP_STATE_DIR/.offsite_state``. Erst beim ZWEITEN
# aufeinanderfolgenden Fehler beendet das Skript mit Exit-Code 1 — das
# fuehrt zu einer sichtbaren Cron-/Coolify-Warnung. Einzelne transiente
# Fehler werden weiterhin nur geloggt, das lokale Backup ist gerettet
# und Off-Site darf beim naechsten Run nachziehen.
if [[ -n "${BACKUP_OFFSITE_TARGET:-}" ]]; then
    echo "Synce Tagesbackups nach: ${BACKUP_OFFSITE_TARGET}"
    OFFSITE_OK=true
    case "$BACKUP_OFFSITE_TARGET" in
        rclone:*)
            REMOTE="${BACKUP_OFFSITE_TARGET#rclone:}"
            rclone copy "$DAILY_DIR" "$REMOTE/daily" \
                --include "*.enc" --include "*.hmac" --transfers 2 \
                || { echo "ERROR: rclone copy fehlgeschlagen — Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            ;;
        s3://*)
            command -v aws >/dev/null 2>&1 || {
                echo "ERROR: aws-CLI nicht installiert — Off-Site (S3) nicht moeglich."; OFFSITE_OK=false; }
            if [[ "$OFFSITE_OK" == true ]]; then
                aws s3 sync "$DAILY_DIR" "${BACKUP_OFFSITE_TARGET}/daily" \
                    --exclude "*" --include "*.enc" --include "*.hmac" \
                    || { echo "ERROR: aws s3 sync fehlgeschlagen — Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            fi
            ;;
        *@*:*)
            # SCP-Target — kompatibel mit OpenSSH ssh/scp inkl. Schluessel-Auth via SSH-Agent.
            scp -q -B "$BACKUP_FILE" "$BACKUP_FILE.hmac" "$MEDIA_FILE" "$MEDIA_FILE.hmac" "${BACKUP_OFFSITE_TARGET%/}/" \
                || { echo "ERROR: scp fehlgeschlagen — Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            ;;
        *)
            echo "ERROR: BACKUP_OFFSITE_TARGET-Format nicht erkannt: ${BACKUP_OFFSITE_TARGET}"
            echo "  Erwartet: rclone:<remote:path>, s3://<bucket/path> oder <user@host:/path>"
            OFFSITE_OK=false
            ;;
    esac

    # State-File: Counter aufeinanderfolgender Fehler. Default-Pfad liegt
    # neben den Backups (BACKUP_DIR), kann aber per BACKUP_STATE_DIR
    # ueberschrieben werden (z.B. /var/lib/anlaufstelle).
    OFFSITE_STATE_FILE="${BACKUP_STATE_DIR:-$BACKUP_DIR}/.offsite_state"
    mkdir -p "$(dirname "$OFFSITE_STATE_FILE")"
    PREV_FAIL_COUNT=0
    if [[ -f "$OFFSITE_STATE_FILE" ]]; then
        PREV_FAIL_COUNT=$(cat "$OFFSITE_STATE_FILE" 2>/dev/null || echo 0)
    fi

    if [[ "$OFFSITE_OK" == true ]]; then
        echo "Off-Site-Sync erfolgreich."
        echo 0 > "$OFFSITE_STATE_FILE"
    else
        FAIL_COUNT=$((PREV_FAIL_COUNT + 1))
        echo "$FAIL_COUNT" > "$OFFSITE_STATE_FILE"
        echo "ERROR: Off-Site-Fehler #${FAIL_COUNT} — State persisted: ${OFFSITE_STATE_FILE}"
        # Sentry-Hook (optional) — wenn ein Hook-Skript hinterlegt ist und
        # SENTRY_DSN gesetzt ist, leiten wir den Fehler an den Operator weiter.
        # Kein hartes Bash-Sentry-SDK notwendig — der Operator kann z.B.
        # ``sentry-cli send-event`` oder ein eigenes curl-Skript einhaengen.
        if [[ -n "${SENTRY_DSN:-}" && -n "${BACKUP_SENTRY_HOOK:-}" && -x "${BACKUP_SENTRY_HOOK}" ]]; then
            "$BACKUP_SENTRY_HOOK" "Off-Site-Backup fehlgeschlagen (#${FAIL_COUNT})" \
                "${BACKUP_OFFSITE_TARGET}" || true
        fi
        if (( FAIL_COUNT >= 2 )); then
            echo "FATAL: Off-Site-Sync seit ${FAIL_COUNT} aufeinanderfolgenden Laeufen kaputt — Skript bricht ab."
            exit 1
        fi
    fi
fi
