#!/usr/bin/env bash
# Deploy dev.anlaufstelle.app (Refs #671).
#
# Laeuft auf dem Operator-Laptop (oder dieser Sandbox). Synct die
# Compose-/Caddy-/Deploy-Files zum Server und triggert dann pull, migrate,
# up. Das Image selbst kommt von ghcr.io (gebaut von dev-image.yml).
#
# Erwartet:
#   - DEV_HOST in der Umgebung (default: anlaufstelle@dev.anlaufstelle.app)
#   - SSH-Key fuer den Service-User
#   - .env.dev liegt bereits unter /opt/anlaufstelle/.env.dev am Server.

set -euo pipefail

DEV_HOST="${DEV_HOST:-anlaufstelle@dev.anlaufstelle.app}"
REMOTE_DIR=/opt/anlaufstelle
COMPOSE="docker compose -f docker-compose.dev.yml --env-file .env.dev"

log() { printf '\033[1;34m[deploy-dev]\033[0m %s\n' "$*"; }

log "sync compose + caddy + deploy/ -> $DEV_HOST:$REMOTE_DIR"
scp -q docker-compose.dev.yml Caddyfile.dev "$DEV_HOST:$REMOTE_DIR/"
rsync -a --delete deploy/ "$DEV_HOST:$REMOTE_DIR/deploy/"

log "pull, migrate, up"
ssh "$DEV_HOST" bash -se <<'EOF'
set -euo pipefail
cd /opt/anlaufstelle
COMPOSE='docker compose -f docker-compose.dev.yml --env-file .env.dev'
# .env.dev laden, damit POSTGRES_ADMIN_USER/PASSWORD verfuegbar sind.
set -a; . ./.env.dev; set +a
$COMPOSE pull
# Migrate als One-Shot-Job mit pg_advisory_lock (docker-migrate.sh, Refs #802).
# Connection als Admin-User (BYPASSRLS), damit Migrationen mit RunPython-
# Default-Daten in RLS-geschuetzte Tabellen schreiben koennen (Refs #863).
$COMPOSE run --rm \
    --entrypoint=/app/docker-migrate.sh \
    -e POSTGRES_USER="$POSTGRES_ADMIN_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_ADMIN_PASSWORD" \
    web
$COMPOSE up -d --remove-orphans
$COMPOSE ps
EOF

log "verify /health/"
curl -fsS "https://${DEV_HOST##*@}/health/" | head -c 200
echo
log "done"
