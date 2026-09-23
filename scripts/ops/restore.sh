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
# Satz-/Manifestpruefung, geteilt mit ``backup.sh --verify`` (Refs #2290, #2292).
# shellcheck source=scripts/ops/lib/backup-set.sh
source "${SCRIPT_DIR}/lib/backup-set.sh"

# Bestaetigung (Refs #2318): Ein per Pipe zugefuehrtes "ja" wurde frueher von
# einem vorgelagerten ``docker compose exec -T``-Aufruf verbraucht; die Kette
# brach danach ab — nachdem die Ziel-Datenbank bereits geleert war. Deshalb
# gibt es jetzt einen expliziten Schalter (``--yes`` bzw. ``RESTORE_CONFIRM=ja``),
# alle Vorpruefungen lesen nichts mehr von stdin, und JEDER zerstoerende Schritt
# liegt hinter der Bestaetigung.
ASSUME_YES=0
ALLOW_MISSING_MANIFEST=0
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes) ASSUME_YES=1; shift ;;
        --allow-missing-manifest) ALLOW_MISSING_MANIFEST=1; shift ;;
        --) shift; POSITIONAL+=("$@"); break ;;
        --*) echo "Fehler: unbekannte Option: $1 (erlaubt: --yes, --allow-missing-manifest)" >&2; exit 1 ;;
        *) POSITIONAL+=("$1"); shift ;;
    esac
