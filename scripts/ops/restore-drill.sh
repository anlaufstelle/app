#!/usr/bin/env bash
set -uo pipefail

# Anlaufstelle Backup-Restore-Drill (Refs #739, #720, #981)
#
# Verifiziert, dass das aktuellste dev-Backup vollstaendig in eine
# Wegwerf-DB wiederherstellbar ist und dabei die Verteidigungslinien
# (RLS, AuditLog-Immutability-Trigger) erhalten bleiben. Bei Erfolg wird
# der Lauf via ``manage.py mark_restore_verified`` im AuditLog
# dokumentiert -> Compliance-Check „Restore-Test" springt auf gruen.
#
# Zwei ausgelieferte Backup-Pfade -> zwei Formate; der Drill deckt BEIDE ab
# (OPS-05, Refs #1336). Wahl per Flag ``--dev``/``--prod`` oder ``$DRILL_FORMAT``;
# ohne Angabe wird anhand der vorhandenen Backup-Dateien auto-erkannt.
#
#   dev  : dev-ops/deploy/backup.sh (dev.anlaufstelle.app)
#          - Quelle:  $DEV_BACKUP_DIR/dump-*.pgc.enc  (Default /var/backups/anl)
#          - Format:  pg_dump --format=custom -> pg_restore (NICHT psql)
#          - Stack:   docker-compose.dev.yml --env-file .env.dev
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
# Die Dump-Dateien sind 0600 und gehoeren root — daher als root ausfuehren:
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

# --- Backup-Format / Modus (OPS-05, Refs #1336) ---------------------------
DRILL_FORMAT="${DRILL_FORMAT:-}"
case "${1:-}" in
    --prod) DRILL_FORMAT="prod"; shift ;;
    --dev)  DRILL_FORMAT="dev";  shift ;;
    --*)    echo "FAIL - unbekannte Option: $1 (erlaubt: --dev, --prod)" >&2; exit 2 ;;
esac

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
        set -a
        # shellcheck disable=SC1090
        source "$env_file"
        set +a
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
    _dev_hit=$(find "$DEV_BACKUP_DIR" -maxdepth 1 -name 'dump-*.pgc.enc' -type f 2>/dev/null | head -n1)
    _prod_hit=$(find "$_prod_backup_dir" -maxdepth 1 -name 'anlaufstelle_*.sql.gz.enc' -type f 2>/dev/null | head -n1)
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
    COMPOSE=(docker compose -f docker-compose.prod.yml)
else
    ENV_FILE="${ENV_FILE:-$COMPOSE_DIR/.env.dev}"
    BACKUP_GLOB='dump-*.pgc.enc'
    COMPOSE=(docker compose -f docker-compose.dev.yml --env-file .env.dev)
fi

