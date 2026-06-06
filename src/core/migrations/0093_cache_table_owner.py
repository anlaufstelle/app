"""Refs #1030: Cache-Tabelle dem Datenbank-Owner (App-Rolle) zuweisen.

Migration 0092 legt ``anlaufstelle_cache`` per ``RunSQL CREATE TABLE`` an. Im
prod-/dev-Deploy laufen Migrationen seit #863 bewusst als BYPASSRLS-**Admin**-
Rolle (``POSTGRES_ADMIN_USER``), nicht als App-Rolle — damit RunPython-Default-
Daten in RLS-geschützte Tabellen schreiben können. Dadurch wird die Cache-
Tabelle *admin-owned*, und die NOSUPERUSER/NOBYPASSRLS-Runtime-App-Rolle bekommt
``permission denied for table anlaufstelle_cache`` (web-Crash-Loop, unhealthy).
Die ``ALTER DEFAULT PRIVILEGES`` in ``deploy/postgres-init/01-app-role.sh``
greifen nur in Richtung App→Admin, nicht Admin→App.

Fix: Owner **env-agnostisch** auf den Datenbank-Owner setzen. Der DB-Owner ist
per ``ALTER DATABASE OWNER TO app_user`` (01-app-role.sh) die App-Rolle. In
dev/test/e2e, wo Migrationen ohnehin als App-Rolle/DB-Owner laufen, ist der
``ALTER TABLE … OWNER`` ein No-op. Im Deploy darf die Admin-Rolle den Owner
wechseln, weil sie Mitglied der App-Rolle ist (``GRANT app_user TO admin``).
"""

from django.db import migrations

# Owner der Cache-Tabelle auf den Datenbank-Owner (= App-Rolle) setzen.
# ``to_regclass``-Guard: no-op, falls die Tabelle (wider Erwarten) fehlt.
_FIX_OWNER = """
DO $$
DECLARE
    owner_role text;
BEGIN
    IF to_regclass('public.anlaufstelle_cache') IS NULL THEN
        RETURN;
    END IF;
    SELECT pg_catalog.pg_get_userbyid(datdba) INTO owner_role
        FROM pg_catalog.pg_database
        WHERE datname = current_database();
    EXECUTE format('ALTER TABLE public.anlaufstelle_cache OWNER TO %I', owner_role);
END
$$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0092_cache_table"),
    ]

    operations = [
        migrations.RunSQL(sql=_FIX_OWNER, reverse_sql=migrations.RunSQL.noop),
    ]
