#!/usr/bin/env bash
set -uo pipefail

# Anlaufstelle Backup-Restore-Drill (Refs #739, #720, #981)
#
# Verifies that the latest backup (prod or dev) can be restored completely into
# a throwaway DB and that the lines of defence (RLS, AuditLog immutability
# trigger) survive. On success the run is recorded in the AuditLog via
# ``manage.py mark_restore_verified`` -> the compliance check "Restore-Test"
# turns green.
#
# Two backup paths -> two formats; the drill covers BOTH (OPS-05, Refs #1336).
# Only the prod path is shipped; the dev path belongs to the project's own dev
# operation. Choose with the flag ``--dev``/``--prod`` or ``$DRILL_FORMAT``;
# for prod additionally with ``--timestamp``/``$BACKUP_TIMESTAMP``. Without a
# choice the format is auto-detected from the existing backup files and the
# newest set is used with a clear banner.
#
#   dev  : dev-ops/deploy/backup.sh (dev.anlaufstelle.app)
#          - Quelle:  $DEV_BACKUP_DIR/dump-*.pgc.enc  (Default /var/backups/anl)
#          - Format:  pg_dump --format=custom -> pg_restore (NICHT psql)
#          - Stack:   the dev compose file with its env file
#   prod : scripts/ops/backup.sh (der an Self-Hoster ausgelieferte Pfad)
#          - Quelle:  $BACKUP_HOST_DIR/daily/anlaufstelle_*.sql.gz.enc
#          - Format:  plain SQL + gzip + A4.3-HMAC-Sidecar -> psql
#          - Stack:   docker-compose.prod.yml
#
# Beide nutzen openssl AES-256-CBC -pbkdf2 und den Postgres-Superuser (Default
# ``postgres`` ueber Local-Socket-Trust im db-Container): noetig fuer CREATE
# DATABASE (beide App-Rollen sind NOCREATEDB) und bypassed zugleich RLS/FORCE-RLS
# beim Restore.
#
# The dump files belong to the account the backup ran under (root in the
# recommended setup; prod: mode 0640, dev: 0600) — so run as root:
#   sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh            # auto
#   sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod     # explizit
#
# Empfohlen quartalsweise (per Timer/Cron) + Alert bei Exit-Code != 0.

# Verzeichnis dieses Skripts VOR dem cd bestimmen, damit das gemeinsame
# Krypto-/Guard-Modul relativ zum Skript gefunden wird (Refs #1441).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# N6-Guard (Refs #1441): backup_require_real_key weist einen change-me*-
# BACKUP_ENCRYPTION_KEY fail-closed ab — geteilt mit backup.sh / restore.sh.
# shellcheck source=scripts/ops/_backup_common.sh
source "${SCRIPT_DIR}/_backup_common.sh"
# Gemeinsame Satz-/Manifestpruefung, geteilt mit backup.sh --verify und
# restore.sh (Refs #2290, #2292). Der Drill hatte hier frueher eine eigene
# Kopie dieser Logik — und akzeptierte damit Saetze, die der Prueflauf und der
# echte Restore ablehnen (z. B. ein im Manifest gebundenes, aber fehlendes
# Anker-Artefakt oder ein Medien-Archiv, das der Satz gar nicht bindet).
# shellcheck source=scripts/ops/lib/backup-set.sh
source "${SCRIPT_DIR}/lib/backup-set.sh"
# Reads dotenv files as data, never as shell code.
# shellcheck source=scripts/ops/lib/env-file.sh
source "${SCRIPT_DIR}/lib/env-file.sh"

# --- Backup-Format / Modus (OPS-05, Refs #1336) ---------------------------
DRILL_FORMAT="${DRILL_FORMAT:-}"
BACKUP_TIMESTAMP="${BACKUP_TIMESTAMP:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --prod) DRILL_FORMAT="prod"; shift ;;
        --dev) DRILL_FORMAT="dev"; shift ;;
        --timestamp)
            [[ $# -ge 2 ]] || { echo "FAIL - --timestamp braucht einen Wert" >&2; exit 2; }
            BACKUP_TIMESTAMP="$2"
            shift 2
            ;;
        --*) echo "FAIL - unbekannte Option: $1 (erlaubt: --dev, --prod, --timestamp <ts>)" >&2; exit 2 ;;
        *) echo "FAIL - unerwartetes Argument: $1" >&2; exit 2 ;;
    esac
done
if [[ -n "$BACKUP_TIMESTAMP" && "$DRILL_FORMAT" != "prod" ]]; then
    echo "FAIL - --timestamp/BACKUP_TIMESTAMP ist nur zusammen mit dem Prod-Format erlaubt" >&2
    exit 2
