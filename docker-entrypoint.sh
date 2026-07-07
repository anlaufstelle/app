#!/bin/sh
# Refs #802 (C-34): Web-Container ist nur noch Web-Server.
# Migrationen laufen vor dem Rolling-Restart als separater One-Shot-Job
# (siehe ``docker-migrate.sh`` und ``docs/ops-runbook.md`` § 1.2).
# Lange RunPython-Schritte blockieren so keine Worker mehr und parallele
# Web-Replicas warten nicht beim Start auf den Migrate-Lock.
set -e

python manage.py collectstatic --noinput

# Security N6: Deploy-Checks fail-closed beim Container-Start — faengt
# Platzhalter-Secrets/unsichere Prod-Settings, bevor gunicorn startet.
python manage.py check --deploy

# Refs #1283: GUNICORN_TIMEOUT MUSS größer als CLAMAV_TIMEOUT (settings/base.py)
# sein. Der ClamAV-Upload-Scan läuft synchron im Worker; ist das Worker-Timeout
# <= dem Scan-Timeout, killt Gunicorn den Worker bei langsamem clamd zuerst
# (→ 500) statt den fail-closed "Scanner nicht erreichbar"-Pfad greifen zu lassen.
exec gunicorn anlaufstelle.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-30}"
