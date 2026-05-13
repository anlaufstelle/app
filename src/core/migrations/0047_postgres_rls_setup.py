"""PostgreSQL Row Level Security als Defense-in-Depth.

Aktiviert RLS auf allen facility-scoped Tabellen. Jede Policy filtert anhand
der Session-Variable ``app.current_facility_id``, die die
``FacilityScopeMiddleware`` pro Request via ``SET LOCAL`` setzt.

Fuer Tabellen ohne direkte ``facility_id``-Spalte (``core_eventhistory``,
``core_eventattachment``, ``core_episode``) wird die Policy ueber ein Join-
Subselect formuliert.

Superuser-DB-Rollen umgehen RLS per Postgres-Default — in Produktion muss
der Django-DB-User daher **kein** Superuser sein. ``FORCE ROW LEVEL
SECURITY`` wird gesetzt, damit auch Tabelleneigentuemer der Policy
unterliegen.

Refs #542.
"""

from django.db import migrations

# Tabellen mit direkter ``facility_id``-Spalte.
DIRECT_TABLES = [
    "core_client",
    "core_event",
    "core_case",
    "core_workitem",
    "core_documenttype",
    "core_fieldtemplate",
    "core_auditlog",
    "core_activity",
    "core_deletionrequest",
    "core_retentionproposal",
    "core_settings",
    "core_timefilter",
    "core_legalhold",
    "core_statisticssnapshot",
    "core_recentclientvisit",
]

# Tabellen ohne direkte ``facility_id``-Spalte — Policy via Join.
# Eintrag: (tabellenname, USING-Klausel).
JOIN_TABLES = [
    (
        "core_eventhistory",
        "event_id IN ("
        "SELECT id FROM core_event "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ")",
    ),
    (
        "core_eventattachment",
        "event_id IN ("
        "SELECT id FROM core_event "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ")",
    ),
    (
        "core_episode",
        "case_id IN ("
        "SELECT id FROM core_case "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ")",
    ),
]

# Hinweis zu ``core_auditlog``: ``facility_id`` ist nullable. NULL-Zeilen
# (z.B. globale Systemereignisse vor dem ersten Login) matchen die
# USING-Policy nicht und sind damit ausschliesslich fuer RLS-bypassende
# Rollen (Superuser/BYPASSRLS) sichtbar. INSERTs mit facility=NULL werden
# in Migration 0083 explizit ueber WITH CHECK erlaubt — siehe
# ``0083_auditlog_rls_with_check.py``.

_DIRECT_POLICY = (
    "facility_id::text = current_setting('app.current_facility_id', true)"
)


def _enable_sql() -> str:
    parts: list[str] = []
    for table in DIRECT_TABLES:
        parts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        parts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        parts.append(
            f"CREATE POLICY facility_isolation ON {table} "
            f"USING ({_DIRECT_POLICY});"
        )
    for table, using_clause in JOIN_TABLES:
        parts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        parts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        parts.append(
            f"CREATE POLICY facility_isolation ON {table} USING ({using_clause});"
        )
    return "\n".join(parts)


def _disable_sql() -> str:
    parts: list[str] = []
    for table in DIRECT_TABLES:
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    for table, _ in JOIN_TABLES:
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(parts)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0046_mfa_totp"),
    ]

    operations = [
        migrations.RunSQL(
            sql=_enable_sql(),
            reverse_sql=_disable_sql(),
        ),
    ]