done
if [[ ${#POSITIONAL[@]} -lt 1 || ${#POSITIONAL[@]} -gt 2 ]]; then
    echo "Verwendung: $0 [--yes] [--allow-missing-manifest] <db-backup.sql.gz.enc> [media-backup.tar.gz.enc]"
    echo "  --yes                     Bestaetigung ohne Rueckfrage (alternativ RESTORE_CONFIRM=ja)"
    echo "  --allow-missing-manifest   Notfall: Satz ohne Manifest (vor 0.99.2) ohne Satzbindung einspielen"
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
if [[ ! "$POSTGRES_DB" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "FEHLER: POSTGRES_DB darf nur Buchstaben, Ziffern, Unterstriche und Bindestriche enthalten." >&2
    exit 1
fi
if [[ -n "${POSTGRES_ADMIN_USER:-}" && ! "$POSTGRES_ADMIN_USER" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "FEHLER: POSTGRES_ADMIN_USER darf nur Buchstaben, Ziffern, Unterstriche und Bindestriche enthalten." >&2
    exit 1
fi
MEDIA_ROOT_CONTAINER="${MEDIA_ROOT_CONTAINER:-/data/media}"
backup_require_real_key

# Der Backup-Satz wird als Ganzes authentifiziert: signiertes Manifest,
# Satzkennung, Dump-Hash und — sofern im Manifest gebunden — Medien-Archiv samt
# Sidecar und Hash. Dieselbe Pruefung fuehrt ``backup.sh --verify`` aus, damit
# Verify und Restore nicht auseinanderlaufen (Refs #2290, #2292).
MANIFEST_FILE="$(backup_set_manifest_path "$BACKUP_FILE")"
LEGACY_SET=0
LEGACY_HWM_HINT=""
if [[ ! -f "$MANIFEST_FILE" ]]; then
    # Saetze aus backup.sh tragen seit 0.99.1 ein signiertes Manifest. Aeltere
    # Weekly-/Monthly-Saetze (Aufbewahrung 28/90 Tage) haben keins — ohne einen
    # ausdruecklichen Notfall-Schalter waeren sie mit diesem Werkzeug gar nicht
    # mehr wiederherstellbar.
    if [[ "$ALLOW_MISSING_MANIFEST" -ne 1 ]]; then
        echo "FEHLER: Pflicht-Manifest des Backup-Satzes fehlt: ${MANIFEST_FILE}" >&2
        echo "  Ohne Manifest gibt es keine Satzbindung (Zeitstempel, Dump-/Medien-Hashes) und keinen" >&2
        echo "  Zeilenzahl-Abgleich. Nur fuer Altsaetze von vor 0.99.2 und nur bewusst:" >&2
        echo "    $0 --allow-missing-manifest ${BACKUP_FILE} [medien-archiv]" >&2
        exit 1
    fi
    LEGACY_SET=1
    if [[ -n "$MEDIA_FILE" ]]; then
        # Ohne Manifest bleibt nur die Namenskonvention als Satzbindung: das
        # Archiv muss denselben Zeitstempel tragen wie der Dump.
        LEGACY_MEDIA_EXPECTED="$(backup_set_media_path "$BACKUP_FILE")"
        if [[ "${MEDIA_FILE##*/}" != "${LEGACY_MEDIA_EXPECTED##*/}" ]]; then
            echo "FEHLER: Medien-Archiv traegt einen anderen Zeitstempel als der Dump: ${MEDIA_FILE##*/}" >&2
            echo "  Erwartet: ${LEGACY_MEDIA_EXPECTED##*/} — im Altsatz-Modus ist der Dateiname die einzige Satzbindung." >&2
            exit 1
        fi
    fi
else
    echo "Prüfe Integrität und Bindung des Backup-Satzes (Manifest, HMAC)..."
    backup_set_load "$MANIFEST_FILE"
    backup_set_verify_artifacts "$BACKUP_FILE" "$MEDIA_FILE"
fi
MANIFEST_MEDIA_SHA256="$BACKUP_SET_MEDIA_SHA256"

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
if [[ -z "${DJANGO_AUDIT_HWM_PATH:-}" && -n "$BACKUP_SET_HWM_SHA256" ]]; then
    echo "WARNUNG: Der Satz enthaelt einen Audit-Anker, dieser Host hat aber keinen Ankerpfad konfiguriert." >&2
    echo "  Die Vorpruefung lief ohne Audit-Anker; der gebundene Anker wurde nicht geprueft." >&2
    echo "  DJANGO_AUDIT_HWM_PATH ist nicht gesetzt — der Anker wird NICHT eingespielt; die Audit-Kette gilt" >&2
    echo "  nach dem Restore als unverankert (docs/disaster-recovery.md § 6 Schritt 4b)." >&2
fi
if [[ -n "${DJANGO_AUDIT_HWM_PATH:-}" ]]; then
    if [[ "${BACKUP_FILE##*/}" != "anlaufstelle_${BACKUP_TIMESTAMP}.sql.gz.enc" ]]; then
        echo "FEHLER: DB-Backup-Dateiname enthaelt keinen auswertbaren Zeitstempel fuer den Audit-HWM." >&2
        exit 1
    fi
    HWM_FILE="${BACKUP_FILE%/*}/audit_hwm-${BACKUP_TIMESTAMP}.json"
    if [[ "$BACKUP_FILE" != */* ]]; then
        HWM_FILE="./audit_hwm-${BACKUP_TIMESTAMP}.json"
    fi
    if [[ "$LEGACY_SET" -eq 1 ]]; then
        # Ohne Manifest gibt es keine kryptografische Bindung des Ankers an den
        # Satz — nur den Zeitstempel im Dateinamen. Ein fehlender Anker ist hier
        # deshalb kein Abbruchgrund (der Satz kennt gar keinen), wird aber laut
        # gemeldet und steht am Ende noch einmal in der Abschlussausgabe.
        if [[ ! -s "$HWM_FILE" ]]; then
            echo "WARNUNG: Altsatz ohne Manifest: kein zum Dump gehoerender Audit-Anker (${HWM_FILE##*/}) gefunden." >&2
            echo "  Der Anker wird NICHT eingespielt; die Audit-Kette bleibt unverankert." >&2
            echo "  Schritt 4b der Disaster-Recovery-Anleitung manuell nachziehen." >&2
            HWM_FILE=""
            LEGACY_HWM_HINT="Kein zugehoeriger Audit-Anker eingespielt — Audit-Kette unverankert, Schritt 4b manuell nachziehen."
        else
            echo "WARNUNG: Altsatz ohne Manifest: der Audit-Anker ${HWM_FILE##*/} wird nur ueber den" >&2
            echo "  Zeitstempel im Dateinamen zugeordnet — es gibt keine Hash-Bindung an diesen Satz." >&2
            LEGACY_HWM_HINT="Audit-Anker nur ueber den Dateinamen zugeordnet (keine Hash-Bindung an den Satz) — Schritt 4b pruefen."
        fi
    else
        if [[ ! -f "$HWM_FILE" ]]; then
            echo "FEHLER: Zum DB-Backup gehoerender Audit-HWM fehlt: ${HWM_FILE}" >&2
            exit 1
        fi
        if [[ ! -s "$HWM_FILE" ]]; then
            echo "FEHLER: Zum DB-Backup gehoerender Audit-HWM ist leer: ${HWM_FILE}" >&2
            exit 1
        fi
        # Der Anker ist Teil des signierten Satzes (Refs #2290).
        backup_set_verify_artifacts "$BACKUP_FILE" "$MEDIA_FILE" "$HWM_FILE"
    fi
fi

# Der Plain-SQL-Dump enthaelt OWNER-TO-/FORCE-RLS-Statements und COPY in
# FORCE-RLS-Tabellen — das scheitert unter der App-Rolle (NOBYPASSRLS, kein
# DDL-Owner-Recht). Bootstrap-Superuser via Local-Socket-Trust im
# db-Container — wie scripts/ops/restore-drill.sh (Review N4).
SU_USER="${POSTGRES_SUPERUSER:-postgres}"

# Wiederherstellungsweg (Refs #2290): Der Plain-SQL-Dump enthaelt keine
# Bereinigungsanweisungen. In eine bereits migrierte Zielinstallation
# eingespielt, kollidieren seine CREATE-Anweisungen mit den vorhandenen
# Objekten; der Import bricht ab. Deshalb wird IMMER zuerst eine frische
# Wiederherstellungs-Datenbank gefuellt und gegen das Manifest verifiziert —
# erst danach wird unter Schreibruhe umgeschaltet (Rename). Der bisherige
# Bestand bleibt als eigene Datenbank erhalten und wird nicht geloescht.
SWITCH_STAMP="$(date +%Y%m%d%H%M%S)"
RESTORE_DB="${POSTGRES_DB}_restore_$$"
OLD_DB="${POSTGRES_DB}_vor_restore_${SWITCH_STAMP}"
for candidate in "$RESTORE_DB" "$OLD_DB"; do
    if [[ ${#candidate} -gt 63 ]]; then
        echo "FEHLER: Abgeleiteter Datenbankname ist laenger als 63 Zeichen: ${candidate}" >&2
        echo "  POSTGRES_DB kuerzen — PostgreSQL schneidet laengere Namen ab." >&2
        exit 1
    fi
done

# Alle Steuerabfragen laufen ohne stdin, damit sie keine durchgereichte
# Bestaetigung aufbrauchen (Refs #2318).
db_psql() {
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -v ON_ERROR_STOP=1 -U "$SU_USER" "$@" < /dev/null
}

# Medien-Kommandos laufen vor der Schreibruhe im laufenden web-Container und
# danach in einem Einweg-``maintenance``-Container (web ist dann gestoppt).
MEDIA_EXEC_CTX="web"
media_exec() {
    if [[ "$MEDIA_EXEC_CTX" == "web" ]]; then
        docker compose -f "$COMPOSE_FILE" exec -T web "$@" < /dev/null
    else
        docker compose -f "$COMPOSE_FILE" run --rm --entrypoint "$1" -T maintenance "${@:2}" < /dev/null
    fi
}

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

if [[ "$LEGACY_SET" -eq 1 ]]; then
    echo "ACHTUNG: Altsatz-Modus — dieser Satz hat KEIN signiertes Manifest."
    echo "  Es gibt keine Satzbindung (Zeitstempel/Hashes ueber Dump, Medien und Anker) und keinen"
    echo "  Zeilenzahl-Abgleich gegen einen Referenzstand. Geprueft werden nur die HMAC-Sidecars."
    echo "  Nach dem Restore die Vollstaendigkeit von Hand pruefen (docs/disaster-recovery.md § 7)."
fi
echo "WARNUNG: Die Datenbank '${POSTGRES_DB}' wird auf den Backup-Stand umgeschaltet!"
echo "Wiederherstellungs-DB: ${RESTORE_DB} (wird gefuellt und verifiziert)"
echo "Bisheriger Stand bleibt als: ${OLD_DB}"
echo "Schreibruhe: web- und cron-Container werden vor der Umschaltung gestoppt."
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

RESTORE_DB_CREATED=0
SWITCHED=0
SWITCH_PENDING=0
OLD_DB_KEPT=0
MEDIA_STAGED=0
cleanup_restore_db() {
    # Zuerst das Staging: es muss auch dann verschwinden, wenn die Umschaltung
    # schon gelaufen ist und erst die Medienaktivierung scheitert (oder ein
    # Signal danach kommt).
    if [[ "$MEDIA_STAGED" -eq 1 ]]; then
        media_exec rm -rf "$MEDIA_STAGE" > /dev/null 2>&1 || true
    fi
    if [[ "$SWITCHED" -eq 1 ]]; then
        return
    fi
    if [[ "$SWITCH_PENDING" -eq 1 ]]; then
        # Abbruch (auch per Signal) zwischen den beiden Umbenennungen: der
        # verifizierte Stand ist das Wertvollste im Spiel und wird NICHT
        # verworfen. Stattdessen den bisherigen Stand zurueckholen.
        echo "ABBRUCH waehrend der Umschaltung — der verifizierte Stand bleibt als '${RESTORE_DB}' erhalten." >&2
        if [[ "$OLD_DB_KEPT" -eq 1 ]]; then
            if db_psql -d postgres -c "ALTER DATABASE \"${OLD_DB}\" RENAME TO \"${POSTGRES_DB}\";" > /dev/null 2>&1; then
                echo "Bisheriger Stand wurde unter '${POSTGRES_DB}' zurueckgeholt." >&2
            else
                echo "Rueckholen nicht moeglich (evtl. war die Sicherung noch nicht aktiv) — von Hand pruefen:" >&2
                echo "  ALTER DATABASE \"${OLD_DB}\" RENAME TO \"${POSTGRES_DB}\";" >&2
            fi
        fi
        echo "Alternativ den verifizierten Stand aktivieren:" >&2
        echo "  ALTER DATABASE \"${RESTORE_DB}\" RENAME TO \"${POSTGRES_DB}\";" >&2
        return
    fi
    if [[ "$RESTORE_DB_CREATED" -eq 1 ]]; then
        echo "Raeume Wiederherstellungs-Datenbank '${RESTORE_DB}' auf..." >&2
        db_psql -d postgres -c "DROP DATABASE IF EXISTS \"${RESTORE_DB}\";" > /dev/null 2>&1 || true
    fi
}
trap cleanup_restore_db EXIT

echo "Lege Wiederherstellungs-Datenbank '${RESTORE_DB}' an..."
db_psql -d postgres -c "DROP DATABASE IF EXISTS \"${RESTORE_DB}\";" > /dev/null
RESTORE_DB_CREATED=1
if [[ -n "${POSTGRES_ADMIN_USER:-}" ]]; then
    db_psql -d postgres -c "CREATE DATABASE \"${RESTORE_DB}\" OWNER \"${POSTGRES_ADMIN_USER}\";" > /dev/null
else
    db_psql -d postgres -c "CREATE DATABASE \"${RESTORE_DB}\";" > /dev/null
fi

# DB-Import in die Wiederherstellungs-DB — beide HMAC-Sidecars wurden in der
# Vorpruefung verifiziert. Direkt vor der Nutzung erneut pruefen, damit die
# interaktive Wartezeit das Pruef-Nutzungs-Fenster nicht vergroessert (Refs #2195).
echo "Stelle DB-Backup in '${RESTORE_DB}' wieder her..."
backup_verify_hmac "$BACKUP_FILE"
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$BACKUP_FILE" \
    | gunzip \
    | docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -v ON_ERROR_STOP=1 --single-transaction -U "$SU_USER" "$RESTORE_DB"
echo "DB-Import abgeschlossen."

# Verifikation gegen den signierten Referenzstand. Erfolg wird erst nach diesem
# Abgleich gemeldet; ein unvollstaendiger Import fuehrt nie zur Umschaltung.
if [[ "$LEGACY_SET" -eq 1 ]]; then
    echo "Altsatz-Modus: kein Manifest — der eingespielte Stand wird gegen KEINEN Referenzstand geprueft."
    VERIFY_FAILED=0
else
echo "Verifiziere den eingespielten Stand gegen das Manifest..."
VERIFY_FAILED=0
for tbl in "${BACKUP_SET_TABLES[@]}"; do
    RESTORED_COUNT=$(db_psql -d "$RESTORE_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null \
        | tr -d '[:space:]' || true)
    EXPECTED_COUNT="${BACKUP_SET_COUNTS[$tbl]}"
    if [[ ! "$RESTORED_COUNT" =~ ^[0-9]+$ ]]; then
        echo "  FEHLER ${tbl}: im Wiederherstellungsstand nicht abfragbar."
        VERIFY_FAILED=1
    elif [[ "$RESTORED_COUNT" -ne "$EXPECTED_COUNT" ]]; then
        echo "  FEHLER ${tbl}: Restore=${RESTORED_COUNT}, Manifest=${EXPECTED_COUNT}."
        VERIFY_FAILED=1
    else
        echo "  OK ${tbl}: ${RESTORED_COUNT} Zeilen (Manifest ${EXPECTED_COUNT})."
    fi
done
if [[ "$VERIFY_FAILED" -ne 0 ]]; then
    echo "FEHLER: Wiederhergestellter Stand passt nicht zum Manifest — keine Umschaltung." >&2
    exit 1
fi
echo "Verifikation bestanden: Wiederherstellungsstand entspricht dem Manifest."
fi

# Medien-Staging (Refs #720): neben dem aktiven App-Pfad entpacken und jede
# Anhangzeile des WIEDERHERGESTELLTEN Stands gegen das Staging pruefen. Der
# bisherige Bestand bleibt bis zur Aktivierung unangetastet.
if [[ -n "$MEDIA_FILE" ]]; then
    MEDIA_STAGE="${MEDIA_ROOT_CONTAINER}.restore.$$"
    MEDIA_OLD="${MEDIA_ROOT_CONTAINER}.old.$$"

    echo "Prüfe Integrität des Medien-Backups erneut unmittelbar vor der Nutzung (HMAC)..."
    backup_verify_hmac "$MEDIA_FILE"

    media_exec rm -rf "$MEDIA_STAGE" "$MEDIA_OLD"
    media_exec mkdir -p "$MEDIA_STAGE"
    MEDIA_STAGED=1
    echo "Entpacke Medien-Backup nach ${MEDIA_STAGE} ..."
    if ! openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$MEDIA_FILE" \
        | gunzip \
        | docker compose -f "$COMPOSE_FILE" exec -T web \
            tar -C "$MEDIA_STAGE" --strip-components=1 -xf -; then
        media_exec rm -rf "$MEDIA_STAGE"
        echo "FEHLER: Medien-Backup konnte nicht vollstaendig entpackt werden." >&2
        exit 1
    fi

    # Jede restaurierte Anhangzeile muss am konfigurierten App-Pfad eine Datei
    # besitzen. Erst nach diesem fail-closed Abgleich wird der Bestand getauscht.
    ATTACHMENT_PATHS=$(db_psql -d "$RESTORE_DB" -t -A \
        -F '|' -c "SELECT facility_id, storage_filename FROM core_attachment ORDER BY facility_id, storage_filename;")
    while IFS='|' read -r facility_id storage_filename; do
        [[ -z "$facility_id" && -z "$storage_filename" ]] && continue
        attachment_path="${facility_id}/${storage_filename}"
        case "$attachment_path" in
            /* | ../* | */../* | */..)
                media_exec rm -rf "$MEDIA_STAGE"
                echo "FEHLER: Unsicherer Anhangpfad in core_attachment: ${attachment_path}" >&2
                exit 1
                ;;
        esac
        if [[ -z "$facility_id" || -z "$storage_filename" || "$facility_id" == */* || "$storage_filename" == */* ]]; then
            media_exec rm -rf "$MEDIA_STAGE"
            echo "FEHLER: Ungueltiger Anhangpfad in core_attachment: ${attachment_path}" >&2
            exit 1
        fi
        if ! media_exec test -f "${MEDIA_STAGE}/$attachment_path"; then
            media_exec rm -rf "$MEDIA_STAGE"
            echo "FEHLER: Datei fuer DB-Anhang fehlt im Medien-Backup: ${attachment_path}" >&2
            exit 1
        fi
    done <<< "$ATTACHMENT_PATHS"
fi

# --- Schreibruhe + Umschaltung ---------------------------------------------
# Erst jetzt wird der bisherige Bestand angefasst. Ohne gestoppte Schreiber
# schlaegt das Umbenennen an offenen Verbindungen fehl — und ein Schreibzugriff
# zwischen Pruefung und Umschaltung waere nach dem Restore verloren.
echo "Stoppe schreibende Dienste (Schreibruhe fuer die Umschaltung)..."
docker compose -f "$COMPOSE_FILE" stop web cron < /dev/null
MEDIA_EXEC_CTX="maintenance"
db_psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('${POSTGRES_DB}', '${RESTORE_DB}') AND pid <> pg_backend_pid();" > /dev/null

TARGET_EXISTS=$(db_psql -d postgres -t -A -c "SELECT 1 FROM pg_database WHERE datname = '${POSTGRES_DB}';" 2>/dev/null \
    | tr -d '[:space:]' || true)
SWITCH_PENDING=1
if [[ "$TARGET_EXISTS" == "1" ]]; then
    echo "Sichere bisherigen Datenbankstand als '${OLD_DB}' ..."
    # Flag VOR der Umbenennung: ein Abbruch unmittelbar danach darf den
    # Rueckholpfad nicht verlieren (der Trap prueft die Sicherung ohnehin).
    OLD_DB_KEPT=1
    db_psql -d postgres -c "ALTER DATABASE \"${POSTGRES_DB}\" RENAME TO \"${OLD_DB}\";" > /dev/null
fi
echo "Schalte auf den verifizierten Wiederherstellungsstand um..."
if ! db_psql -d postgres -c "ALTER DATABASE \"${RESTORE_DB}\" RENAME TO \"${POSTGRES_DB}\";" > /dev/null; then
    if [[ "$OLD_DB_KEPT" -eq 1 ]]; then
        db_psql -d postgres -c "ALTER DATABASE \"${OLD_DB}\" RENAME TO \"${POSTGRES_DB}\";" > /dev/null || true
    fi
    echo "FEHLER: Umschalten auf den Wiederherstellungsstand fehlgeschlagen — Ziel unveraendert." >&2
    exit 1
fi
SWITCHED=1
SWITCH_PENDING=0
echo "DB-Wiederherstellung abgeschlossen."

if [[ -n "$HWM_FILE" ]]; then
    echo "Spiele Audit-HWM ${HWM_FILE##*/} nach ${DJANGO_AUDIT_HWM_PATH} ..."
    validate_or_install_hwm install
    echo "Audit-HWM wiederhergestellt: ${HWM_FILE##*/}"
fi

# Medien-Aktivierung: ein einziger Tausch im Medien-Volume, mit Ruecknahme bei
# Fehlschlag. Laeuft im maintenance-Container, weil web fuer die Schreibruhe
# gestoppt ist.
if [[ -n "$MEDIA_FILE" ]]; then
    if ! docker compose -f "$COMPOSE_FILE" run --rm --entrypoint sh -T maintenance -c '
        set -eu
        stage="$1"; live="$2"; old="$3"
        if [ -e "$live" ]; then
            mv "$live" "$old"
        fi
        if ! mv "$stage" "$live"; then
            if [ -e "$old" ]; then
                mv "$old" "$live"
            fi
            exit 1
        fi
        rm -rf "$old"
    ' sh "$MEDIA_STAGE" "$MEDIA_ROOT_CONTAINER" "$MEDIA_OLD" < /dev/null; then
        echo "FEHLER: Validierter Medienbestand konnte nicht aktiviert werden." >&2
        exit 1
    fi
    MEDIA_STAGED=0
    echo "Medien-Wiederherstellung abgeschlossen."
fi

if [[ "$OLD_DB_KEPT" -eq 1 ]]; then
    echo "Bisheriger Datenbankstand gesichert als: ${OLD_DB}"
    echo "  Nach erfolgreicher Abnahme loeschbar: DROP DATABASE \"${OLD_DB}\";"
fi
echo "Hinweis: web- und cron-Container sind gestoppt (Schreibruhe) — Migrationen fahren, dann Stack starten."
echo "RESTORE-SATZ WIEDERHERGESTELLT: ${BACKUP_TIMESTAMP}"
if [[ "$LEGACY_SET" -eq 1 ]]; then
    echo "HINWEIS: Altsatz-Modus — ohne Manifest ist dieser Lauf KEIN vollstaendiger Satznachweis (keine Satzbindung, kein Zeilenabgleich)."
    if [[ -n "$LEGACY_HWM_HINT" ]]; then
        echo "HINWEIS: ${LEGACY_HWM_HINT}"
    fi
fi
