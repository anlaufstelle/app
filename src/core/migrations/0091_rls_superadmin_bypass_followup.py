"""Refs #1016 A1.3: Zieht den super_admin-Bypass-OR-Branch fuer vier
facility-gescopte Tabellen nach, die ``0085_rls_superadmin_bypass`` ausliess.

Die Policies wurden in 0057 (``core_quicktemplate``) bzw. 0063
(``core_outcomegoal``/``core_milestone``/``core_documenttypefield``)
angelegt, aber ohne den ``current_setting('app.is_super_admin', true) =
'true'``-Branch. Dadurch ist der ``super_admin`` (Refs #867) fuer genau
diese Tabellen cross-facility blind, obwohl 0085 ihn ueberall sonst
freischaltet.

Muster identisch zu ``0085_rls_superadmin_bypass`` (DROP + CREATE der
``facility_isolation``-Policy). Reverse stellt den Stand aus 0057/0063
(ohne Bypass) wieder her. RLS/FORCE bleiben unberuehrt (in 0057/0063
aktiviert).
"""

from django.db import migrations

_BYPASS = "current_setting('app.is_super_admin', true) = 'true'"

# (Tabelle, USING-Klausel OHNE Bypass — exakt wie in 0057/0063).
_TABLES = [
    (
        "core_quicktemplate",
        "facility_id::text = current_setting('app.current_facility_id', true)",
    ),
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


def _sql(*, with_bypass: bool) -> str:
    parts: list[str] = []
    for table, base in _TABLES:
        using = f"{base} OR {_BYPASS}" if with_bypass else base
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(f"CREATE POLICY facility_isolation ON {table} USING ({using});")
    return "\n".join(parts)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0090_auditlog_cron_actions"),
    ]

    operations = [
        migrations.RunSQL(sql=_sql(with_bypass=True), reverse_sql=_sql(with_bypass=False)),
    ]
