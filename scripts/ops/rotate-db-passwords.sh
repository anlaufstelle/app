#!/usr/bin/env bash
# Atomare Rotation der drei PostgreSQL-Rollen-Passwoerter (Refs #1964).
#
# Default: Produktions-Stack im Repo-Root. Fuer andere Stacks explizit aufrufen:
#   COMPOSE_FILE=<compose-file> ENV_FILE=<env-file> \
#     ./scripts/ops/rotate-db-passwords.sh
#
# Das Skript gibt zu keinem Zeitpunkt Passwoerter aus. Es stoppt die dauerhaften
# DB-Consumer, rotiert alle Rollen in EINER DB-Transaktion, tauscht die Env-Datei
# atomar, recreated PostgreSQL (damit auch dessen Container-Env aktuell ist),
# faehrt den echten Migrate-/Ownership-Pfad und prueft positive sowie negative
# Logins. Jeder Fehler fuehrt zu einem best-effort Rollback von DB und Env-Datei.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-180}"

[[ "$COMPOSE_FILE" = /* ]] || COMPOSE_FILE="${PROJECT_DIR}/${COMPOSE_FILE}"
[[ "$ENV_FILE" = /* ]] || ENV_FILE="${PROJECT_DIR}/${ENV_FILE}"

info() { printf '[db-password-rotation] %s\n' "$*"; }
warn() { printf '[db-password-rotation] WARN: %s\n' "$*" >&2; }
fail() {
    printf '[db-password-rotation] FEHLER: %s\n' "$*" >&2
    return 1
}

for command in docker openssl mktemp stat; do
    command -v "$command" >/dev/null 2>&1 || {
        printf '[db-password-rotation] FEHLER: %s fehlt.\n' "$command" >&2
        exit 1
    }
done
[[ -f "$COMPOSE_FILE" ]] || {
    printf '[db-password-rotation] FEHLER: Compose-Datei fehlt: %s\n' "$COMPOSE_FILE" >&2
    exit 1
}
[[ -f "$ENV_FILE" ]] || {
    printf '[db-password-rotation] FEHLER: Env-Datei fehlt: %s\n' "$ENV_FILE" >&2
    exit 1
}

env_mode="$(stat -c '%a' "$ENV_FILE")"
if ((8#$env_mode & 077)); then
    printf '[db-password-rotation] FEHLER: %s muss mode 600 (oder strenger) haben; aktuell %s.\n' \
        "$ENV_FILE" "$env_mode" >&2
    exit 1
fi

read_env() {
    local name="$1" line value count=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" == "${name}="* ]] || continue
        value="${line#*=}"
        count=$((count + 1))
    done < "$ENV_FILE"
    [[ "$count" -eq 1 ]] || fail "${name} muss genau einmal aktiv in ${ENV_FILE} stehen (gefunden: ${count})."
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
        value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
        value="${value:1:${#value}-2}"
    fi
    [[ -n "$value" ]] || fail "${name} ist leer."
    printf '%s' "$value"
}

POSTGRES_DB="$(read_env POSTGRES_DB)"
APP_USER="$(read_env POSTGRES_USER)"
OLD_APP_PASSWORD="$(read_env POSTGRES_PASSWORD)"
ADMIN_USER="$(read_env POSTGRES_ADMIN_USER)"
OLD_ADMIN_PASSWORD="$(read_env POSTGRES_ADMIN_PASSWORD)"
OLD_BOOTSTRAP_PASSWORD="$(read_env POSTGRES_BOOTSTRAP_PASSWORD)"

for role in "$APP_USER" "$ADMIN_USER"; do
    [[ "$role" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
        printf '[db-password-rotation] FEHLER: ungueltiger PostgreSQL-Rollenname in %s.\n' "$ENV_FILE" >&2
        exit 1
    }
done

# Every recreate needs the same fixed file list as the admin guide (base file,
# then compose.legal.yml and compose.override.yml if they sit next to the base
# file). Once a -f is given, Compose no longer loads compose.override.yml on its
# own; without compose.legal.yml the recreated web container loses the imprint/
# privacy mount and variables.
COMPOSE_FILES=("$COMPOSE_FILE")
compose_extra_dir="$(dirname "$COMPOSE_FILE")"
for compose_extra_file in "${compose_extra_dir}/compose.legal.yml" "${compose_extra_dir}/compose.override.yml"; do
    [[ -f "$compose_extra_file" ]] && COMPOSE_FILES+=("$compose_extra_file")
done
compose_flags=()
for compose_file in "${COMPOSE_FILES[@]}"; do
    compose_flags+=(-f "$compose_file")
done
info "Compose files: ${COMPOSE_FILES[*]}"
COMPOSE=(docker compose --profile maintenance "${compose_flags[@]}" --env-file "$ENV_FILE")
services="$("${COMPOSE[@]}" config --services)"
for required in db web maintenance; do
    grep -qx "$required" <<< "$services" || {
        printf '[db-password-rotation] FEHLER: Compose-Service %s fehlt in %s.\n' "$required" "$COMPOSE_FILE" >&2
        exit 1
    }
done
CONSUMERS=(web)
grep -qx cron <<< "$services" && CONSUMERS+=(cron)

LOCK_DIR="${ENV_FILE}.rotation.lock"
mkdir "$LOCK_DIR" 2>/dev/null || {
    printf '[db-password-rotation] FEHLER: Rotation laeuft bereits (%s).\n' "$LOCK_DIR" >&2
    exit 1
}

STAGED_ENV=""
ROLLBACK_ENV=""
early_cleanup() {
    rm -f "$STAGED_ENV" "$ROLLBACK_ENV"
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap early_cleanup EXIT
STAGED_ENV="$(mktemp "${ENV_FILE}.rotation.new.XXXXXX")"
ROLLBACK_ENV="$(mktemp "${ENV_FILE}.rotation.old.XXXXXX")"
cp -p "$ENV_FILE" "$ROLLBACK_ENV"

NEW_APP_PASSWORD="$(openssl rand -base64 48 | tr -d '\n')"
NEW_ADMIN_PASSWORD="$(openssl rand -base64 48 | tr -d '\n')"
NEW_BOOTSTRAP_PASSWORD="$(openssl rand -base64 48 | tr -d '\n')"
for secret in "$NEW_APP_PASSWORD" "$NEW_ADMIN_PASSWORD" "$NEW_BOOTSTRAP_PASSWORD"; do
    [[ "${#secret}" -ge 32 && "$secret" != change-me* && "$secret" != *"'"* ]] || {
        printf '[db-password-rotation] FEHLER: Generator lieferte keinen sicheren DB-Wert.\n' >&2
        exit 1
    }
done

replace_count_app=0
replace_count_admin=0
replace_count_bootstrap=0
: > "$STAGED_ENV"
while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
        POSTGRES_PASSWORD=*)
            printf 'POSTGRES_PASSWORD=%s\n' "$NEW_APP_PASSWORD" >> "$STAGED_ENV"
            replace_count_app=$((replace_count_app + 1))
            ;;
        POSTGRES_ADMIN_PASSWORD=*)
            printf 'POSTGRES_ADMIN_PASSWORD=%s\n' "$NEW_ADMIN_PASSWORD" >> "$STAGED_ENV"
            replace_count_admin=$((replace_count_admin + 1))
            ;;
        POSTGRES_BOOTSTRAP_PASSWORD=*)
            printf 'POSTGRES_BOOTSTRAP_PASSWORD=%s\n' "$NEW_BOOTSTRAP_PASSWORD" >> "$STAGED_ENV"
            replace_count_bootstrap=$((replace_count_bootstrap + 1))
            ;;
        *) printf '%s\n' "$line" >> "$STAGED_ENV" ;;
    esac
done < "$ENV_FILE"
[[ "$replace_count_app" -eq 1 && "$replace_count_admin" -eq 1 && "$replace_count_bootstrap" -eq 1 ]] || {
    printf '[db-password-rotation] FEHLER: Passwortvariablen sind nicht eindeutig in %s.\n' "$ENV_FILE" >&2
    exit 1
}
chmod --reference="$ENV_FILE" "$STAGED_ENV"
chown --reference="$ENV_FILE" "$STAGED_ENV" 2>/dev/null || true

ROTATED=0
ENV_SWAPPED=0
DB_RECREATED=0
CONSUMERS_STOPPED=0
SUCCESS=0

sql_escape() {
    local value="$1"
    printf '%s' "${value//\'/\'\'}"
}

apply_passwords() {
    local app_password admin_password bootstrap_password
    app_password="$(sql_escape "$1")"
    admin_password="$(sql_escape "$2")"
    bootstrap_password="$(sql_escape "$3")"
    "${COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d "$POSTGRES_DB" <<SQL
BEGIN;
ALTER ROLE "$APP_USER" PASSWORD '$app_password';
ALTER ROLE "$ADMIN_USER" PASSWORD '$admin_password';
ALTER ROLE "postgres" PASSWORD '$bootstrap_password';
COMMIT;
SQL
}

wait_healthy() {
    local service="$1" waited=0 cid health
    while ((waited < WAIT_TIMEOUT)); do
        cid="$("${COMPOSE[@]}" ps -q "$service")"
        if [[ -n "$cid" ]]; then
            health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || true)"
            [[ "$health" == healthy || "$health" == none ]] && return 0
            [[ "$health" == unhealthy ]] && fail "Service ${service} ist unhealthy."
        fi
        sleep 3
        waited=$((waited + 3))
    done
    fail "Timeout: Service ${service} wurde nicht healthy."
}

login_succeeds() {
    local password="$1" user="$2"
    printf '%s\n' "$password" | "${COMPOSE[@]}" exec -T db sh -ceu '
        IFS= read -r PGPASSWORD
        export PGPASSWORD
        exec psql -h db -U "$1" -d "$2" -tAc "SELECT 1"
    ' sh "$user" "$POSTGRES_DB" >/dev/null 2>&1
}

old_login_is_rejected() {
    local password="$1" user="$2" output status=0
    output="$(
        printf '%s\n' "$password" | "${COMPOSE[@]}" exec -T db sh -ceu '
            IFS= read -r PGPASSWORD
            export PGPASSWORD
            exec psql -h db -U "$1" -d "$2" -tAc "SELECT 1"
        ' sh "$user" "$POSTGRES_DB" 2>&1
    )" || status=$?
    [[ "$status" -ne 0 ]] || return 1
    grep -qi "password authentication failed" <<< "$output"
}

rollback() {
    local exit_code="$1"
    local rollback_failed=0
    trap - EXIT
    set +e
    if [[ "$SUCCESS" -ne 1 ]]; then
        warn "Rotation fehlgeschlagen — Rollback von Env, Rollen und Consumern startet."
        if [[ "$ENV_SWAPPED" -eq 1 && -f "$ROLLBACK_ENV" ]]; then
            mv -f "$ROLLBACK_ENV" "$ENV_FILE"
            ENV_SWAPPED=0
        fi
        if [[ "$ROTATED" -eq 1 ]]; then
            apply_passwords "$OLD_APP_PASSWORD" "$OLD_ADMIN_PASSWORD" "$OLD_BOOTSTRAP_PASSWORD" \
                >/dev/null 2>&1 || rollback_failed=1
        fi
        if [[ "$DB_RECREATED" -eq 1 ]]; then
            "${COMPOSE[@]}" up -d --pull never --force-recreate db >/dev/null 2>&1 || rollback_failed=1
            wait_healthy db >/dev/null 2>&1 || rollback_failed=1
        fi
        if [[ "$CONSUMERS_STOPPED" -eq 1 ]]; then
            "${COMPOSE[@]}" up -d --pull never "${CONSUMERS[@]}" >/dev/null 2>&1 || rollback_failed=1
        fi
        if [[ "$rollback_failed" -eq 0 ]]; then
            warn "Rollback abgeschlossen; Ursache oberhalb pruefen."
        else
            warn "Rollback war unvollstaendig — DB/Env vor einem Neustart manuell abgleichen."
        fi
    fi
    rm -f "$STAGED_ENV" "$ROLLBACK_ENV"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    unset OLD_APP_PASSWORD OLD_ADMIN_PASSWORD OLD_BOOTSTRAP_PASSWORD
    unset NEW_APP_PASSWORD NEW_ADMIN_PASSWORD NEW_BOOTSTRAP_PASSWORD
    exit "$exit_code"
}
trap 'rollback $?' EXIT

info "Preflight gruen; dauerhafte DB-Consumer werden gestoppt (${CONSUMERS[*]})."
CONSUMERS_STOPPED=1
"${COMPOSE[@]}" stop "${CONSUMERS[@]}"

info "Alle drei Rollen werden in einer DB-Transaktion rotiert."
apply_passwords "$NEW_APP_PASSWORD" "$NEW_ADMIN_PASSWORD" "$NEW_BOOTSTRAP_PASSWORD"
ROTATED=1

login_succeeds "$NEW_APP_PASSWORD" "$APP_USER" || fail "Neuer Login fuer Rolle ${APP_USER} wurde abgewiesen."
login_succeeds "$NEW_ADMIN_PASSWORD" "$ADMIN_USER" || fail "Neuer Login fuer Rolle ${ADMIN_USER} wurde abgewiesen."
login_succeeds "$NEW_BOOTSTRAP_PASSWORD" postgres || fail "Neuer Login fuer Rolle postgres wurde abgewiesen."

mv -f "$STAGED_ENV" "$ENV_FILE"
ENV_SWAPPED=1
info "Env-Datei atomar aktualisiert; PostgreSQL wird mit der neuen Bootstrap-Env recreated."
DB_RECREATED=1
"${COMPOSE[@]}" up -d --pull never --force-recreate db
wait_healthy db

# Recreate darf weder die persistierte Rollen-Aenderung noch die Erreichbarkeit
# des TCP-/Passwortpfads verdecken; alle drei neuen Logins erneut beweisen.
login_succeeds "$NEW_APP_PASSWORD" "$APP_USER" || fail "Neuer Login fuer Rolle ${APP_USER} scheitert nach DB-Recreate."
login_succeeds "$NEW_ADMIN_PASSWORD" "$ADMIN_USER" || fail "Neuer Login fuer Rolle ${ADMIN_USER} scheitert nach DB-Recreate."
login_succeeds "$NEW_BOOTSTRAP_PASSWORD" postgres || fail "Neuer Login fuer Rolle postgres scheitert nach DB-Recreate."

info "Migrate-/Ownership-Gate laeuft als Maintenance-Rolle."
"${COMPOSE[@]}" run --rm -T --pull never --entrypoint=/app/docker-migrate.sh maintenance

info "Dauerhafte Consumer starten mit den neuen Zugangsdaten."
"${COMPOSE[@]}" up -d --pull never "${CONSUMERS[@]}"
wait_healthy web

old_login_is_rejected "$OLD_APP_PASSWORD" "$APP_USER" || \
    fail "Negativtest fehlgeschlagen/uneindeutig: alter Login fuer Rolle ${APP_USER}."
info "Negativtest gruen: alter Login fuer Rolle ${APP_USER} wird mit Auth-Fehler abgewiesen."
old_login_is_rejected "$OLD_ADMIN_PASSWORD" "$ADMIN_USER" || \
    fail "Negativtest fehlgeschlagen/uneindeutig: alter Login fuer Rolle ${ADMIN_USER}."
info "Negativtest gruen: alter Login fuer Rolle ${ADMIN_USER} wird mit Auth-Fehler abgewiesen."
old_login_is_rejected "$OLD_BOOTSTRAP_PASSWORD" postgres || \
    fail "Negativtest fehlgeschlagen/uneindeutig: alter Login fuer Rolle postgres."
info "Negativtest gruen: alter Login fuer Rolle postgres wird mit Auth-Fehler abgewiesen."

info "Rotation abgeschlossen. Backup-/Restore-Jobs lesen beim naechsten Lauf die neue Env-Datei."
info "Jetzt Passwort-Escrow und Notfallunterlagen aktualisieren (Ops-Runbook DB-Rotation)."
SUCCESS=1
rm -f "$ROLLBACK_ENV"
exit 0
