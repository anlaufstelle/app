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
#
# Refs #1002: Vor der Migration verifiziert ``check_db_roles`` die
# Postgres-Rollen-Topologie (App-Rolle NOSUPERUSER/NOBYPASSRLS,
# Admin-Rolle BYPASSRLS) als Fail-Fast-Deploy-Gate gegen die
# RLS-Bypass-Luecke aus #902. Da dieser One-Shot-Job genau einmal pro
# Deploy vor dem Rolling-Restart laeuft, ist er der natuerliche Ort fuer
# diese Deploy-weite Invariante — der Web-Entrypoint bleibt schlank
# (Refs #802). Exit 1 (falsches Rollenprofil) bricht den Deploy ab;
# Exit 2 (Konfig unvollstaendig) warnt nur und laesst die Migration zu.
set -e

python <<'PY'
import os
import subprocess
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "anlaufstelle.settings.prod")
django.setup()
from django.db import connection

# Refs #1002: Rollen-Gate VOR dem Lock — read-only pg_roles-Query, kein
# Lock noetig. Schlaegt fail-fast fehl, bevor neue Web-Replicas live gehen.
roles = subprocess.run([sys.executable, "manage.py", "check_db_roles"])
if roles.returncode == 1:
    sys.stderr.write(
        "FATAL: check_db_roles meldet ein falsches DB-Rollenprofil "
        "(RLS-Bypass-Risiko, Refs #902/#1002) — Deploy abgebrochen.\n"
    )
    sys.exit(1)
if roles.returncode == 2:
    sys.stderr.write(
        "WARN: check_db_roles meldet eine unvollstaendige Konfiguration "
        "(Refs #1002) — Migration laeuft weiter.\n"
    )

with connection.cursor() as cursor:
    cursor.execute("SELECT pg_advisory_lock(1)")
    try:
        result = subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"])
        # Refs #1085: Nach erfolgreichem Migrate die Tabellen-Ownership auf den
        # DB-Owner (App-Rolle) normalisieren. Der Migrate-Job connectet als
        # BYPASSRLS-Admin (POSTGRES_USER-Override, Refs #863) — frisch migrierte
        # Tabellen sind dadurch admin-owned, und die NOSUPERUSER-App-Runtime-Rolle
        # bekäme 'permission denied'. REASSIGN OWNED überträgt sie env-agnostisch
        # und idempotent auf den DB-Owner; ein Fehler bricht den Deploy fail-fast
        # ab, bevor neue Web-Replicas live gehen.
        if result.returncode == 0:
            normalize = subprocess.run([sys.executable, "manage.py", "normalize_db_ownership"])
            if normalize.returncode != 0:
                result = normalize
    finally:
        cursor.execute("SELECT pg_advisory_unlock(1)")
sys.exit(result.returncode)
PY
