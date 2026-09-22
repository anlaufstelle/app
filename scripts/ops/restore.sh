#!/usr/bin/env bash
set -euo pipefail

# Anlaufstelle Restore-Skript
# Stellt verschlüsselte DB- und Medien-Backups wieder her.
#
# Verwendung:
#   $0 <db-backup.sql.gz.enc>                       # Nur DB
#   $0 <db-backup.sql.gz.enc> <media.tar.gz.enc>    # DB + Medien (Refs #720)
#
# Medien-Restore entpackt neben dem existierenden /data/media-Inhalt, prueft
# jeden DB-Anhang und ersetzt den Bestand erst danach. Erwartet die im backup.sh
# erzeugte Struktur (`media/...` relativ zu `/data`).

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

# Bestaetigung (Refs #2318): Ein per Pipe zugefuehrtes "ja" wurde frueher von
# einem vorgelagerten ``docker compose exec -T``-Aufruf verbraucht; die Kette
# brach danach ab — nachdem die Ziel-Datenbank bereits geleert war. Deshalb
# gibt es jetzt einen expliziten Schalter (``--yes`` bzw. ``RESTORE_CONFIRM=ja``),
# alle Vorpruefungen lesen nichts mehr von stdin, und JEDER zerstoerende Schritt
# liegt hinter der Bestaetigung.
ASSUME_YES=0
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes) ASSUME_YES=1; shift ;;
        --) shift; POSITIONAL+=("$@"); break ;;
        --*) echo "Fehler: unbekannte Option: $1 (erlaubt: --yes)" >&2; exit 1 ;;
        *) POSITIONAL+=("$1"); shift ;;
    esac
