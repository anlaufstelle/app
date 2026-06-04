"""A5.1 (Refs #1024 / #1016): Tabelle für den shared DatabaseCache.

``settings/prod.py`` nutzt ``django.core.cache.backends.db.DatabaseCache``
(LOCATION ``anlaufstelle_cache``), damit django-ratelimit (Login/Sudo/Health)
und das Health-Detail-Caching (A7.2) prozessübergreifend zählen — LocMemCache
ist pro Gunicorn-Worker isoliert und würde Rate-Limits effektiv ge-N-fachen.

Die Tabelle wird hier per RunSQL angelegt (createcachetable-äquivalent,
settings-unabhängig: in dev/test/e2e mit LocMemCache bleibt sie ungenutzt, aber
harmlos). Schema entspricht Djangos DatabaseCache (cache_key/value/expires).
Migrationen laufen unter der App-Rolle (= DB-Owner, siehe
deploy/postgres-init/01-app-role.sh) → die Tabelle ist app-owned, die Runtime
hat damit vollen Zugriff. Nicht facility-gescoped → kein RLS.
"""

from django.db import migrations

_CREATE = """
CREATE TABLE IF NOT EXISTS anlaufstelle_cache (
    cache_key varchar(255) NOT NULL PRIMARY KEY,
    value text NOT NULL,
    expires timestamp with time zone NOT NULL
);
CREATE INDEX IF NOT EXISTS anlaufstelle_cache_expires_idx
    ON anlaufstelle_cache (expires);
"""

_DROP = "DROP TABLE IF EXISTS anlaufstelle_cache;"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0091_rls_superadmin_bypass_followup"),
    ]

    operations = [
        migrations.RunSQL(sql=_CREATE, reverse_sql=_DROP),
    ]
