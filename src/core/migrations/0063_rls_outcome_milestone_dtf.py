"""Zieht RLS-Policies fuer drei transitiv facility-gescopte Tabellen nach.

Im Retro-Audit (#600) aufgefallen:

* ``core_outcomegoal`` (`goal.case.facility_id`)
* ``core_milestone`` (`milestone.goal.case.facility_id`, doppelt transitiv)
* ``core_documenttypefield`` (`dtf.document_type.facility_id`, Through-Tabelle)

Alle drei Tabellen haben keine eigene ``facility_id``-Spalte — Policy
laueft als Subquery analog zu ``core_episode`` in
[0047_postgres_rls_setup.py](../migrations/0047_postgres_rls_setup.py).

Refs #600, #615, #616, #617.
"""

from django.db import migrations

JOIN_TABLES = [
    (
        "core_outcomegoal",
        "case_id IN ("
        "SELECT id FROM core_case "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ")",
    ),
    (
        "core_milestone",
        "goal_id IN ("
        "SELECT id FROM core_outcomegoal WHERE case_id IN ("
        "SELECT id FROM core_case "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ")"
        ")",
    ),
    (
        "core_documenttypefield",
        "document_type_id IN ("
        "SELECT id FROM core_documenttype "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ")",
    ),
]


def _enable_sql() -> str:
    parts: list[str] = []
    for table, using_clause in JOIN_TABLES:
        parts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        parts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        parts.append(
            f"CREATE POLICY facility_isolation ON {table} USING ({using_clause});"
        )
    return "\n".join(parts)


def _disable_sql() -> str:
    parts: list[str] = []
    for table, _ in JOIN_TABLES:
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(parts)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0062_alter_auditlog_action"),
    ]

    operations = [
        migrations.RunSQL(
            sql=_enable_sql(),
            reverse_sql=_disable_sql(),
        ),
    ]
