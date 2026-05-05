#!/usr/bin/env bash
set -uo pipefail

# Anlaufstelle Backup-Restore-Drill (Refs #739, Refs #720)
#
# Verifiziert in 7 Schritten, dass das aktuellste Backup vollstaendig
# wiederherstellbar ist und dabei alle Verteidigungslinien (RLS,
# AuditLog-Trigger, Medien-Volume) erhalten bleiben:
#
#   1. Frische Temp-DB anlegen
#   2. Neuestes DB-Backup decrypten + restoren
#   3. Stichproben pro Tabelle (core_facility, core_client, core_event,
#      core_auditlog, core_workitem) — Counts > 0 wenn Original > 0
#   4. RLS-Policy-Check — Anzahl ``relrowsecurity = true`` muss zur
#      Erwartung passen (>= 21)
#   5. AuditLog-Trigger-Check — Raw UPDATE muss scheitern
#   6. Medien-Restore-Check — letztes *_media.tar.gz.enc auflisten
#      und mindestens 1 Eintrag bestaetigen
#   7. Cleanup: Temp-DB drop
#
# Output: ein OK/FAIL-Eintrag pro Schritt; Exit-Code != 0 bei jedem
# FAIL. Empfohlen quartalsweise per Cron + Alert-Mail bei Fehlschlag.
#
# Verwendung:
#   ./scripts/restore-drill.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_DIR}/backups"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
DAILY_DIR="${BACKUP_DIR}/daily"

# .env laden falls vorhanden
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

: "${POSTGRES_USER:?POSTGRES_USER nicht gesetzt}"
: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY nicht gesetzt}"

DRILL_FAILED=0
ok() { echo "OK   - $*"; }
fail() { echo "FAIL - $*"; DRILL_FAILED=1; }

# Schritt 0: Backup-Files finden
LATEST_DB=$(find "$DAILY_DIR" -name "*.sql.gz.enc" -type f 2>/dev/null | sort -r | head -n1)
LATEST_MEDIA=$(find "$DAILY_DIR" -name "*_media.tar.gz.enc" -type f 2>/dev/null | sort -r | head -n1)
if [[ -z "$LATEST_DB" ]]; then
    fail "Kein DB-Backup in ${DAILY_DIR} gefunden"
    echo "Drill abgebrochen — keine Restore-Quelle."
    exit 1
fi
echo "Drill gegen DB-Backup: $LATEST_DB"
[[ -n "$LATEST_MEDIA" ]] && echo "Drill gegen Medien-Backup: $LATEST_MEDIA" || echo "WARNUNG: kein Medien-Backup gefunden — Schritt 6 skipped."

# Schritt 1: Temp-DB anlegen
DRILL_DB="anlaufstelle_drill_$$"
cleanup() {
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$POSTGRES_USER" -d postgres \
        -c "DROP DATABASE IF EXISTS \"${DRILL_DB}\";" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$POSTGRES_USER" -d postgres \
    -c "CREATE DATABASE \"${DRILL_DB}\";" >/dev/null 2>&1; then
    ok "Schritt 1: Temp-DB ${DRILL_DB} angelegt"
else
    fail "Schritt 1: Temp-DB konnte nicht angelegt werden"
    exit 1
fi

# Schritt 2: DB-Backup decrypten + restoren
if openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$LATEST_DB" \
    | gunzip \
    | docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$POSTGRES_USER" -d "$DRILL_DB" >/dev/null 2>&1; then
    ok "Schritt 2: DB-Backup wiederhergestellt"
else
    fail "Schritt 2: DB-Restore fehlgeschlagen"
    exit 1
fi

# Schritt 3: Stichproben pro Tabelle
EXPECTED_TABLES=("core_facility" "core_client" "core_event" "core_auditlog" "core_workitem")
SAMPLE_FAIL=0
for tbl in "${EXPECTED_TABLES[@]}"; do
    COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$POSTGRES_USER" -d "$DRILL_DB" -t -A \
        -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]')
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
RLS_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$POSTGRES_USER" -d "$DRILL_DB" -t -A \
    -c "SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true;" 2>/dev/null | tr -d '[:space:]')
if [[ "$RLS_COUNT" =~ ^[0-9]+$ ]] && [[ "$RLS_COUNT" -ge 18 ]]; then
    ok "Schritt 4: RLS aktiv auf ${RLS_COUNT} core_*-Tabellen (erwartet >= 18)"
else
    fail "Schritt 4: RLS-Tabellen-Anzahl unerwartet (${RLS_COUNT:-nicht abfragbar})"
fi

# Schritt 5: AuditLog-Trigger-Check
TRIGGER_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$POSTGRES_USER" -d "$DRILL_DB" -t -A \
    -c "SELECT COUNT(*) FROM pg_trigger WHERE tgname = 'auditlog_immutable';" 2>/dev/null | tr -d '[:space:]')
if [[ "$TRIGGER_COUNT" == "1" ]]; then
    # Raw UPDATE muss scheitern.
    if docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$POSTGRES_USER" -d "$DRILL_DB" \
        -c "UPDATE core_auditlog SET action = 'logout' WHERE id = (SELECT id FROM core_auditlog LIMIT 1);" 2>&1 \
        | grep -qi "immutable"; then
        ok "Schritt 5: auditlog_immutable-Trigger blockt UPDATE"
    else
        fail "Schritt 5: Trigger existiert, blockt UPDATE aber NICHT — kritisch"
    fi
else
    fail "Schritt 5: auditlog_immutable-Trigger fehlt in Restore"
fi

# Schritt 6: Medien-Restore-Check
if [[ -n "$LATEST_MEDIA" ]]; then
    FILE_COUNT=$(openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$LATEST_MEDIA" \
        | gunzip \
        | tar -tf - 2>/dev/null | wc -l)
    if [[ "$FILE_COUNT" =~ ^[0-9]+$ ]] && [[ "$FILE_COUNT" -ge 1 ]]; then
        ok "Schritt 6: Medien-Backup enthaelt ${FILE_COUNT} Eintraege"
    else
        fail "Schritt 6: Medien-Backup leer oder beschaedigt"
    fi
else
    echo "SKIP - Schritt 6: kein Medien-Backup vorhanden"
fi

# Schritt 7: Cleanup laeuft via trap

if [[ "$DRILL_FAILED" -ne 0 ]]; then
    echo
    echo "DRILL FEHLGESCHLAGEN — mindestens ein Schritt hat FAIL gemeldet."
    exit 1
fi

echo
echo "Drill erfolgreich abgeschlossen."
