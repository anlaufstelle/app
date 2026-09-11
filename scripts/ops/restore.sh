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

# Wenn ein Audit-HWM konfiguriert ist, gehoert ausschliesslich die Datei mit
# demselben Zeitstempel wie der DB-Dump zum Restore-Satz. Fehlt sie, darf der
# Zielhost nicht still ohne externen Anker weiterlaufen.
BACKUP_TIMESTAMP="${BACKUP_FILE##*/}"
BACKUP_TIMESTAMP="${BACKUP_TIMESTAMP#anlaufstelle_}"
BACKUP_TIMESTAMP="${BACKUP_TIMESTAMP%.sql.gz.enc}"
HWM_FILE=""
if [[ -n "${DJANGO_AUDIT_HWM_PATH:-}" ]]; then
    if [[ "${BACKUP_FILE##*/}" != "anlaufstelle_${BACKUP_TIMESTAMP}.sql.gz.enc" ]]; then
        echo "FEHLER: DB-Backup-Dateiname enthaelt keinen auswertbaren Zeitstempel fuer den Audit-HWM." >&2
        exit 1
    fi
    HWM_FILE="${BACKUP_FILE%/*}/audit_hwm-${BACKUP_TIMESTAMP}.json"
    if [[ "$BACKUP_FILE" != */* ]]; then
        HWM_FILE="./audit_hwm-${BACKUP_TIMESTAMP}.json"
    fi
    if [[ ! -f "$HWM_FILE" ]]; then
        echo "FEHLER: Zum DB-Backup gehoerender Audit-HWM fehlt: ${HWM_FILE}" >&2
        exit 1
    fi
fi

# Der Plain-SQL-Dump enthaelt OWNER-TO-/FORCE-RLS-Statements und COPY in
# FORCE-RLS-Tabellen — das scheitert unter der App-Rolle (NOBYPASSRLS, kein
# DDL-Owner-Recht). Bootstrap-Superuser via Local-Socket-Trust im
# db-Container — wie scripts/ops/restore-drill.sh (Review N4).
SU_USER="${POSTGRES_SUPERUSER:-postgres}"

# Beide Artefakte authentifizieren, bevor die Bestaetigung abgefragt und die
# Datenbank als erste destruktive Aktion eingespielt wird (Refs #2195).
echo "Prüfe Integrität des DB-Backups (HMAC)..."
backup_verify_hmac "$BACKUP_FILE"
if [[ -n "$MEDIA_FILE" ]]; then
    echo "Prüfe Integrität des Medien-Backups (HMAC)..."
    backup_verify_hmac "$MEDIA_FILE"
fi

echo "WARNUNG: Die Datenbank '${POSTGRES_DB}' wird überschrieben!"
echo "DB-Backup:    ${BACKUP_FILE}"
if [[ -n "$HWM_FILE" ]]; then
    echo "Audit-HWM:    ${HWM_FILE}"
fi
if [[ -n "$MEDIA_FILE" ]]; then
    echo "WARNUNG: Der MEDIA_ROOT-Inhalt wird ueberschrieben!"
    echo "Medien-Backup: ${MEDIA_FILE}"
fi
read -rp "Fortfahren? (ja/nein): " CONFIRM

if [[ "$CONFIRM" != "ja" ]]; then
    echo "Abgebrochen."
    exit 0
fi

# DB restore — beide HMAC-Sidecars wurden in der Vorpruefung verifiziert.
echo "Stelle DB-Backup wieder her..."
# Direkt vor der Nutzung erneut pruefen, damit die interaktive Wartezeit das
# Pruef-Nutzungs-Fenster nicht unnoetig vergroessert (Refs #2195).
backup_verify_hmac "$BACKUP_FILE"
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$BACKUP_FILE" \
    | gunzip \
    | docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -v ON_ERROR_STOP=1 --single-transaction -U "$SU_USER" "$POSTGRES_DB"
echo "DB-Wiederherstellung abgeschlossen."

if [[ -n "$HWM_FILE" ]]; then
    echo "Spiele Audit-HWM ${HWM_FILE##*/} nach ${DJANGO_AUDIT_HWM_PATH} ..."
    docker compose -f "$COMPOSE_FILE" run --rm --entrypoint sh -T maintenance \
        -c "umask 077; cat > '$DJANGO_AUDIT_HWM_PATH'" < "$HWM_FILE"
    echo "Audit-HWM wiederhergestellt: ${HWM_FILE##*/}"
fi

# Medien-Restore (Refs #720)
if [[ -n "$MEDIA_FILE" ]]; then
    MEDIA_ROOT_CONTAINER="${MEDIA_ROOT_CONTAINER:-/data/media}"
    MEDIA_STAGE="${MEDIA_ROOT_CONTAINER}.restore.$$"
    MEDIA_OLD="${MEDIA_ROOT_CONTAINER}.old.$$"

    echo "Prüfe Integrität des Medien-Backups erneut unmittelbar vor der Nutzung (HMAC)..."
    backup_verify_hmac "$MEDIA_FILE"

    # Zuerst neben dem aktiven App-Pfad entpacken. Der bisherige Bestand bleibt
    # bis zur vollstaendigen Extraktion und zum DB-/Datei-Abgleich unangetastet.
    docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_STAGE" "$MEDIA_OLD"
    docker compose -f "$COMPOSE_FILE" exec -T web mkdir -p "$MEDIA_STAGE"
    echo "Entpacke Medien-Backup nach ${MEDIA_STAGE} ..."
    if ! openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$MEDIA_FILE" \
        | gunzip \
        | docker compose -f "$COMPOSE_FILE" exec -T web \
            tar -C "$MEDIA_STAGE" --strip-components=1 -xf -; then
        docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_STAGE"
        echo "FEHLER: Medien-Backup konnte nicht vollstaendig entpackt werden." >&2
        exit 1
    fi

    # Jede restaurierte Anhangzeile muss am konfigurierten App-Pfad eine Datei
    # besitzen. Erst nach diesem fail-closed Abgleich wird der Bestand getauscht.
    ATTACHMENT_PATHS=$(docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -v ON_ERROR_STOP=1 -U "$SU_USER" -d "$POSTGRES_DB" -t -A \
        -c "SELECT storage_filename FROM core_attachment ORDER BY storage_filename;")
    while IFS= read -r storage_filename; do
        [[ -z "$storage_filename" ]] && continue
        case "$storage_filename" in
            /* | ../* | */../* | */..)
                docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_STAGE"
                echo "FEHLER: Unsicherer Anhangpfad in core_attachment: ${storage_filename}" >&2
                exit 1
                ;;
        esac
        if ! docker compose -f "$COMPOSE_FILE" exec -T web \
            test -f "${MEDIA_STAGE}/$storage_filename"; then
            docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_STAGE"
            echo "FEHLER: Datei fuer DB-Anhang fehlt im Medien-Backup: ${storage_filename}" >&2
            exit 1
        fi
    done <<< "$ATTACHMENT_PATHS"

    HAD_OLD_MEDIA=0
    if docker compose -f "$COMPOSE_FILE" exec -T web test -e "$MEDIA_ROOT_CONTAINER"; then
        docker compose -f "$COMPOSE_FILE" exec -T web mv "$MEDIA_ROOT_CONTAINER" "$MEDIA_OLD"
        HAD_OLD_MEDIA=1
    fi
    if ! docker compose -f "$COMPOSE_FILE" exec -T web mv "$MEDIA_STAGE" "$MEDIA_ROOT_CONTAINER"; then
        if [[ "$HAD_OLD_MEDIA" -eq 1 ]]; then
            docker compose -f "$COMPOSE_FILE" exec -T web mv "$MEDIA_OLD" "$MEDIA_ROOT_CONTAINER" || true
        fi
        echo "FEHLER: Validierter Medienbestand konnte nicht aktiviert werden." >&2
        exit 1
    fi
    if [[ "$HAD_OLD_MEDIA" -eq 1 ]]; then
        docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_OLD"
    fi
    echo "Medien-Wiederherstellung abgeschlossen."
fi
