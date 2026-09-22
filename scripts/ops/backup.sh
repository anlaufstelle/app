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
# SCRIPT_DIR ist <repo>/scripts/ops — der Repo-Root (mit docker-compose.prod.yml)
# liegt ZWEI Ebenen darueber. Frueher loeste ein einfaches
# ``dirname "$SCRIPT_DIR"`` nur auf <repo>/scripts auf, wodurch COMPOSE_FILE ins
# Leere zeigte und jedes Backup sofort scheiterte (Refs #1336).
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

# Fruehzeitiger, klarer Abbruch statt kryptischem docker-Fehler, falls die
# Pfadaufloesung daneben liegt oder das Skript verschoben wurde (Refs #1336).
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "FEHLER: Compose-Datei nicht gefunden: ${COMPOSE_FILE}" >&2
    echo "  Erwartet im Repo-Root — liegt backup.sh unter <repo>/scripts/ops/?" >&2
    exit 1
fi

# A4.3: Encrypt-then-MAC-Helfer (backup_write_hmac / backup_verify_hmac).
# shellcheck source=scripts/ops/_backup_common.sh
source "${SCRIPT_DIR}/_backup_common.sh"

# .env laden falls vorhanden
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

# Host- und Container-Sicht sind absichtlich getrennt: BACKUP_DIR aus .env
# bezeichnet das read-only Mount-Ziel im Container. Dieses Host-Skript nutzt
# ausschliesslich BACKUP_HOST_DIR; relative Werte beziehen sich auf den
# Repo-/Compose-Root (Refs #2021, #2022).
BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-${PROJECT_DIR}/backups}"
if [[ "$BACKUP_HOST_DIR" != /* ]]; then
    BACKUP_HOST_DIR="${PROJECT_DIR}/${BACKUP_HOST_DIR#./}"
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
    DAILY_DIR="${BACKUP_HOST_DIR}/daily"

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

    # Vollstaendigkeits-Guard (Refs #1336, DOC-P1): Zeilenzahl-Abgleich
    # kritischer RLS-Tabellen zwischen Restore und Live-DB. Der stille
    # Fehlerfall ist ein Dump unter einer NOBYPASSRLS-Rolle — er ist syntaktisch
    # valide, enthaelt aber 0 Zeilen der FORCE-RLS-Tabellen. core_facility (ohne
    # RLS) wuerde das NICHT erkennen (Review N4), daher werden mehrere
    # RLS-gebundene Nutzdaten-Tabellen geprueft. Signal: Live>0 aber Restore=0.
    echo "Vollstaendigkeits-Guard: RLS-Tabellen Restore vs. Live abgleichen..."
    RLS_VERIFY_TABLES=(core_client core_event core_workitem core_auditlog)
    VERIFY_FAILED=0
    for tbl in "${RLS_VERIFY_TABLES[@]}"; do
        RESTORED_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U "$SU_USER" -d "$VERIFY_DB" -t -A \
            -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
        LIVE_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U "$SU_USER" -d "$POSTGRES_DB" -t -A \
            -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
        if [[ -z "$RESTORED_COUNT" || ! "$RESTORED_COUNT" =~ ^[0-9]+$ ]]; then
            echo "FEHLER: Pruefabfrage fuer ${tbl} fehlgeschlagen (Restore nicht abfragbar)."
            VERIFY_FAILED=1
            continue
        fi
        if [[ "$LIVE_COUNT" =~ ^[0-9]+$ && "$LIVE_COUNT" -gt 0 && "$RESTORED_COUNT" -eq 0 ]]; then
            echo "FEHLER: ${tbl} — Backup 0 Zeilen, Live ${LIVE_COUNT}: Dump war RLS-leer (falsche Dump-Rolle?)."
            VERIFY_FAILED=1
        else
            echo "  OK ${tbl}: Restore=${RESTORED_COUNT} (Live=${LIVE_COUNT:-n/a})."
        fi
    done
    if [[ "$VERIFY_FAILED" -ne 0 ]]; then
        echo "FEHLER: Vollstaendigkeits-Guard fehlgeschlagen — Backup unvollstaendig oder nicht wiederherstellbar."
        exit 1
    fi
    echo "Vollstaendigkeits-Guard bestanden: gepruefte RLS-Tabellen konsistent."
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
DAILY_DIR="${BACKUP_HOST_DIR}/daily"
WEEKLY_DIR="${BACKUP_HOST_DIR}/weekly"
MONTHLY_DIR="${BACKUP_HOST_DIR}/monthly"

mkdir -p "$DAILY_DIR" "$WEEKLY_DIR" "$MONTHLY_DIR"

# Erfolgsmetadaten erst nach vollstaendig erzeugtem Satz veroeffentlichen
# (Refs #2291): Alle Artefakte entstehen in einem Arbeitsverzeichnis und werden
# erst am Ende in das Tagesverzeichnis verschoben — das signierte Manifest als
# letztes. Ein abgebrochener Lauf hinterlaesst dadurch KEIN Artefakt, das
# Health-/Compliance-Anzeige als frisches Backup lesen koennten.
WORK_DIR="${DAILY_DIR}/.in-progress-${TIMESTAMP}"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cleanup_work_dir() { rm -rf "$WORK_DIR"; }
trap cleanup_work_dir EXIT

BACKUP_FILE="${WORK_DIR}/anlaufstelle_${TIMESTAMP}.sql.gz.enc"
MEDIA_FILE="${WORK_DIR}/anlaufstelle_${TIMESTAMP}_media.tar.gz.enc"
MANIFEST_FILE="${WORK_DIR}/anlaufstelle_${TIMESTAMP}.manifest"

# Absoluter Referenzstand fuer den Restore-Drill. Der Drill vergleicht den
# Restore spaeter gegen diese Werte, nicht gegen eine moeglicherweise bereits
# aus demselben Backup aufgebaute Live-Datenbank. Signiert wird das Manifest
# erst nach Erzeugung aller Satzartefakte, damit es deren Kennung und Hashes
# kryptografisch bindet. Die Betriebsanleitung fordert fuer den Satz weiterhin
# Schreibruhe; das Manifest ersetzt keinen Snapshot ueber DB und Medien.
MANIFEST_TABLES=(core_facility core_client core_event core_workitem core_auditlog core_attachment)
: > "$MANIFEST_FILE"
for tbl in "${MANIFEST_TABLES[@]}"; do
    MANIFEST_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD db \
        psql -v ON_ERROR_STOP=1 -U "$DUMP_USER" -d "$POSTGRES_DB" -t -A \
        -c "SELECT COUNT(*) FROM ${tbl};" | tr -d '[:space:]')
    if [[ ! "$MANIFEST_COUNT" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Zeilenzahl-Manifest ist unvollstaendig (${tbl})."
        exit 1
    fi
    printf 'table_%s=%s\n' "$tbl" "$MANIFEST_COUNT" >> "$MANIFEST_FILE"
done

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

# Audit-High-Water-Mark (Refs #2022): Die signierte JSON-Datei liegt im
# persistenten /data-Volume des cron-Containers. Der Host kann das Named Volume
# nicht direkt lesen, deshalb wird sie ueber den Container exportiert. Fehlende
# Konfiguration/Datei macht das regulaere DB-/Medien-Backup nicht unbrauchbar,
# wird aber sichtbar geloggt.
HWM_FILE=""
if [[ -n "${DJANGO_AUDIT_HWM_PATH:-}" ]]; then
    HWM_FILE="${WORK_DIR}/audit_hwm-${TIMESTAMP}.json"
    if docker compose -f "$COMPOSE_FILE" exec -T cron cat "$DJANGO_AUDIT_HWM_PATH" > "$HWM_FILE"; then
        if [[ -s "$HWM_FILE" ]]; then
            echo "Audit-HWM gesichert: ${HWM_FILE}"
        else
            echo "INFO: Audit-HWM unter ${DJANGO_AUDIT_HWM_PATH} ist leer — kein HWM-Artefakt gesichert."
            rm -f "$HWM_FILE"
            HWM_FILE=""
        fi
    else
        echo "INFO: Audit-HWM unter ${DJANGO_AUDIT_HWM_PATH} fehlt oder ist nicht lesbar — DB-/Medien-Backup bleibt gueltig."
        rm -f "$HWM_FILE"
        HWM_FILE=""
    fi
else
    echo "INFO: DJANGO_AUDIT_HWM_PATH ist nicht gesetzt — kein Audit-HWM-Artefakt gesichert."
fi

# Satzbindung: Die signierte Nutzlast nennt den ausgewaehlten Zeitstempel und
# bindet mindestens den verschluesselten Dump, daneben auch Medien und HWM,
# sofern diese Artefakte Teil des Satzes sind.
{
    printf 'backup_timestamp=%s\n' "$TIMESTAMP"
    printf 'db_sha256=%s\n' "$(sha256sum "$BACKUP_FILE" | awk '{print $1}')"
    printf 'media_sha256=%s\n' "$(sha256sum "$MEDIA_FILE" | awk '{print $1}')"
    if [[ -n "$HWM_FILE" ]]; then
        printf 'hwm_sha256=%s\n' "$(sha256sum "$HWM_FILE" | awk '{print $1}')"
    fi
} >> "$MANIFEST_FILE"
backup_write_hmac "$MANIFEST_FILE"

# Veroeffentlichung: erst die Nutzartefakte, das signierte Manifest zuletzt.
# Es ist der Marker fuer "Satz vollstaendig" — solange es fehlt, gilt der Satz
# als nicht erfolgreich (Refs #2291).
publish_artifact() {
    local source="$1"
    mv -f "$source" "${DAILY_DIR}/${source##*/}"
    printf '%s/%s\n' "$DAILY_DIR" "${source##*/}"
}
BACKUP_FILE_HMAC="$(publish_artifact "${BACKUP_FILE}.hmac")"
BACKUP_FILE="$(publish_artifact "$BACKUP_FILE")"
MEDIA_FILE_HMAC="$(publish_artifact "${MEDIA_FILE}.hmac")"
MEDIA_FILE="$(publish_artifact "$MEDIA_FILE")"
if [[ -n "$HWM_FILE" ]]; then
    HWM_FILE="$(publish_artifact "$HWM_FILE")"
