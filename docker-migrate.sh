#!/bin/sh
# Refs #802 (C-34): One-Shot-Migrate-Job.
#
# Aufruf vor dem Rolling-Restart der Web-Replicas:
#
#   docker compose -f docker-compose.prod.yml run --rm \
#       --entrypoint=/app/docker-migrate.sh web
#
# Der Advisory-Lock verhindert, dass parallele Migrate-Jobs (z. B. zwei
# Operatoren gleichzeitig) sich gegenseitig ueberholen. Der Web-
# Container faehrt danach sauber hoch — sein Entrypoint laeuft nicht
# mehr migrate.
set -e

python <<'PY'
import os
import subprocess
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "anlaufstelle.settings.prod")
django.setup()
from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SELECT pg_advisory_lock(1)")
    try:
        result = subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"])
    finally:
        cursor.execute("SELECT pg_advisory_unlock(1)")
sys.exit(result.returncode)
PY
