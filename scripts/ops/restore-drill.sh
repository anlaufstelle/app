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
#          - Quelle:  $BACKUP_DIR/dump-*.pgc.enc  (Default /var/backups/anl)
#          - Format:  pg_dump --format=custom -> pg_restore (NICHT psql)
#          - Stack:   docker-compose.dev.yml --env-file .env.dev
#   prod : scripts/ops/backup.sh (der an Self-Hoster ausgelieferte Pfad)
#          - Quelle:  backups/daily/anlaufstelle_*.sql.gz.enc
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

# Kandidaten-Verzeichnisse je Format (fuer Auto-Erkennung + Default).
DEV_BACKUP_DIR="${DEV_BACKUP_DIR:-/var/backups/anl}"
PROD_BACKUP_DIR="${PROD_BACKUP_DIR:-$COMPOSE_DIR/backups/daily}"
if [[ -z "$DRILL_FORMAT" ]]; then
    _dev_hit=$(find "$DEV_BACKUP_DIR" -maxdepth 1 -name 'dump-*.pgc.enc' -type f 2>/dev/null | head -n1)
    _prod_hit=$(find "$PROD_BACKUP_DIR" -maxdepth 1 -name 'anlaufstelle_*.sql.gz.enc' -type f 2>/dev/null | head -n1)
    if [[ -n "$_dev_hit" && -n "$_prod_hit" ]]; then
        echo "FAIL - beide Backup-Formate gefunden (dev: $DEV_BACKUP_DIR, prod: $PROD_BACKUP_DIR)." >&2
        echo "       Format explizit waehlen: restore-drill.sh --dev | --prod" >&2
        exit 2
    elif [[ -n "$_prod_hit" ]]; then
        DRILL_FORMAT="prod"
    else
        DRILL_FORMAT="dev"
    fi
fi

# Format-spezifische Konfiguration: Env-Datei, Backup-Verzeichnis + -Glob,
# Compose-Stack. Alle per gleichnamiger Env-Variable ueberschreibbar.
if [[ "$DRILL_FORMAT" == "prod" ]]; then
    ENV_FILE="${ENV_FILE:-$COMPOSE_DIR/.env}"
    BACKUP_DIR="${BACKUP_DIR:-$PROD_BACKUP_DIR}"
    BACKUP_GLOB='anlaufstelle_*.sql.gz.enc'
    COMPOSE=(docker compose -f docker-compose.prod.yml)
else
    ENV_FILE="${ENV_FILE:-$COMPOSE_DIR/.env.dev}"
    BACKUP_DIR="${BACKUP_DIR:-$DEV_BACKUP_DIR}"
    BACKUP_GLOB='dump-*.pgc.enc'
    COMPOSE=(docker compose -f docker-compose.dev.yml --env-file .env.dev)
fi
echo "Restore-Drill — Format: ${DRILL_FORMAT}, Backups: ${BACKUP_DIR}"

# Env laden (nur Variablen) — liefert POSTGRES_*, BACKUP_ENCRYPTION_KEY.
if [[ -r "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

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

# Schritt 6: Cleanup laeuft via trap

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