fi
MANIFEST_FILE_HMAC="$(publish_artifact "${MANIFEST_FILE}.hmac")"
MANIFEST_FILE="$(publish_artifact "$MANIFEST_FILE")"
echo "Zeilenzahl-Manifest mit Satzbindung erstellt: ${MANIFEST_FILE}"

# Weekly-/Monthly-Snapshots werden vor dem Off-Site-Abgleich angelegt, damit
# alle drei Retention-Stufen im selben Lauf uebertragen werden koennen.
DOW=$(date +%u)  # 1=Montag, 7=Sonntag
DOM=$(date +%d)  # Tag des Monats

# Sonntags → Weekly kopieren (DB + Medien + Manifest + HMAC-Sidecars)
if [[ "$DOW" -eq 7 ]]; then
    cp "$BACKUP_FILE" "$BACKUP_FILE_HMAC" "$MEDIA_FILE" "$MEDIA_FILE_HMAC" \
        "$MANIFEST_FILE" "$MANIFEST_FILE_HMAC" "$WEEKLY_DIR/"
    if [[ -n "$HWM_FILE" ]]; then
        cp "$HWM_FILE" "$WEEKLY_DIR/"
    fi
    echo "Weekly-Backup kopiert."
fi

# Am 1. des Monats → Monthly kopieren (DB + Medien + Manifest + HMAC-Sidecars)
if [[ "$DOM" -eq "01" ]]; then
    cp "$BACKUP_FILE" "$BACKUP_FILE_HMAC" "$MEDIA_FILE" "$MEDIA_FILE_HMAC" \
        "$MANIFEST_FILE" "$MANIFEST_FILE_HMAC" "$MONTHLY_DIR/"
    if [[ -n "$HWM_FILE" ]]; then
        cp "$HWM_FILE" "$MONTHLY_DIR/"
    fi
    echo "Monthly-Backup kopiert."
