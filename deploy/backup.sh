#!/usr/bin/env bash
# Daily encrypted Postgres-Dump fuer dev.anlaufstelle.app (Refs #671).
#
# Laeuft am Server (cron oder make dev-backup). Erzeugt einen
# pg_dump --format=custom, leitet ihn durch openssl-AES-256-CBC und legt
# das Ergebnis in $BACKUP_DIR ab. Retention via mtime-Loeschung.
#
# Restore (Notfall):
#   openssl enc -d -aes-256-cbc -pbkdf2 \
#     -pass env:BACKUP_ENCRYPTION_KEY \
#     -in dump-YYYYMMDDTHHMMSS.pgc.enc |
#     docker compose -f docker-compose.dev.yml --env-file .env.dev exec -T db \
#     pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists

set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/anlaufstelle}"
ENV_FILE="$COMPOSE_DIR/.env.dev"

if [[ ! -r "$ENV_FILE" ]]; then
	echo "backup.sh: $ENV_FILE not readable" >&2
	exit 1
fi

# Konfig laden (nur Variablen, kein Skript-Code).
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${POSTGRES_USER:?POSTGRES_USER missing in $ENV_FILE}"
: "${POSTGRES_DB:?POSTGRES_DB missing in $ENV_FILE}"
: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY missing in $ENV_FILE}"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/anl}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/dump-$TIMESTAMP.pgc.enc"

mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"

cd "$COMPOSE_DIR"
docker compose -f docker-compose.dev.yml --env-file .env.dev \
	exec -T db \
	pg_dump -U "$POSTGRES_USER" -Fc -Z 6 "$POSTGRES_DB" |
	openssl enc -aes-256-cbc -pbkdf2 -salt \
		-pass env:BACKUP_ENCRYPTION_KEY \
		-out "$OUT"

chmod 0600 "$OUT"

# Retention: Dumps aelter als RETENTION_DAYS loeschen.
find "$BACKUP_DIR" -maxdepth 1 -name 'dump-*.pgc.enc' \
	-mtime "+$RETENTION_DAYS" -delete

# Output: Pfad + Groesse, damit cron/make das einfach loggen kann.
ls -lh "$OUT"
