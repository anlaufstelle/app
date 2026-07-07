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
# WICHTIG (Refs #981): Dieses Skript ist auf das **dev-ops/deploy/backup.sh**-Format
# ausgerichtet, das auf dev.anlaufstelle.app tatsaechlich laeuft:
#   - Quelle:  $BACKUP_DIR/dump-*.pgc.enc  (Default /var/backups/anl)
#   - Format:  pg_dump --format=custom -> braucht pg_restore (NICHT psql)
#   - Crypto:  openssl AES-256-CBC -pbkdf2
#   - Stack:   docker-compose.dev.yml --env-file .env.dev
#   - User:    Postgres-Superuser (Default ``postgres``, ueber Local-Socket-
#              Trust im db-Container). Noetig fuer CREATE DATABASE; bypassed
#              zugleich RLS/FORCE-RLS beim pg_restore. (``anlaufstelle_admin``
#              ist BYPASSRLS, hat aber kein CREATEDB.)
# Das alte scripts/ops/backup.sh + scripts/ops/restore.sh-Schema (backups/daily/
# *.sql.gz.enc, plain SQL) ist davon unberuehrt.
#
# Die Dump-Dateien sind 0600 und gehoeren root — daher als root ausfuehren:
#   sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh
#
# Empfohlen quartalsweise (per Timer/Cron) + Alert bei Exit-Code != 0.

# Verzeichnis dieses Skripts VOR dem cd bestimmen, damit das gemeinsame
# Krypto-/Guard-Modul relativ zum Skript gefunden wird (Refs #1441).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# N6-Guard (Refs #1441): backup_require_real_key weist einen change-me*-
# BACKUP_ENCRYPTION_KEY fail-closed ab — geteilt mit backup.sh / restore.sh.
# shellcheck source=scripts/ops/_backup_common.sh
source "${SCRIPT_DIR}/_backup_common.sh"

COMPOSE_DIR="${COMPOSE_DIR:-/opt/anlaufstelle}"
ENV_FILE="${ENV_FILE:-$COMPOSE_DIR/.env.dev}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/anl}"
COMPOSE=(docker compose -f docker-compose.dev.yml --env-file .env.dev)

cd "$COMPOSE_DIR" || {
    echo "FAIL - COMPOSE_DIR $COMPOSE_DIR nicht erreichbar"
    exit 1
}

# .env.dev laden (nur Variablen) — liefert POSTGRES_*, BACKUP_ENCRYPTION_KEY.
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

# Der Drill braucht CREATE DATABASE — das kann auf dem 3-Rollen-Modell nur der
# Postgres-Superuser (``anlaufstelle_admin`` ist BYPASSRLS, aber NICHT CREATEDB;
# ``anlaufstelle`` ist der RLS-gebundene App-User). Der Superuser bypassed
# zugleich RLS, sodass pg_restore die geschuetzten Tabellen fuellen kann.
# Verbindung ueber den lokalen Socket im db-Container (pg_hba: local trust) —
# daher kein Passwort noetig.
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
LATEST_DB=$(find "$BACKUP_DIR" -maxdepth 1 -name 'dump-*.pgc.enc' -type f 2>/dev/null | sort -r | head -n1)
if [[ -z "$LATEST_DB" ]]; then
    fail "Kein DB-Backup (dump-*.pgc.enc) in ${BACKUP_DIR} gefunden"
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

# Schritt 2: decrypten + pg_restore (custom format) in die Wegwerf-DB.
#
# Bekannte benigne Ausnahme: das Dump enthaelt ``REFRESH MATERIALIZED VIEW
# core_statistics_event_flat``. Beim Restore scheitert dieser Refresh an
# FORCE ROW LEVEL SECURITY auf core_event (RLS gilt dort selbst fuer den Owner,
# und der Refresh laeuft nicht mit BYPASSRLS) -> pg_restore endet mit rc=1
# ("errors ignored on restore: 1"). Das betrifft nur den Statistik-Cache, nicht
# die Nutzdaten — der mv-refresh-Timer baut ihn stuendlich neu. Solche reinen
# MV-Refresh-Fehler tolerieren wir; jeder andere/zusaetzliche Fehler bleibt FAIL.
RESTORE_ERR="$(mktemp)"
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