done
if [[ ${#POSITIONAL[@]} -lt 1 || ${#POSITIONAL[@]} -gt 2 ]]; then
    echo "Verwendung: $0 [--yes] <db-backup.sql.gz.enc> [media-backup.tar.gz.enc]"
    echo "  --yes            Bestaetigung ohne Rueckfrage (alternativ RESTORE_CONFIRM=ja)"
    exit 1
fi

BACKUP_FILE="${POSITIONAL[0]}"
MEDIA_FILE="${POSITIONAL[1]:-}"

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "Fehler: DB-Backup-Datei nicht gefunden: $BACKUP_FILE"
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
MEDIA_ROOT_CONTAINER="${MEDIA_ROOT_CONTAINER:-/data/media}"
backup_require_real_key

# Ein vorhandenes Manifest ist Teil des authentifizierten Backup-Satzes. Wenn
# es einen Medien-Hash enthaelt, sind Medien-Archiv und Sidecar verpflichtend;
# nur ein Manifest ohne Medien-Hash beschreibt einen DB-only-Satz.
MANIFEST_FILE="${BACKUP_FILE%.sql.gz.enc}.manifest"
MANIFEST_MEDIA_SHA256=""
if [[ -f "$MANIFEST_FILE" ]]; then
    echo "Prüfe Integrität des Backup-Manifests (HMAC)..."
    backup_verify_hmac "$MANIFEST_FILE"
    while IFS='=' read -r key value extra; do
        if [[ -n "$extra" ]]; then
            echo "FEHLER: Backup-Manifest ungueltig: ${MANIFEST_FILE}" >&2
            exit 1
        fi
        if [[ "$key" == "media_sha256" ]]; then
            MANIFEST_MEDIA_SHA256="$value"
        fi
    done < "$MANIFEST_FILE"
    if [[ -n "$MANIFEST_MEDIA_SHA256" ]]; then
        if [[ ! "$MANIFEST_MEDIA_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
            echo "FEHLER: Manifest-Medien-Hash ist ungueltig: ${MANIFEST_FILE}" >&2
            exit 1
        elif [[ -z "$MEDIA_FILE" || ! -f "$MEDIA_FILE" ]]; then
            echo "FEHLER: Satz unvollstaendig: Medien-Archiv fehlt." >&2
            exit 1
        elif [[ ! -f "${MEDIA_FILE}.hmac" ]]; then
            echo "FEHLER: Satz unvollstaendig: Medien-Sidecar fehlt: ${MEDIA_FILE}.hmac" >&2
            exit 1
        elif [[ "$(sha256sum "$MEDIA_FILE" | awk '{print $1}')" != "$MANIFEST_MEDIA_SHA256" ]]; then
            echo "FEHLER: Manifest-Medien-Hash passt nicht zum gewaehlten Satz: ${MEDIA_FILE}" >&2
            exit 1
        fi
    fi
fi

if [[ -n "$MEDIA_FILE" && ! -f "$MEDIA_FILE" ]]; then
    echo "Fehler: Medien-Backup-Datei nicht gefunden: $MEDIA_FILE"
    exit 1
fi

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
    if [[ ! -s "$HWM_FILE" ]]; then
        echo "FEHLER: Zum DB-Backup gehoerender Audit-HWM ist leer: ${HWM_FILE}" >&2
        exit 1
    fi
fi

# Der Plain-SQL-Dump enthaelt OWNER-TO-/FORCE-RLS-Statements und COPY in
# FORCE-RLS-Tabellen — das scheitert unter der App-Rolle (NOBYPASSRLS, kein
# DDL-Owner-Recht). Bootstrap-Superuser via Local-Socket-Trust im
# db-Container — wie scripts/ops/restore-drill.sh (Review N4).
SU_USER="${POSTGRES_SUPERUSER:-postgres}"

validate_or_install_hwm() {
    local mode="$1"
    local expected_size expected_sha256
    expected_size=$(wc -c < "$HWM_FILE" | tr -d '[:space:]')
    expected_sha256=$(sha256sum "$HWM_FILE" | awk '{print $1}')
    HWM_EXPECTED_SIZE="$expected_size"
    HWM_EXPECTED_SHA256="$expected_sha256"
    docker compose -f "$COMPOSE_FILE" run --rm --entrypoint sh -T maintenance \
        -c '
            set -eu
            mode="$1"; target="$2"; expected_size="$3"; expected_sha256="$4"
            HWM_CHECK="/tmp/hwm-check.$$"
            trap '\''rm -f "$HWM_CHECK" "${HWM_TEMP:-}"'\'' EXIT HUP INT TERM
            umask 077
            cat > "$HWM_CHECK"
            DJANGO_AUDIT_HWM_PATH="$HWM_CHECK" python manage.py shell -c '\''from core.services.audit.hwm import HighWaterMarkStore; import os; HighWaterMarkStore.load(os.environ["DJANGO_AUDIT_HWM_PATH"])'\''
            python -c '\''import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
chains = data.get("chains")
valid_chain = lambda value: isinstance(value, dict) and isinstance(value.get("count"), int) and isinstance(value.get("last_entry_hash"), str)
if data.get("version") != 1 or not isinstance(chains, dict) or not all(valid_chain(value) for value in chains.values()):
    raise SystemExit("Audit-HWM: Pflichtfelder oder Feldtypen ungueltig")'\'' "$HWM_CHECK"
            if test "$mode" = validate; then
                exit 0
            fi
            target_dir=$(dirname "$target")
            HWM_TEMP="${target_dir}/.audit-hwm-restore.$$"
            cat "$HWM_CHECK" > "$HWM_TEMP"
            actual_size=$(wc -c < "$HWM_TEMP" | tr -d "[:space:]")
            actual_sha256=$(sha256sum "$HWM_TEMP" | cut -d " " -f 1)
            test "$actual_size" = "$expected_size"
            test "$actual_sha256" = "$expected_sha256"
            mv -f "$HWM_TEMP" "$target"
        ' sh "$mode" "$DJANGO_AUDIT_HWM_PATH" "$HWM_EXPECTED_SIZE" "$HWM_EXPECTED_SHA256" < "$HWM_FILE"
}

# Beide Artefakte authentifizieren, bevor die Bestaetigung abgefragt und die
# Datenbank als erste destruktive Aktion eingespielt wird (Refs #2195).
echo "Prüfe Integrität des DB-Backups (HMAC)..."
backup_verify_hmac "$BACKUP_FILE"
if [[ -n "$MEDIA_FILE" ]]; then
    echo "Prüfe Integrität des Medien-Backups (HMAC)..."
    backup_verify_hmac "$MEDIA_FILE"
fi
if [[ -n "$HWM_FILE" ]]; then
    echo "Validiere Audit-HWM vor der Wiederherstellung ..."
    validate_or_install_hwm validate
fi
if [[ -n "$MEDIA_FILE" ]]; then
    MEDIA_ROOT_EFFECTIVE=""
    # Ein Sentinel hinter dem Wert verhindert, dass die Kommandosubstitution
    # abschliessende Zeilenumbrueche entfernt und ein abweichender Pfad als
    # gleich durchgeht (Refs https://github.com/anlaufstelle/app/issues/2024).
    # ``< /dev/null``: die Vorpruefung darf eine per Pipe zugefuehrte Bestaetigung
    # nicht aufbrauchen (Refs #2318).
    if ! MEDIA_ROOT_EFFECTIVE=$(timeout 30s docker compose -f "$COMPOSE_FILE" exec -T web \
        sh -c 'printf "%s@" "$MEDIA_ROOT"' < /dev/null) || [[ "$MEDIA_ROOT_EFFECTIVE" != *@ ]]; then
        echo "FEHLER: MEDIA_ROOT des web-Containers konnte nicht ermittelt werden." >&2
        echo "  MEDIA_ROOT='<nicht ermittelbar>', MEDIA_ROOT_CONTAINER='${MEDIA_ROOT_CONTAINER}'." >&2
        echo "  Werte in ${PROJECT_DIR}/.env pruefen und angleichen." >&2
        exit 1
    fi
    MEDIA_ROOT_EFFECTIVE="${MEDIA_ROOT_EFFECTIVE%@}"
    if [[ -z "$MEDIA_ROOT_EFFECTIVE" ]]; then
        echo "FEHLER: MEDIA_ROOT des web-Containers ist leer." >&2
        echo "  MEDIA_ROOT='<leer>', MEDIA_ROOT_CONTAINER='${MEDIA_ROOT_CONTAINER}'." >&2
        echo "  Werte in ${PROJECT_DIR}/.env pruefen und angleichen." >&2
        exit 1
    fi
    if [[ "$MEDIA_ROOT_EFFECTIVE" != "$MEDIA_ROOT_CONTAINER" ]]; then
        echo "FEHLER: Medienpfade weichen voneinander ab." >&2
        echo "  MEDIA_ROOT='${MEDIA_ROOT_EFFECTIVE}', MEDIA_ROOT_CONTAINER='${MEDIA_ROOT_CONTAINER}'." >&2
        echo "  Werte in ${PROJECT_DIR}/.env pruefen und angleichen." >&2
        exit 1
    fi
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
RESTORE_CONFIRMED=0
if [[ "$ASSUME_YES" -eq 1 || "${RESTORE_CONFIRM:-}" == "ja" ]]; then
    echo "Bestaetigung liegt vor (--yes bzw. RESTORE_CONFIRM=ja)."
    RESTORE_CONFIRMED=1
elif read -r -t "${RESTORE_CONFIRM_TIMEOUT:-300}" -p "Fortfahren? (ja/nein): " CONFIRM \
    && [[ "$CONFIRM" == "ja" ]]; then
    RESTORE_CONFIRMED=1
fi

if [[ "$RESTORE_CONFIRMED" -ne 1 ]]; then
    echo "Abgebrochen — ohne Bestaetigung wird nichts veraendert." >&2
    echo "  Nicht-interaktiv: --yes uebergeben oder RESTORE_CONFIRM=ja setzen." >&2
    exit 1
fi

# --- Ab hier beginnen die zerstoerenden Schritte (Refs #2318) --------------

# Zielvorbereitung: erst Schreibruhe herstellen, dann die Ziel-Datenbank neu
# anlegen. Beides stand frueher als Kopiervorlage in der DR-Doku VOR dem Aufruf
# dieses Skripts und lief damit vor jeder Bestaetigung.
echo "Bereite Ziel-Datenbank '${POSTGRES_DB}' vor (Verbindungen beenden, DB neu anlegen)..."
docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -v ON_ERROR_STOP=1 -U "$SU_USER" -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();" \
    < /dev/null > /dev/null
docker compose -f "$COMPOSE_FILE" exec -T db sh -c '
    set -eu
    su_user="$1"; target="$2"; owner="$3"
    dropdb -U "$su_user" --if-exists "$target"
    if [ -n "$owner" ]; then
        createdb -U "$su_user" -O "$owner" "$target"
    else
        createdb -U "$su_user" "$target"
    fi
' sh "$SU_USER" "$POSTGRES_DB" "${POSTGRES_ADMIN_USER:-}" < /dev/null

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
    validate_or_install_hwm install
    echo "Audit-HWM wiederhergestellt: ${HWM_FILE##*/}"
fi

# Medien-Restore (Refs #720)
if [[ -n "$MEDIA_FILE" ]]; then
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
        -F '|' -c "SELECT facility_id, storage_filename FROM core_attachment ORDER BY facility_id, storage_filename;")
    while IFS='|' read -r facility_id storage_filename; do
        [[ -z "$facility_id" && -z "$storage_filename" ]] && continue
        attachment_path="${facility_id}/${storage_filename}"
        case "$attachment_path" in
            /* | ../* | */../* | */..)
                docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_STAGE"
                echo "FEHLER: Unsicherer Anhangpfad in core_attachment: ${attachment_path}" >&2
                exit 1
                ;;
        esac
        if [[ -z "$facility_id" || -z "$storage_filename" || "$facility_id" == */* || "$storage_filename" == */* ]]; then
            docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_STAGE"
            echo "FEHLER: Ungueltiger Anhangpfad in core_attachment: ${attachment_path}" >&2
            exit 1
        fi
        if ! docker compose -f "$COMPOSE_FILE" exec -T web \
            test -f "${MEDIA_STAGE}/$attachment_path"; then
            docker compose -f "$COMPOSE_FILE" exec -T web rm -rf "$MEDIA_STAGE"
            echo "FEHLER: Datei fuer DB-Anhang fehlt im Medien-Backup: ${attachment_path}" >&2
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

echo "RESTORE-SATZ WIEDERHERGESTELLT: ${BACKUP_TIMESTAMP}"
