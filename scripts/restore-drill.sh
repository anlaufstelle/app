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
# WICHTIG (Refs #981): Dieses Skript ist auf das **deploy/backup.sh**-Format
# ausgerichtet, das auf dev.anlaufstelle.app tatsaechlich laeuft:
#   - Quelle:  $BACKUP_DIR/dump-*.pgc.enc  (Default /var/backups/anl)
#   - Format:  pg_dump --format=custom -> braucht pg_restore (NICHT psql)
#   - Crypto:  openssl AES-256-CBC -pbkdf2
#   - Stack:   docker-compose.dev.yml --env-file .env.dev
#   - User:    POSTGRES_ADMIN_USER (BYPASSRLS) — sonst scheitert der Restore
#              an FORCE ROW LEVEL SECURITY.
# Das alte scripts/backup.sh + scripts/restore.sh-Schema (backups/daily/
# *.sql.gz.enc, plain SQL) ist davon unberuehrt.
#
# Die Dump-Dateien sind 0600 und gehoeren root — daher als root ausfuehren:
#   sudo bash /opt/anlaufstelle/scripts/restore-drill.sh
#
# Empfohlen quartalsweise (per Timer/Cron) + Alert bei Exit-Code != 0.

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

: "${POSTGRES_DB:?POSTGRES_DB nicht gesetzt (aus $ENV_FILE)}"
: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY nicht gesetzt (aus $ENV_FILE)}"

# Restore als BYPASSRLS-Admin (gleicher User-Typ wie der Dump). Fallback auf
# den App-User fuer aeltere Stages ohne 2-User-Modell.
ADMIN_USER="${POSTGRES_ADMIN_USER:-${POSTGRES_USER:?POSTGRES_USER nicht gesetzt}}"
ADMIN_PASS="${POSTGRES_ADMIN_PASSWORD:-${POSTGRES_PASSWORD:?POSTGRES_PASSWORD nicht gesetzt}}"

DRILL_FAILED=0
ok() { echo "OK   - $*"; }
fail() {
    echo "FAIL - $*"
    DRILL_FAILED=1
}

dc() { "${COMPOSE[@]}" "$@"; }
psql_admin() { dc exec -T -e PGPASSWORD="$ADMIN_PASS" db psql -U "$ADMIN_USER" "$@"; }

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
    psql_admin -d postgres -c "DROP DATABASE IF EXISTS \"${DRILL_DB}\";" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if psql_admin -d postgres -c "CREATE DATABASE \"${DRILL_DB}\";" >/dev/null 2>&1; then
    ok "Schritt 1: Wegwerf-DB ${DRILL_DB} angelegt"
else
    fail "Schritt 1: Wegwerf-DB konnte nicht angelegt werden"
    exit 1
fi

# Schritt 2: decrypten + pg_restore (custom format) in die Wegwerf-DB
if openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$LATEST_DB" \
    | dc exec -T -e PGPASSWORD="$ADMIN_PASS" db \
        pg_restore -U "$ADMIN_USER" -d "$DRILL_DB" >/dev/null 2>&1; then
    ok "Schritt 2: DB-Backup wiederhergestellt (pg_restore, custom format)"
else
    fail "Schritt 2: DB-Restore fehlgeschlagen (openssl/pg_restore)"
    exit 1
fi

# Schritt 3: Stichproben pro Tabelle
EXPECTED_TABLES=("core_facility" "core_client" "core_event" "core_auditlog" "core_workitem")
SAMPLE_FAIL=0
for tbl in "${EXPECTED_TABLES[@]}"; do
    COUNT=$(psql_admin -d "$DRILL_DB" -t -A -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
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
RLS_COUNT=$(psql_admin -d "$DRILL_DB" -t -A \
    -c "SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true;" 2>/dev/null | tr -d '[:space:]')
if [[ "$RLS_COUNT" =~ ^[0-9]+$ ]] && [[ "$RLS_COUNT" -ge 18 ]]; then
    ok "Schritt 4: RLS aktiv auf ${RLS_COUNT} core_*-Tabellen (erwartet >= 18)"
else
    fail "Schritt 4: RLS-Tabellen-Anzahl unerwartet (${RLS_COUNT:-nicht abfragbar})"
fi

# Schritt 5: AuditLog-Immutability-Trigger-Check
TRIGGER_COUNT=$(psql_admin -d "$DRILL_DB" -t -A \
    -c "SELECT COUNT(*) FROM pg_trigger WHERE tgname = 'auditlog_immutable';" 2>/dev/null | tr -d '[:space:]')
if [[ "$TRIGGER_COUNT" == "1" ]]; then
    if psql_admin -d "$DRILL_DB" \
        -c "UPDATE core_auditlog SET action = 'logout' WHERE id = (SELECT id FROM core_auditlog LIMIT 1);" 2>&1 \
        | grep -qi "immutable"; then
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