# Env laden (nur Variablen) — liefert POSTGRES_*, BACKUP_ENCRYPTION_KEY.
if [[ -r "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

if [[ "$DRILL_FORMAT" == "prod" ]]; then
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

# Der Vollstaendigkeits-Guard (Schritt 3b) gleicht die Wegwerf-DB gegen die
# Live-DB ab — dafuer muss der Live-DB-Name bekannt sein (wie backup.sh
# --verify). Fehlt er, koennte ein leerer/teilweiser Restore nicht als solcher
# erkannt werden; fail-closed statt still gruen (Refs #1336).
: "${POSTGRES_DB:?POSTGRES_DB nicht gesetzt (aus $ENV_FILE) — der Vollstaendigkeits-Abgleich braucht die Live-DB}"

# Der Drill braucht CREATE DATABASE — das kann auf dem 3-Rollen-Modell nur der
# Postgres-Superuser (``anlaufstelle_admin`` ist BYPASSRLS, aber NICHT CREATEDB;
# ``anlaufstelle`` ist der RLS-gebundene App-User). Der Superuser bypassed
# zugleich RLS, sodass der Restore (pg_restore bzw. psql) die geschuetzten
# Tabellen fuellen kann. Verbindung ueber den lokalen Socket im db-Container
# (pg_hba: local trust) — daher kein Passwort noetig.
SU_USER="${POSTGRES_SUPERUSER:-postgres}"

DRILL_FAILED=0
HWM_COPIED=0
HWM_DRILL_CONTAINER_PATH="/tmp/audit_hwm-drill-$$.json"
MEDIA_TMP_DIR=""
MEDIA_LIST_FILE=""
ok() { echo "OK   - $*"; }
fail() {
    echo "FAIL - $*"
    DRILL_FAILED=1
}

dc() { "${COMPOSE[@]}" "$@"; }
psql_su() { dc exec -T db psql -U "$SU_USER" "$@"; }

# Schritt 0: neuestes Backup finden
LATEST_DB=$(find "$BACKUP_DIR" -maxdepth 1 -name "$BACKUP_GLOB" -type f 2>/dev/null | sort -r | head -n1)
if [[ -z "$LATEST_DB" ]]; then
    fail "Kein DB-Backup (${BACKUP_GLOB}) in ${BACKUP_DIR} gefunden"
    echo "Drill abgebrochen — keine Restore-Quelle. Laeuft der Backup-Timer? (Ops-Runbook §3.3)"
    exit 1
fi
echo "Drill gegen DB-Backup: $LATEST_DB"

# Schritt 1: Wegwerf-DB anlegen
DRILL_DB="anlaufstelle_drill_$$"
cleanup() {
    if [[ -n "$MEDIA_TMP_DIR" ]]; then
        rm -rf -- "$MEDIA_TMP_DIR"
    fi
    if [[ -n "$MEDIA_LIST_FILE" ]]; then
        rm -f -- "$MEDIA_LIST_FILE"
    fi
    if [[ "$HWM_COPIED" -eq 1 ]]; then
        dc exec -T web rm -f "$HWM_DRILL_CONTAINER_PATH" >/dev/null 2>&1 || true
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
# Bekannte benigne Ausnahme: das Dump enthaelt ``REFRESH MATERIALIZED VIEW
# core_statistics_event_flat``. Beim Restore scheitert dieser Refresh an
# FORCE ROW LEVEL SECURITY auf core_event (RLS gilt dort selbst fuer den Owner,
# und der Refresh laeuft nicht mit BYPASSRLS) -> pg_restore endet mit rc=1
# ("errors ignored on restore: 1"). Das betrifft nur den Statistik-Cache, nicht
# die Nutzdaten — der mv-refresh-Timer baut ihn stuendlich neu. Solche reinen
# MV-Refresh-Fehler tolerieren wir; jeder andere/zusaetzliche Fehler bleibt FAIL.
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
        | dc exec -T db psql -U "$SU_USER" -d "$DRILL_DB" >/dev/null 2>"$RESTORE_ERR"
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
    # psql laeuft ohne ON_ERROR_STOP (wie backup.sh --verify): der abschliessende
    # REFRESH MATERIALIZED VIEW kann an FORCE-RLS scheitern, ohne die Nutzdaten
    # zu betreffen (der mv-refresh-Timer baut den Cache neu). Der psql-Exit-Code
    # ist damit KEIN Vollstaendigkeits-Beweis (er bleibt auch bei echten Fehlern
    # 0) — die Vollstaendigkeit belegt fail-closed der Guard in Schritt 3b
    # (Zeilenabgleich Restore vs. Live) plus RLS-/Trigger-Check (Schritt 4/5).
    if [[ "$PSQL_RC" -eq 0 ]]; then
        ok "Schritt 2: DB-Backup wiederhergestellt (psql, plain SQL)"
    else
        ok "Schritt 2: DB-Backup eingespielt — psql meldete rc=$PSQL_RC (i.d.R. benigner MV-Refresh unter FORCE-RLS; Schritt 3b prueft die Nutzdaten fail-closed)"
    fi
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

# Schritt 2b: Offsite-verankerten Audit-HWM im Prod-Pfad pruefen (Refs #2022).
# Die Datei wird in das beschreibbare /tmp des Web-Containers kopiert und dort
# ueber dieselbe HighWaterMarkStore.load()-API geladen wie im regulaeren
# verify_audit_chain-Pfad. Eine fehlende/manipulierte HWM faerbt den Drill rot,
# bricht die folgenden Restore-Pruefungen aber bewusst nicht hart ab.
if [[ "$DRILL_FORMAT" == "prod" && -n "${DJANGO_AUDIT_HWM_PATH:-}" ]]; then
    LATEST_HWM=$(find "$BACKUP_DIR" -maxdepth 1 -name 'audit_hwm-*.json' -type f 2>/dev/null | sort -r | head -n1)
    if [[ -z "$LATEST_HWM" ]]; then
        fail "Schritt 2b: Kein Audit-HWM-Backup (audit_hwm-*.json) in ${BACKUP_DIR} gefunden"
    elif dc cp "$LATEST_HWM" "web:${HWM_DRILL_CONTAINER_PATH}" >/dev/null 2>&1; then
        HWM_COPIED=1
        HWM_VERIFY_CODE="from core.services.audit.hwm import HighWaterMarkStore; HighWaterMarkStore.load('${HWM_DRILL_CONTAINER_PATH}')"
        if dc exec -T web python manage.py shell -c "$HWM_VERIFY_CODE" >/dev/null 2>&1; then
            ok "Schritt 2b: Audit-HWM-Signatur verifiziert (${LATEST_HWM##*/})"
        else
            fail "Schritt 2b: Audit-HWM-Signaturpruefung fehlgeschlagen — Datei manipuliert oder AUDIT_HASH_KEY falsch"
        fi
    else
        fail "Schritt 2b: Audit-HWM konnte nicht in den Drill-Container kopiert werden"
    fi
elif [[ "$DRILL_FORMAT" == "prod" ]]; then
    echo "INFO - Schritt 2b: HWM nicht konfiguriert — uebersprungen (DJANGO_AUDIT_HWM_PATH nicht gesetzt)"
fi

# Schritt 3: Stichproben pro Tabelle
EXPECTED_TABLES=("core_facility" "core_client" "core_event" "core_auditlog" "core_workitem")
SAMPLE_FAIL=0
for tbl in "${EXPECTED_TABLES[@]}"; do
    COUNT=$(psql_su -d "$DRILL_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
    if [[ -z "$COUNT" || "$COUNT" =~ [^0-9] ]]; then
        echo "       - ${tbl}: nicht abfragbar (${COUNT:-leer})"
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

# Schritt 3b: Vollstaendigkeits-Guard — Restore vs. Quelle/Live-DB (Refs #1336).
# Kritisch fuer den --prod-Zweig: dort wird via ``psql`` OHNE ON_ERROR_STOP
# eingespielt (der benigne MV-Refresh unter FORCE-RLS darf nicht failen) —
# dadurch bleibt der psql-Exit-Code aber auch bei ECHTEN Fehlern 0. Ein leerer
# oder mittendrin abgebrochener Restore waere sonst unsichtbar, denn Schritt 3
# laesst COUNT=0 als gueltige Zahl durch. Daher hier derselbe Mechanismus wie
# ``backup.sh --verify``: Zeilenzahl-Abgleich kritischer RLS-Tabellen zwischen
# Wegwerf-DB und Live-DB. Fail-closed-Signal: Live>0 aber Restore=0 (Dump
# RLS-leer bzw. Restore unvollstaendig). Dieser Nachweis MUSS vor
# mark_restore_verified (Schritt 7) stehen, sonst faerbt ein unvollstaendiger
# Restore den Compliance-Check faelschlich gruen. Der benigne MV-Refresh
# beruehrt die Nutzdaten NICHT — core_client/core_event/... bleiben gefuellt.
COMPLETENESS_TABLES=("core_client" "core_event" "core_workitem" "core_auditlog")
COMPLETENESS_FAIL=0
for tbl in "${COMPLETENESS_TABLES[@]}"; do
    RESTORED_COUNT=$(psql_su -d "$DRILL_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
    LIVE_COUNT=$(psql_su -d "$POSTGRES_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
    if [[ -z "$RESTORED_COUNT" || ! "$RESTORED_COUNT" =~ ^[0-9]+$ ]]; then
        echo "       - ${tbl}: im Restore nicht abfragbar (${RESTORED_COUNT:-leer})"
        COMPLETENESS_FAIL=1
    elif [[ "$LIVE_COUNT" =~ ^[0-9]+$ && "$LIVE_COUNT" -gt 0 && "$RESTORED_COUNT" -eq 0 ]]; then
        echo "       - ${tbl}: Live=${LIVE_COUNT} Zeilen, Restore=0 — leerer/teilweiser Restore"
        COMPLETENESS_FAIL=1
    else
        echo "       - ${tbl}: Restore=${RESTORED_COUNT} (Live=${LIVE_COUNT:-n/a})"
    fi
done
if [[ "$COMPLETENESS_FAIL" -eq 0 ]]; then
    ok "Schritt 3b: Vollstaendigkeits-Guard bestanden — Restore vs. Live konsistent"
else
    fail "Schritt 3b: Restore unvollstaendig (Live>0 aber Restore=0 bzw. nicht abfragbar) — NICHT als verifiziert markieren"
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
    # Raw UPDATE muss am Trigger scheitern. Output separat einfangen: psql endet
    # bei der erwarteten Exception mit rc!=0, was unter ``set -o pipefail`` eine
    # direkte ``… | grep``-Pipe verfaelschen wuerde (Pipeline-rc = psql-rc != 0,
    # obwohl grep matcht). Daher erst in eine Variable, dann grep.
    UPDATE_OUT=$(psql_su -d "$DRILL_DB" \
        -c "UPDATE core_auditlog SET action = 'logout' WHERE id = (SELECT id FROM core_auditlog LIMIT 1);" 2>&1 || true)
    if grep -qi "immutable" <<<"$UPDATE_OUT"; then
        ok "Schritt 5: auditlog_immutable-Trigger blockt UPDATE"
    else
        fail "Schritt 5: Trigger existiert, blockt UPDATE aber NICHT — kritisch"
    fi
else
    fail "Schritt 5: auditlog_immutable-Trigger fehlt in Restore"
fi

# Schritt 6: Passendes Medien-Archiv authentifizieren und auf dem Host
# probeweise entpacken (Refs #2195). Das Prod-Backup benennt beide Archive mit
# demselben Zeitstempel. Ein fehlendes Medien-Archiv ist zulaessig, weil der
# Restore-Pfad ausdruecklich DB-only-Backups unterstuetzt.
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    LATEST_MEDIA="${LATEST_DB%.sql.gz.enc}_media.tar.gz.enc"
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
                    ok "Schritt 6: Medien: ${MEDIA_FILE_COUNT} Dateien, ${MEDIA_BYTES} Bytes, HMAC ok"
                fi
            fi
        fi
    fi
else
    echo "INFO - Schritt 6: Kein Medien-Archiv im dev-Backup-Format — uebersprungen"
fi

# Schritt 7: Bei vollem Erfolg den Restore-Test im AuditLog dokumentieren.
# Laeuft im web-Container (DSGVO Art. 32 lit. c: Wiederherstellbarkeit belegt).
if [[ "$DRILL_FAILED" -eq 0 ]]; then
    NOTE="Restore-Drill $(date -u +%Y-%m-%dT%H:%MZ) gegen ${LATEST_DB##*/} -> ${DRILL_DB}"
    if dc exec -T web python manage.py mark_restore_verified --note "$NOTE" >/dev/null 2>&1; then
        ok "Schritt 7: Restore-Test im AuditLog dokumentiert (mark_restore_verified)"
    else
        fail "Schritt 7: mark_restore_verified fehlgeschlagen — Compliance-Check bleibt unknown"
    fi
fi

if [[ "$DRILL_FAILED" -ne 0 ]]; then
    echo
    echo "DRILL FEHLGESCHLAGEN — mindestens ein Schritt hat FAIL gemeldet."
    exit 1
fi

echo
echo "Drill erfolgreich abgeschlossen — Restore verifiziert und dokumentiert."
