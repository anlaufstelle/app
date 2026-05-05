#!/bin/sh
# Refs #802 (C-34): Web-Container ist nur noch Web-Server.
# Migrationen laufen vor dem Rolling-Restart als separater One-Shot-Job
# (siehe ``docker-migrate.sh`` und ``docs/ops-runbook.md`` § 1.2).
# Lange RunPython-Schritte blockieren so keine Worker mehr und parallele
# Web-Replicas warten nicht beim Start auf den Migrate-Lock.
set -e

python manage.py collectstatic --noinput

exec gunicorn anlaufstelle.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-30}"