fi

# Off-Site-Sync (Refs #738; State-File + Sentry: Refs #797).
# BACKUP_OFFSITE_TARGET: rclone-Remote ("rclone:remote:bucket"),
# S3-URL ("s3://bucket/path") oder SCP-Target ("user@host:/path").
#
# Failure-Mode: jeder Fehler erhoeht einen persistenten Counter im
# State-File ``$BACKUP_STATE_DIR/.offsite_state``. Erst beim ZWEITEN
# aufeinanderfolgenden Fehler beendet das Skript mit Exit-Code 1 — das
# fuehrt zu einer sichtbaren Cron-Warnung. Einzelne transiente
# Fehler werden weiterhin nur geloggt, das lokale Backup ist gerettet
# und Off-Site darf beim naechsten Run nachziehen.
OFFSITE_OK=true
if [[ -n "${BACKUP_OFFSITE_TARGET:-}" ]]; then
    echo "Synce Daily-, Weekly- und Monthly-Backups nach: ${BACKUP_OFFSITE_TARGET}"
    case "$BACKUP_OFFSITE_TARGET" in
        rclone:*)
            REMOTE="${BACKUP_OFFSITE_TARGET#rclone:}"
            rclone copy "$DAILY_DIR" "$REMOTE/daily" \
                --include "*.enc" --include "*.hmac" --include "*.manifest" --include "audit_hwm-*.json" --transfers 2 \
                || { echo "ERROR: rclone copy fehlgeschlagen — Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            rclone copy "$WEEKLY_DIR" "$REMOTE/weekly" \
                --include "*.enc" --include "*.hmac" --include "*.manifest" --include "audit_hwm-*.json" --transfers 2 \
                || { echo "ERROR: rclone copy fehlgeschlagen — Weekly-Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            rclone copy "$MONTHLY_DIR" "$REMOTE/monthly" \
                --include "*.enc" --include "*.hmac" --include "*.manifest" --include "audit_hwm-*.json" --transfers 2 \
                || { echo "ERROR: rclone copy fehlgeschlagen — Monthly-Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            ;;
        s3://*)
            command -v aws >/dev/null 2>&1 || {
                echo "ERROR: aws-CLI nicht installiert — Off-Site (S3) nicht moeglich."; OFFSITE_OK=false; }
            if [[ "$OFFSITE_OK" == true ]]; then
                aws s3 sync "$DAILY_DIR" "${BACKUP_OFFSITE_TARGET}/daily" \
                    --exclude "*" --include "*.enc" --include "*.hmac" --include "*.manifest" --include "audit_hwm-*.json" \
                    || { echo "ERROR: aws s3 sync fehlgeschlagen — Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
                aws s3 sync "$WEEKLY_DIR" "${BACKUP_OFFSITE_TARGET}/weekly" \
                    --exclude "*" --include "*.enc" --include "*.hmac" --include "*.manifest" --include "audit_hwm-*.json" \
                    || { echo "ERROR: aws s3 sync fehlgeschlagen — Weekly-Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
                aws s3 sync "$MONTHLY_DIR" "${BACKUP_OFFSITE_TARGET}/monthly" \
                    --exclude "*" --include "*.enc" --include "*.hmac" --include "*.manifest" --include "audit_hwm-*.json" \
                    || { echo "ERROR: aws s3 sync fehlgeschlagen — Monthly-Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            fi
            ;;
        *@*:*)
            # SCP gleicht ebenfalls alle Retention-Stufen ab, sodass ein nach
            # einem transienten Fehler ausgelassener Satz nachgeholt wird.
            scp -q -B -r "$DAILY_DIR" "$WEEKLY_DIR" "$MONTHLY_DIR" \
                "${BACKUP_OFFSITE_TARGET%/}/" \
                || { echo "ERROR: scp fehlgeschlagen — Off-Site nicht aktualisiert."; OFFSITE_OK=false; }
            ;;
        *)
            echo "ERROR: BACKUP_OFFSITE_TARGET-Format nicht erkannt: ${BACKUP_OFFSITE_TARGET}"
            echo "  Erwartet: rclone:<remote:path>, s3://<bucket/path> oder <user@host:/path>"
            OFFSITE_OK=false
            ;;
    esac

    # State-File: Counter aufeinanderfolgender Fehler. Default-Pfad liegt
    # neben den Host-Backups (BACKUP_HOST_DIR), kann aber per BACKUP_STATE_DIR
    # ueberschrieben werden (z.B. /var/lib/anlaufstelle).
    OFFSITE_STATE_FILE="${BACKUP_STATE_DIR:-$BACKUP_HOST_DIR}/.offsite_state"
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

# Rotation erst nach einem erfolgreichen Off-Site-Abgleich. Schon der erste
# Sync-Fehler setzt die lokale Loeschung aus, auch wenn er fuer die bestehende
# Zwei-Fehler-Alarmierung noch keinen harten Exit ausloest.
if [[ "$OFFSITE_OK" == false ]]; then
    echo "WARNUNG: Rotation ausgesetzt — Off-Site-Sync war nicht vollstaendig."
else
    find "$DAILY_DIR" \( -name "*.sql.gz.enc" -o -name "*.sql.gz.enc.hmac" \) -mtime +7 -delete
    find "$DAILY_DIR" \( -name "*_media.tar.gz.enc" -o -name "*_media.tar.gz.enc.hmac" \) -mtime +7 -delete
    find "$DAILY_DIR" \( -name "*.manifest" -o -name "*.manifest.hmac" \) -mtime +7 -delete
    find "$WEEKLY_DIR" \( -name "*.sql.gz.enc" -o -name "*.sql.gz.enc.hmac" \) -mtime +28 -delete
    find "$WEEKLY_DIR" \( -name "*_media.tar.gz.enc" -o -name "*_media.tar.gz.enc.hmac" \) -mtime +28 -delete
    find "$WEEKLY_DIR" \( -name "*.manifest" -o -name "*.manifest.hmac" \) -mtime +28 -delete
    find "$MONTHLY_DIR" \( -name "*.sql.gz.enc" -o -name "*.sql.gz.enc.hmac" \) -mtime +90 -delete
    find "$MONTHLY_DIR" \( -name "*_media.tar.gz.enc" -o -name "*_media.tar.gz.enc.hmac" \) -mtime +90 -delete
    find "$MONTHLY_DIR" \( -name "*.manifest" -o -name "*.manifest.hmac" \) -mtime +90 -delete
    find "$DAILY_DIR" -name "audit_hwm-*.json" -mtime +7 -delete
    find "$WEEKLY_DIR" -name "audit_hwm-*.json" -mtime +28 -delete
    find "$MONTHLY_DIR" -name "audit_hwm-*.json" -mtime +90 -delete
    echo "Rotation abgeschlossen."
fi