fi
if [[ -n "$BACKUP_TIMESTAMP" && ! "$BACKUP_TIMESTAMP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$ ]]; then
    echo "FAIL - ungueltiger Backup-Zeitstempel: ${BACKUP_TIMESTAMP}" >&2
    exit 2
fi

COMPOSE_DIR="${COMPOSE_DIR:-/opt/anlaufstelle}"
cd "$COMPOSE_DIR" || {
    echo "FAIL - COMPOSE_DIR $COMPOSE_DIR nicht erreichbar"
    exit 1
}

# Kandidaten-Verzeichnisse je Format (fuer Auto-Erkennung + Default). Der
# Prod-Pfad kommt aus der Host-Sicht BACKUP_HOST_DIR; BACKUP_DIR aus .env ist
# dagegen das Mount-Ziel im Container und wird bewusst ignoriert.
DEV_BACKUP_DIR="${DEV_BACKUP_DIR:-/var/backups/anl}"
resolve_prod_backup_dir() (
    local env_file="$1" host_dir
    if [[ -r "$env_file" ]]; then
        load_env_file "$env_file"
    fi
    host_dir="${BACKUP_HOST_DIR:-$COMPOSE_DIR/backups}"
    if [[ "$host_dir" != /* ]]; then
        host_dir="${COMPOSE_DIR}/${host_dir#./}"
    fi
    printf '%s/daily\n' "$host_dir"
)
PROD_ENV_FILE="${ENV_FILE:-$COMPOSE_DIR/.env}"
_prod_backup_dir="$(resolve_prod_backup_dir "$PROD_ENV_FILE")"
if [[ -z "$DRILL_FORMAT" ]]; then
    # Kein "| head -n1" unter pipefail (SIGPIPE-Gefahr): Liste ganz lesen, erstes Element nehmen.
    mapfile -t _dev_hit_CANDIDATES < <(find "$DEV_BACKUP_DIR" -maxdepth 1 -name 'dump-*.pgc.enc' -type f 2>/dev/null)
    _dev_hit="${_dev_hit_CANDIDATES[0]:-}"
    # Kein "| head -n1" unter pipefail (SIGPIPE-Gefahr): Liste ganz lesen, erstes Element nehmen.
    mapfile -t _prod_hit_CANDIDATES < <(find "$_prod_backup_dir" -maxdepth 1 -name 'anlaufstelle_*.sql.gz.enc' -type f 2>/dev/null)
    _prod_hit="${_prod_hit_CANDIDATES[0]:-}"
    if [[ -n "$_dev_hit" && -n "$_prod_hit" ]]; then
        echo "FAIL - beide Backup-Formate gefunden (dev: $DEV_BACKUP_DIR, prod: $_prod_backup_dir)." >&2
        echo "       Format explizit waehlen: restore-drill.sh --dev | --prod" >&2
        exit 2
    elif [[ -n "$_prod_hit" ]]; then
        DRILL_FORMAT="prod"
    else
        DRILL_FORMAT="dev"
    fi
fi

# Format-spezifische Konfiguration: Env-Datei, Backup-Glob und Compose-Stack.
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    ENV_FILE="$PROD_ENV_FILE"
    BACKUP_GLOB='anlaufstelle_*.sql.gz.enc'
    # Same file list as restore.sh and the runbook: as soon as one -f is given,
    # Compose stops loading compose.override.yml on its own, so the optional
    # files are added explicitly (the working directory is COMPOSE_DIR).
    COMPOSE=(docker compose -f docker-compose.prod.yml)
    for compose_extra_file in compose.legal.yml compose.override.yml; do
        if [[ -f "$compose_extra_file" ]]; then
            COMPOSE+=(-f "$compose_extra_file")
        fi
    done
else
    ENV_FILE="${ENV_FILE:-$COMPOSE_DIR/.env.dev}"
    BACKUP_GLOB='dump-*.pgc.enc'
    COMPOSE=(docker compose -f docker-compose.dev.yml --env-file .env.dev)
fi

# Env laden (nur Variablen) — liefert POSTGRES_*, BACKUP_ENCRYPTION_KEY.
if [[ -r "$ENV_FILE" ]]; then
    load_env_file "$ENV_FILE"
fi

if [[ "$DRILL_FORMAT" == "prod" ]]; then
    DJANGO_AUDIT_HWM_PATH="${DJANGO_AUDIT_HWM_PATH:-/data/audit_hwm.json}"
    BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-$COMPOSE_DIR/backups}"
    if [[ "$BACKUP_HOST_DIR" != /* ]]; then
        BACKUP_HOST_DIR="${COMPOSE_DIR}/${BACKUP_HOST_DIR#./}"
    fi
    BACKUP_DIR="${BACKUP_HOST_DIR}/daily"
else
    # Welt A nutzt DEV_BACKUP_DIR als Host-Pfad; auch hier darf das
    # Container-Ziel BACKUP_DIR aus einer Env-Datei nicht gewinnen.
    BACKUP_DIR="$DEV_BACKUP_DIR"
fi
echo "Restore-Drill — Format: ${DRILL_FORMAT}, Backups: ${BACKUP_DIR}"

: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY nicht gesetzt (aus $ENV_FILE)}"
# Ein mit dem oeffentlich bekannten Platzhalter "verschluesseltes" Backup ist
# faktisch unverschluesselt — der Drill wuerde es sonst als restore-faehig
# durchwinken. Fail-closed (Refs #1441).
backup_require_real_key

# Der Vollstaendigkeits-Guard (Schritt 3b) gleicht im Prod-Format die
# Wegwerf-DB mit dem authentifizierten Manifest ab; nur das Dev-Format nutzt
# dafuer weiterhin die Live-DB (wie backup.sh --verify). POSTGRES_DB benennt
# zudem die Live-Datenbank und muss deshalb in beiden Formaten bekannt sein.
: "${POSTGRES_DB:?POSTGRES_DB nicht gesetzt (aus $ENV_FILE) — der Vollstaendigkeits-Abgleich braucht die Live-DB}"

# Der Drill braucht CREATE DATABASE — das kann auf dem 3-Rollen-Modell nur der
# Postgres-Superuser (``anlaufstelle_admin`` ist BYPASSRLS, aber NICHT CREATEDB;
# ``anlaufstelle`` ist der RLS-gebundene App-User). Der Superuser bypassed
# zugleich RLS, sodass der Restore (pg_restore bzw. psql) die geschuetzten
# Tabellen fuellen kann. Verbindung ueber den lokalen Socket im db-Container
# (pg_hba: local trust) — daher kein Passwort noetig.
SU_USER="${POSTGRES_SUPERUSER:-postgres}"

DRILL_FAILED=0
DRILL_UNDETERMINED=0
HWM_DRILL_CONTAINER_PATH="/tmp/audit_hwm-drill-$$.json"
MEDIA_TMP_DIR=""
MEDIA_LIST_FILE=""
DRILL_SKIPPED=()
ok() { echo "OK   - $*"; }
fail() {
    echo "FAIL - $*"
    DRILL_FAILED=1
}
# Ein nicht ausgefuehrter Schritt ist kein bestandener Schritt: er erscheint nie
# als OK und wird im Ergebnisblock namentlich als uebersprungen ausgewiesen.
skip() {
    echo "SKIP - $*"
    DRILL_SKIPPED+=("$*")
}

dc() { "${COMPOSE[@]}" "$@"; }
psql_su() { dc exec -T db psql -U "$SU_USER" "$@"; }

# Schritt 0: Backup finden. Eine explizite Prod-Satzwahl hat Vorrang vor dem
# lexikografisch neuesten Satz; ohne Angabe bleibt das bisherige Verhalten.
if [[ "$DRILL_FORMAT" == "prod" && -n "$BACKUP_TIMESTAMP" ]]; then
    LATEST_DB="${BACKUP_DIR}/anlaufstelle_${BACKUP_TIMESTAMP}.sql.gz.enc"
    echo "ACHTUNG - explizit gewaehlter Restore-Satz: ${BACKUP_TIMESTAMP}"
else
    # Kein "| head -n1" unter pipefail (SIGPIPE-Gefahr): Liste ganz lesen, erstes Element nehmen.
    mapfile -t LATEST_DB_CANDIDATES < <(find "$BACKUP_DIR" -maxdepth 1 -name "$BACKUP_GLOB" -type f 2>/dev/null | sort -r)
    LATEST_DB="${LATEST_DB_CANDIDATES[0]:-}"
    if [[ "$DRILL_FORMAT" == "prod" ]]; then
        echo "ACHTUNG - Ohne explizite Satzwahl wird der neueste Restore-Satz verwendet."
    fi
fi
if [[ -z "$LATEST_DB" || ! -f "$LATEST_DB" ]]; then
    fail "Kein DB-Backup (${BACKUP_GLOB}) in ${BACKUP_DIR} gefunden"
    echo "Drill abgebrochen — keine Restore-Quelle. Laeuft der Backup-Timer? (Ops-Runbook §3.3)"
    exit 1
fi
echo "Drill gegen DB-Backup: $LATEST_DB"
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    SELECTED_TIMESTAMP="${LATEST_DB##*/}"
    SELECTED_TIMESTAMP="${SELECTED_TIMESTAMP#anlaufstelle_}"
    SELECTED_TIMESTAMP="${SELECTED_TIMESTAMP%.sql.gz.enc}"
    BACKUP_TIMESTAMP="$SELECTED_TIMESTAMP"
    CURRENT_TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
    if [[ "$BACKUP_TIMESTAMP" > "$CURRENT_TIMESTAMP" ]]; then
        echo "WARNUNG: Ausgewaehlter Backup-Satz hat einen Zeitstempel in der Zukunft (moegliche Uhrzeit-Drift); er wird trotzdem geprueft." >&2
    fi
    MEDIA_FILE="${LATEST_DB%.sql.gz.enc}_media.tar.gz.enc"
    echo "Ausgewaehlter Restore-Satz: DB=${LATEST_DB##*/}, Medien=${MEDIA_FILE##*/}, HWM=audit_hwm-${BACKUP_TIMESTAMP}.json"
else
    echo "Ausgewaehlter Restore-Satz: DB=${LATEST_DB##*/} (dev-Format ohne Medien-/HWM-Artefakt)"
fi

# Der Prod-Drill braucht den beim Backup authentifiziert mitgeschriebenen,
# absoluten Tabellenstand. Ein Vergleich mit der Live-DB ist auf einem frischen
# Restore-Host wertlos, wenn diese selbst aus derselben unvollstaendigen Quelle
# aufgebaut wurde.
declare -A MANIFEST_COUNTS=()
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    MANIFEST_FILE="$(backup_set_manifest_path "$LATEST_DB")"
    # Vorpruefung VOR der Bibliothek, bewusst doppelt: ``backup_set_load``
    # authentifiziert das Manifest ebenfalls, meldet aber mit seinem eigenen
    # Wortlaut. Der Drill soll die beiden Ursachen „Manifest fehlt" und
    # „Signatur passt nicht" getrennt und in seiner Schritt-0-Sprache melden —
    # danach uebernimmt die gemeinsame Satzpruefung.
    if [[ ! -f "$MANIFEST_FILE" ]]; then
        fail "Schritt 0: Zeilenzahl-Manifest fehlt (${MANIFEST_FILE##*/})"
        exit 1
    elif ! backup_verify_hmac "$MANIFEST_FILE"; then
        fail "Schritt 0: HMAC-Verifikation des Zeilenzahl-Manifests fehlgeschlagen (${MANIFEST_FILE##*/})"
        exit 1
    fi
    if ! backup_set_load "$MANIFEST_FILE" 2>&1; then
        fail "Schritt 0: Backup-Satz nicht authentifizierbar (${MANIFEST_FILE##*/})"
        exit 1
    fi
    # Authentifizierte Manifest-Werte unter den im Drill etablierten Namen
    # bereitstellen (Diagnose, Ergebnisblock, spaetere Schritte).
    MANIFEST_TIMESTAMP="$BACKUP_SET_TIMESTAMP"
    MANIFEST_DB_SHA256="$BACKUP_SET_DB_SHA256"
    MANIFEST_MEDIA_SHA256="$BACKUP_SET_MEDIA_SHA256"
    MANIFEST_HWM_SHA256="$BACKUP_SET_HWM_SHA256"
    MANIFEST_MEDIA_FILE="$(backup_set_media_path "$LATEST_DB")"
    MANIFEST_HWM_FILE="${BACKUP_DIR}/audit_hwm-${BACKUP_TIMESTAMP}.json"
    # Selbstpruefung des Drills zusaetzlich zur Satzbibliothek: die Bibliothek
    # bindet den Satz an den Dump-DATEINAMEN, der Drill leitet daraus aber auch
    # die Namen der Medien- und Anker-Datei ab. Gingen beide je auseinander,
    # pruefte der Drill Artefakte eines anderen Satzes.
    ACTUAL_DB_SHA256=$(sha256sum "$LATEST_DB" | awk '{print $1}')
    if [[ "$MANIFEST_TIMESTAMP" != "$BACKUP_TIMESTAMP" ]]; then
        fail "Schritt 0: Manifest-Satzkennung passt nicht zum gewaehlten Dump (${MANIFEST_TIMESTAMP:-fehlt} != ${BACKUP_TIMESTAMP})"
        exit 1
    elif [[ "$ACTUAL_DB_SHA256" != "$MANIFEST_DB_SHA256" ]]; then
        fail "Schritt 0: Manifest-Dump-Hash passt nicht zum gewaehlten Dump (${LATEST_DB##*/})"
        exit 1
    fi
    # Ein vorhandenes Archiv wird immer mitgeprueft — auch wenn das Manifest
    # keines bindet: dann ist es ein Fremdkoerper (identisch zu backup.sh --verify).
    DRILL_MEDIA_ARG=""
    if [[ -f "$MANIFEST_MEDIA_FILE" ]]; then
        DRILL_MEDIA_ARG="$MANIFEST_MEDIA_FILE"
    fi
    if ! backup_set_verify_artifacts "$LATEST_DB" "$DRILL_MEDIA_ARG" "$MANIFEST_HWM_FILE" 2>&1; then
        fail "Schritt 0: Backup-Satz nicht zusammengehoerig oder unvollstaendig (${MANIFEST_FILE##*/})"
        exit 1
    fi
    # Der Zeilenzahl-Abgleich (Schritt 3/3b) arbeitet weiter auf MANIFEST_COUNTS.
    # Der Praefix-Strip bleibt stehen, damit die Uebernahme unabhaengig davon
    # korrekt ist, ob die Bibliothek ihre Schluessel mit oder ohne ``table_``
    # fuehrt.
    for key in "${!BACKUP_SET_COUNTS[@]}"; do
        value="${BACKUP_SET_COUNTS[$key]}"
        MANIFEST_COUNTS["${key#table_}"]="$value"
    done
    ok "Schritt 0: Zeilenzahl-Manifest authentifiziert (${MANIFEST_FILE##*/})"
fi

# Schritt 1: Wegwerf-DB anlegen
DRILL_DB="anlaufstelle_drill_$$"
cleanup() {
    if [[ -n "$MEDIA_TMP_DIR" ]]; then
        rm -rf -- "$MEDIA_TMP_DIR"
    fi
    if [[ -n "$MEDIA_LIST_FILE" ]]; then
        rm -f -- "$MEDIA_LIST_FILE"
    fi
    psql_su -d postgres -c "DROP DATABASE IF EXISTS \"${DRILL_DB}\";" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if psql_su -d postgres -c "CREATE DATABASE \"${DRILL_DB}\";" >/dev/null 2>&1; then
    ok "Schritt 1: Wegwerf-DB ${DRILL_DB} angelegt"
else
    fail "Schritt 1: Wegwerf-DB konnte nicht angelegt werden"
    exit 1
fi

# Schritt 2: decrypten + restore in die Wegwerf-DB — Format-abhaengig
# (prod: gunzip|psql, dev: pg_restore).
#
# Nur im Dev-Format kann das Dump beim ``REFRESH MATERIALIZED VIEW
# core_statistics_event_flat`` an FORCE ROW LEVEL SECURITY auf core_event
# scheitern; pg_restore endet dann mit rc=1 ("errors ignored on restore: 1").
# Diesen reinen Statistik-Cache-Fehler toleriert der Dev-Zweig gezielt. Im
# Prod-Format beendet jeder psql-Fehler den Restore; alle weiteren Fehler
# bleiben in beiden Formaten ein FAIL.
RESTORE_ERR="$(mktemp)"
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    # Prod-Format: plain SQL (gzip) -> psql. A4.3-HMAC-Sidecar VOR dem
    # Entschluesseln pruefen (backup.sh schreibt ihn) — ein manipuliertes oder
    # beschaedigtes Backup wird so erkannt, bevor etwas eingespielt wird.
    if ! backup_verify_hmac "$LATEST_DB" 2>>"$RESTORE_ERR"; then
        fail "Schritt 2: HMAC-Verifikation fehlgeschlagen — Backup manipuliert/beschaedigt oder Sidecar fehlt"
        sed 's/^/       /' "$RESTORE_ERR" | head -5
        rm -f "$RESTORE_ERR"
        exit 1
    fi
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$LATEST_DB" \
        | gunzip \
        | dc exec -T db psql -U "$SU_USER" -d "$DRILL_DB" -v ON_ERROR_STOP=1 --single-transaction \
            >/dev/null 2>"$RESTORE_ERR"
    # PIPESTATUS sofort in einem Rutsch sichern — jede folgende Zuweisung wuerde
    # es ueberschreiben (und mit ``set -u`` zum unbound-Fehler fuehren).
    PIPE_RC=("${PIPESTATUS[@]}")
    OSSL_RC="${PIPE_RC[0]}"
    GUNZIP_RC="${PIPE_RC[1]}"
    PSQL_RC="${PIPE_RC[2]}"
    if [[ "$OSSL_RC" -ne 0 ]]; then
        fail "Schritt 2: Entschluesselung fehlgeschlagen (openssl rc=$OSSL_RC) — BACKUP_ENCRYPTION_KEY falsch?"
        rm -f "$RESTORE_ERR"
        exit 1
    elif [[ "$GUNZIP_RC" -ne 0 ]]; then
        fail "Schritt 2: gunzip fehlgeschlagen (rc=$GUNZIP_RC) — Backup beschaedigt?"
        rm -f "$RESTORE_ERR"
        exit 1
    fi
    if [[ "$PSQL_RC" -ne 0 ]]; then
        fail "Schritt 2: DB-Restore fehlgeschlagen (psql rc=$PSQL_RC):"
        sed 's/^/       /' "$RESTORE_ERR" | head -15
        rm -f "$RESTORE_ERR"
        exit 1
    fi
    ok "Schritt 2: DB-Backup wiederhergestellt (psql, plain SQL)"
else
    # Dev-Format: custom format -> pg_restore.
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$LATEST_DB" \
        | dc exec -T db pg_restore -U "$SU_USER" -d "$DRILL_DB" >/dev/null 2>"$RESTORE_ERR"
    # PIPESTATUS sofort in einem Rutsch sichern — jede folgende Zuweisung wuerde es
    # ueberschreiben (und mit ``set -u`` zum unbound-Fehler fuehren).
    PIPE_RC=("${PIPESTATUS[@]}")
    OSSL_RC="${PIPE_RC[0]}"
    RESTORE_RC="${PIPE_RC[1]}"
    N_ERR=$(grep -c "pg_restore: error:" "$RESTORE_ERR" 2>/dev/null || true)
    N_MV=$(grep -c "REFRESH MATERIALIZED VIEW" "$RESTORE_ERR" 2>/dev/null || true)
    if [[ "$OSSL_RC" -ne 0 ]]; then
        fail "Schritt 2: Entschluesselung fehlgeschlagen (openssl rc=$OSSL_RC) — BACKUP_ENCRYPTION_KEY falsch?"
        rm -f "$RESTORE_ERR"
        exit 1
    elif [[ "$RESTORE_RC" -eq 0 ]]; then
        ok "Schritt 2: DB-Backup wiederhergestellt (pg_restore, custom format)"
    elif [[ "$N_ERR" -gt 0 && "$N_ERR" -eq "$N_MV" ]]; then
        ok "Schritt 2: DB-Backup wiederhergestellt — ${N_MV} MV-Refresh(es) wg. FORCE-RLS uebersprungen (benigne)"
    else
        fail "Schritt 2: DB-Restore fehlgeschlagen (pg_restore rc=$RESTORE_RC, ${N_ERR} Fehler):"
        sed 's/^/       /' "$RESTORE_ERR" | head -15
        rm -f "$RESTORE_ERR"
        exit 1
    fi
fi
rm -f "$RESTORE_ERR"

# Schritt 3: Stichproben pro Tabelle
EXPECTED_TABLES=("core_facility" "core_client" "core_event" "core_auditlog" "core_workitem" "core_attachment")
SAMPLE_FAIL=0
for tbl in "${EXPECTED_TABLES[@]}"; do
    COUNT=$(psql_su -d "$DRILL_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
    if [[ -z "$COUNT" || "$COUNT" =~ [^0-9] ]]; then
        echo "       - ${tbl}: nicht abfragbar (${COUNT:-leer})"
        SAMPLE_FAIL=1
    elif [[ "$DRILL_FORMAT" == "prod" ]] \
        && [[ "${MANIFEST_COUNTS[$tbl]}" -gt 0 && "$COUNT" -eq 0 ]]; then
        echo "       - ${tbl}: 0 Zeilen, Manifest erwartet ${MANIFEST_COUNTS[$tbl]}"
        SAMPLE_FAIL=1
    else
        echo "       - ${tbl}: ${COUNT} Zeilen"
    fi
done
if [[ "$SAMPLE_FAIL" -eq 0 ]]; then
    ok "Schritt 3: Tabellen-Stichproben erfolgreich"
else
    fail "Schritt 3: mindestens eine Tabelle nicht abfragbar"
fi

# Schritt 3b: Vollstaendigkeits-Guard gegen den absoluten, authentifizierten
# Referenzstand aus dem zum Dump gehoerenden Manifest. Dieser Nachweis MUSS vor
# mark_restore_verified (Schritt 7) stehen.
COMPLETENESS_TABLES=("core_facility" "core_client" "core_event" "core_workitem" "core_auditlog" "core_attachment")
COMPLETENESS_FAIL=0
AUDIT_ROW_COUNT_BEFORE_CHAIN=""
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    for tbl in "${COMPLETENESS_TABLES[@]}"; do
        RESTORED_COUNT=$(psql_su -d "$DRILL_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
        EXPECTED_COUNT="${MANIFEST_COUNTS[$tbl]}"
        if [[ "$tbl" == "core_auditlog" ]]; then
            AUDIT_ROW_COUNT_BEFORE_CHAIN="$RESTORED_COUNT"
        fi
        if [[ -z "$RESTORED_COUNT" || ! "$RESTORED_COUNT" =~ ^[0-9]+$ ]]; then
            echo "       - ${tbl}: im Restore nicht abfragbar (${RESTORED_COUNT:-leer})"
            COMPLETENESS_FAIL=1
        elif [[ "$RESTORED_COUNT" -ne "$EXPECTED_COUNT" ]]; then
            echo "       - ${tbl}: Restore=${RESTORED_COUNT}, Manifest=${EXPECTED_COUNT} — unvollstaendiger Restore"
            COMPLETENESS_FAIL=1
        else
            echo "       - ${tbl}: Restore=${RESTORED_COUNT}, Manifest=${EXPECTED_COUNT}"
        fi
    done
else
    # Das dev-Format besitzt kein signiertes Manifest. Seine alte Zusicherung
    # bleibt deshalb erhalten: exakter Vergleich der Wegwerf-DB mit der Live-DB.
    for tbl in "${COMPLETENESS_TABLES[@]}"; do
        RESTORED_COUNT=$(psql_su -d "$DRILL_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
        LIVE_COUNT=$(psql_su -d "$POSTGRES_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
        if [[ "$tbl" == "core_auditlog" ]]; then
            AUDIT_ROW_COUNT_BEFORE_CHAIN="$RESTORED_COUNT"
        fi
        if [[ ! "$RESTORED_COUNT" =~ ^[0-9]+$ || ! "$LIVE_COUNT" =~ ^[0-9]+$ ]]; then
            echo "       - ${tbl}: Restore oder Live-DB nicht abfragbar"
            COMPLETENESS_FAIL=1
        elif [[ "$RESTORED_COUNT" -ne "$LIVE_COUNT" ]]; then
            echo "       - ${tbl}: Restore=${RESTORED_COUNT}, Live=${LIVE_COUNT} — unvollstaendiger Restore"
            COMPLETENESS_FAIL=1
        else
            echo "       - ${tbl}: Restore=${RESTORED_COUNT}, Live=${LIVE_COUNT}"
        fi
    done
fi
if [[ "$COMPLETENESS_FAIL" -eq 0 ]]; then
    if [[ "$DRILL_FORMAT" == "prod" ]]; then
        ok "Schritt 3b: Vollstaendigkeits-Guard gegen Manifest bestanden"
    else
        ok "Schritt 3b: Vollstaendigkeits-Guard gegen Live-DB bestanden"
    fi
else
    fail "Schritt 3b: Restore ist unvollstaendig — nicht als verifiziert markieren"
fi

# Schritt 2b: Offsite-verankerten Audit-HWM im Prod-Pfad pruefen (Refs #2022).
# Die Schrittnummer bleibt fuer das referenzierte Pruefblatt stabil. Die
# Ausfuehrung folgt bewusst erst auf den exakten Manifest-Zaehlvergleich: Der
# Management-Command protokolliert einen erfolgreichen Lauf in core_auditlog
# und darf den unveraenderten Restore-Bestand deshalb erst danach erweitern.
# Der maintenance-Service stellt den erforderlichen BYPASSRLS-Kontext bereit.
if [[ "$DRILL_FORMAT" == "prod" && -n "${DJANGO_AUDIT_HWM_PATH:-}" ]]; then
    HWM_FILE="${BACKUP_DIR}/audit_hwm-${BACKUP_TIMESTAMP}.json"
    HWM_VERIFY_ERR="$(mktemp)"
    HWM_VERIFY_OUT="$(mktemp)"
    if [[ ! -f "$HWM_FILE" ]]; then
        fail "Schritt 2b: Kein Audit-HWM-Backup fuer den Restore-Satz (${HWM_FILE##*/})"
    elif dc run --rm -T -e POSTGRES_DB="$DRILL_DB" \
        -e DJANGO_AUDIT_HWM_PATH="$HWM_DRILL_CONTAINER_PATH" --entrypoint sh maintenance \
        -c 'cat > "$DJANGO_AUDIT_HWM_PATH" && python manage.py shell -c "from core.services.audit.hwm import HighWaterMarkStore; import os; HighWaterMarkStore.load(os.environ['\''DJANGO_AUDIT_HWM_PATH'\''])" && python manage.py verify_audit_chain --require-anchor' \
        < "$HWM_FILE" >"$HWM_VERIFY_OUT" 2>"$HWM_VERIFY_ERR"; then
        sed 's/^/       /' "$HWM_VERIFY_OUT"
        ok "Schritt 2b: Audit-HWM-Signatur verifiziert und gegen restaurierte Kette geprueft (${HWM_FILE##*/})"
    else
        cat "$HWM_VERIFY_OUT" "$HWM_VERIFY_ERR" | sed 's/^/       /'
        if grep -qiE 'RLS-gefilterte|connection|could not connect|database .* does not exist|password authentication|OperationalError' \
            "$HWM_VERIFY_OUT" "$HWM_VERIFY_ERR"; then
            fail "Schritt 2b: Rollen-/Verbindungsfehler bei der Audit-Kettenpruefung (${HWM_FILE##*/})"
        elif grep -qiE 'HwmIntegrityError|Signatur ungueltig|JSONDecodeError' "$HWM_VERIFY_OUT" "$HWM_VERIFY_ERR"; then
            fail "Schritt 2b: Audit-HWM-Signaturpruefung fehlgeschlagen (${HWM_FILE##*/}) — Datei manipuliert oder AUDIT_HASH_KEY falsch"
        else
            fail "Schritt 2b: Audit-HWM passt nicht zur restaurierten Kette (${HWM_FILE##*/})"
        fi
    fi
    rm -f "$HWM_VERIFY_OUT" "$HWM_VERIFY_ERR"
elif [[ "$DRILL_FORMAT" == "prod" ]]; then
    # Legacy fallback: the production default above prevents this branch from
    # skipping an anchor when the host variable is unset or empty (Refs #2527).
    skip "Schritt 2b: Audit-Anker NICHT geprueft (keine Anker-Konfiguration) — DJANGO_AUDIT_HWM_PATH nicht gesetzt (${ENV_FILE}); dieser Lauf belegt keine unversehrte Audit-Kette"
else
    skip "Schritt 2b: Audit-Anker NICHT geprueft (keine Anker-Konfiguration) — das dev-Backup-Format fuehrt keinen externen Audit-Anker mit"
fi

# Schritt 4: RLS-Policy-Check
RLS_COUNT=$(psql_su -d "$DRILL_DB" -t -A \
    -c "SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true;" 2>/dev/null | tr -d '[:space:]')
if [[ "$RLS_COUNT" =~ ^[0-9]+$ ]] && [[ "$RLS_COUNT" -ge 18 ]]; then
    ok "Schritt 4: RLS aktiv auf ${RLS_COUNT} core_*-Tabellen (erwartet >= 18)"
else
    fail "Schritt 4: RLS-Tabellen-Anzahl unerwartet (${RLS_COUNT:-nicht abfragbar})"
fi

# Schritt 5: AuditLog-Immutability-Trigger-Check
TRIGGER_COUNT=$(psql_su -d "$DRILL_DB" -t -A \
    -c "SELECT COUNT(*) FROM pg_trigger WHERE tgname = 'auditlog_immutable';" 2>/dev/null | tr -d '[:space:]')
if [[ "$TRIGGER_COUNT" == "1" ]]; then
    if [[ ! "$AUDIT_ROW_COUNT_BEFORE_CHAIN" =~ ^[0-9]+$ ]]; then
        fail "Schritt 5: AuditLog-Zeilenzahl nicht abfragbar"
    elif [[ "$AUDIT_ROW_COUNT_BEFORE_CHAIN" -eq 0 ]]; then
        echo "NICHT PRUEFBAR - Schritt 5: core_auditlog ist leer; Trigger kann nicht ausgeloest werden"
        DRILL_UNDETERMINED=1
    else
        # Raw UPDATE muss am Trigger scheitern. Output separat einfangen: psql
        # endet bei der erwarteten Exception mit rc!=0, was eine direkte Pipe
        # verfaelschen wuerde. Daher erst in eine Variable, dann grep.
        UPDATE_OUT=$(psql_su -d "$DRILL_DB" \
            -c "UPDATE core_auditlog SET action = 'logout' WHERE id = (SELECT id FROM core_auditlog LIMIT 1);" 2>&1 || true)
        if grep -qi "immutable" <<<"$UPDATE_OUT"; then
            ok "Schritt 5: auditlog_immutable-Trigger blockt UPDATE"
        else
            fail "Schritt 5: Trigger existiert, blockt UPDATE aber NICHT — kritisch"
        fi
    fi
else
    fail "Schritt 5: auditlog_immutable-Trigger fehlt in Restore"
fi

# Schritt 6: Passendes Medien-Archiv authentifizieren und auf dem Host
# probeweise entpacken (Refs #2195). Das Prod-Backup benennt beide Archive mit
# demselben Zeitstempel. Ein fehlendes Medien-Archiv ist nur zulaessig, wenn
# das authentifizierte Manifest keinen Medien-Hash enthaelt und damit einen
# DB-only-Satz beschreibt.
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    LATEST_MEDIA="$MEDIA_FILE"
    if [[ ! -f "$LATEST_MEDIA" ]]; then
        echo "INFO - Schritt 6: Kein passendes Medien-Archiv — uebersprungen (DB-only-Backup zulaessig)"
    elif ! backup_verify_hmac "$LATEST_MEDIA"; then
        fail "Schritt 6: Medien-HMAC-Verifikation fehlgeschlagen — Archiv manipuliert/beschaedigt oder Sidecar fehlt"
    elif [[ ! -d "${TMPDIR:-/tmp}" || ! -w "${TMPDIR:-/tmp}" ]]; then
        fail "Schritt 6: TMPDIR ${TMPDIR:-/tmp} fehlt oder ist nicht beschreibbar"
    elif ! MEDIA_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/anl-drill-media-XXXXXX")" \
        || ! MEDIA_LIST_FILE="$(mktemp "${TMPDIR:-/tmp}/anl-drill-media-list-XXXXXX")"; then
        fail "Schritt 6: Temporaeres Verzeichnis fuer Medien-Pruefung konnte nicht angelegt werden"
    else
        MEDIA_ARCHIVE_BYTES=$(wc -c <"$LATEST_MEDIA" | tr -d '[:space:]')
        MEDIA_REQUIRED_BYTES=$((MEDIA_ARCHIVE_BYTES * 2))
        MEDIA_AVAILABLE_KB=$(df -Pk "$MEDIA_TMP_DIR" | awk 'NR == 2 {print $4}')
        if [[ ! "$MEDIA_AVAILABLE_KB" =~ ^[0-9]+$ ]]; then
            fail "Schritt 6: Freier Platz in TMPDIR ${TMPDIR:-/tmp} konnte nicht ermittelt werden"
        elif [[ $((MEDIA_AVAILABLE_KB * 1024)) -lt "$MEDIA_REQUIRED_BYTES" ]]; then
            fail "Schritt 6: Nicht genug freier Platz in TMPDIR ${TMPDIR:-/tmp} — mindestens ${MEDIA_REQUIRED_BYTES} Bytes (Archivgroesse x2) erforderlich"
        else
            openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$LATEST_MEDIA" \
                | gunzip \
                | tar -C "$MEDIA_TMP_DIR" -xvf - >"$MEDIA_LIST_FILE"
            MEDIA_EXTRACT_RC=("${PIPESTATUS[@]}")
            if [[ "${MEDIA_EXTRACT_RC[0]}" -ne 0 || "${MEDIA_EXTRACT_RC[1]}" -ne 0 || "${MEDIA_EXTRACT_RC[2]}" -ne 0 ]]; then
                fail "Schritt 6: Medien-Archiv konnte nicht vollstaendig probeentpackt werden"
            else
                MEDIA_LISTED_COUNT=0
                MEDIA_MATCHED_COUNT=0
                while IFS= read -r MEDIA_ENTRY; do
                    [[ -z "$MEDIA_ENTRY" ]] && continue
                    MEDIA_LISTED_COUNT=$((MEDIA_LISTED_COUNT + 1))
                    if [[ -e "$MEDIA_TMP_DIR/$MEDIA_ENTRY" || -L "$MEDIA_TMP_DIR/$MEDIA_ENTRY" ]]; then
                        MEDIA_MATCHED_COUNT=$((MEDIA_MATCHED_COUNT + 1))
                    fi
                done <"$MEDIA_LIST_FILE"
                MEDIA_FILE_COUNT=$(find "$MEDIA_TMP_DIR" -type f \
                    -exec sh -c 'printf "%s\n" "$#"' sh {} + \
                    | awk '{total += $1} END {print total + 0}')
                MEDIA_BYTES=$(find "$MEDIA_TMP_DIR" -type f -exec cat {} + | wc -c | tr -d '[:space:]')
                if [[ "$MEDIA_LISTED_COUNT" -eq 0 || "$MEDIA_MATCHED_COUNT" -ne "$MEDIA_LISTED_COUNT" ]]; then
                    fail "Schritt 6: Probeentpackung stimmt nicht mit tar-Listing ueberein (${MEDIA_MATCHED_COUNT}/${MEDIA_LISTED_COUNT} Eintraege)"
                else
                    ok "Schritt 6: Medien ${LATEST_MEDIA##*/}: ${MEDIA_FILE_COUNT} Dateien, ${MEDIA_BYTES} Bytes, HMAC ok"
                fi
            fi
        fi
    fi
else
    echo "INFO - Schritt 6: Kein Medien-Archiv im dev-Backup-Format — uebersprungen"
fi

# Schritt 7: Bei vollem Erfolg den Restore-Test im AuditLog dokumentieren.
# Laeuft im web-Container (DSGVO Art. 32 lit. c: Wiederherstellbarkeit belegt).
#
# Refs #2341: „Voller Erfolg" schliesst die uebersprungenen Pflichtschritte ein.
# Ohne die Luecken-Zaehlung schrieb der Drill den Nachweis auch dann, wenn er im
# selben Lauf mit Exit-Code 2 ausdruecklich KEINEN vollstaendigen Nachweis
# behauptete (Refs #2316) — die Compliance-Karte „Restore-Test" sprang auf
# gruen und widersprach dem Exit-Code des Skripts.
if [[ "$DRILL_FAILED" -eq 0 && "$DRILL_UNDETERMINED" -eq 0 && ${#DRILL_SKIPPED[@]} -eq 0 ]]; then
    NOTE="Restore-Drill $(date -u +%Y-%m-%dT%H:%MZ) gegen ${LATEST_DB##*/} -> ${DRILL_DB}"
    if dc exec -T web python manage.py mark_restore_verified --note "$NOTE" >/dev/null 2>&1; then
        ok "Schritt 7: Restore-Test im AuditLog dokumentiert (mark_restore_verified)"
    else
        fail "Schritt 7: mark_restore_verified fehlgeschlagen — Compliance-Check bleibt unknown (wahrscheinlich weil der web-Container nicht laeuft oder nicht healthy ist; siehe Runbook)"
    fi
elif [[ "$DRILL_FAILED" -eq 0 && "$DRILL_UNDETERMINED" -eq 0 ]]; then
    echo "INFO - Schritt 7: kein Compliance-Nachweis geschrieben — ${#DRILL_SKIPPED[@]} Pflichtschritt(e) uebersprungen."
fi

if [[ ${#DRILL_SKIPPED[@]} -gt 0 ]]; then
    echo
    echo "Uebersprungen (${#DRILL_SKIPPED[@]}) — nicht geprueft, daher auch nicht bestanden:"
    printf '  - %s\n' "${DRILL_SKIPPED[@]}"
fi

if [[ "$DRILL_FAILED" -ne 0 ]]; then
    echo
    echo "DRILL FEHLGESCHLAGEN — mindestens ein Schritt hat FAIL gemeldet."
    exit 1
fi

if [[ "$DRILL_UNDETERMINED" -ne 0 ]]; then
    echo
    echo "DRILL OFFEN — mindestens ein Schritt war nicht pruefbar; Restore wird nicht als verifiziert markiert."
    exit 1
fi

# Uebersprungene Pflichtschritte sind kein Erfolg (Refs #2316): In der
# Werkskonfiguration (kein Ankerpfad) blieb Schritt 2b ungeprueft, der Lauf
# endete aber mit Exit 0 — die Alarmregel des Runbooks (Exit != 0) schlug nie
# an und die fehlende Ankerpruefung blieb unsichtbar. Eigener Exit-Code 2.
if [[ ${#DRILL_SKIPPED[@]} -gt 0 ]]; then
    echo
    echo "DRILL MIT LUECKEN — die ausgefuehrten Schritte sind gruen, ${#DRILL_SKIPPED[@]} Pflichtschritt(e) wurden uebersprungen: kein vollstaendiger Nachweis."
    echo "  Es wurde kein Compliance-Nachweis geschrieben (mark_restore_verified nicht ausgefuehrt) —"
    echo "  der Compliance-Check 'Restore-Test' bleibt auf dem Stand des letzten vollstaendigen Laufs."
    echo "  Exit-Code 2 — Ursache der Luecke beheben (siehe Liste oben) und erneut fahren."
    exit 2
fi

echo
echo "Drill erfolgreich abgeschlossen — Restore verifiziert und dokumentiert."
