#!/bin/sh
set -e

# Migrate with advisory lock (prevents race conditions with multiple replicas)
python -c "
import subprocess, sys, os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'anlaufstelle.settings.prod')
django.setup()
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute('SELECT pg_advisory_lock(1)')
    result = subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'])
    cursor.execute('SELECT pg_advisory_unlock(1)')
    sys.exit(result.returncode)
"

python manage.py collectstatic --noinput

exec gunicorn anlaufstelle.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-30}"
