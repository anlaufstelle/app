"""Erweitert alle facility_isolation-Policies um einen Superadmin-Bypass.

Refs #867: ``super_admin`` ist installation-weit zustaendig (Persona Jonas)
und benoetigt facility-uebergreifende Read-Sicht — z.B. fuer den
``/system/``-Bereich, der Pre-Auth-AuditLogs (NULL-facility) und
Cross-Facility-Audits anzeigt.

Implementierung: Per Request setzt die ``FacilityScopeMiddleware`` die
zusaetzliche Session-Var ``app.is_super_admin`` (``'true'`` bei
super_admin, sonst ``''``). Alle facility_isolation-Policies werden um
einen ``OR``-Branch erweitert, der bei gesetzter Var den Filter
neutralisiert.

Pattern (USING + WITH CHECK):
::
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    )

Fuer ``core_auditlog`` bleibt der WITH-CHECK-Branch ``facility_id IS NULL``
zusaetzlich erhalten (Pre-Auth-INSERT, Refs #863).

Reverse: stellt die Policies aus 0047 + 0083 wieder her.
"""

from django.db import migrations

# Tabellen mit direkter ``facility_id``-Spalte (gleiche Liste wie 0047,
# erweitert um core_auditlog mit Spezial-WITH-CHECK).
_DIRECT_TABLES = [
    "core_client",
    "core_event",
    "core_case",
    "core_workitem",
    "core_documenttype",
    "core_fieldtemplate",
    "core_activity",
    "core_deletionrequest",
    "core_retentionproposal",
    "core_settings",
    "core_timefilter",
    "core_legalhold",
    "core_statisticssnapshot",
    "core_recentclientvisit",
]

# Tabellen ohne direkte ``facility_id``-Spalte — Policy via Join-Subselect.
_JOIN_TABLES = [
    (
        "core_eventhistory",
        "event_id IN ("
        "SELECT id FROM core_event "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ") OR current_setting('app.is_super_admin', true) = 'true'",
        # Reverse ohne Bypass:
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
        ") OR current_setting('app.is_super_admin', true) = 'true'",
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
        ") OR current_setting('app.is_super_admin', true) = 'true'",
        "case_id IN ("
        "SELECT id FROM core_case "
        "WHERE facility_id::text = current_setting('app.current_facility_id', true)"
        ")",
    ),
]

_DIRECT_POLICY_FORWARD = (
    "facility_id::text = current_setting('app.current_facility_id', true) "
    "OR current_setting('app.is_super_admin', true) = 'true'"
)

_DIRECT_POLICY_REVERSE = (
    "facility_id::text = current_setting('app.current_facility_id', true)"
)

# core_auditlog hat zusaetzlich einen WITH-CHECK-NULL-Branch (Refs #863).
_AUDITLOG_FORWARD = """
DROP POLICY IF EXISTS facility_isolation ON core_auditlog;
CREATE POLICY facility_isolation ON core_auditlog
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        facility_id IS NULL
        OR facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

_AUDITLOG_REVERSE = """
DROP POLICY IF EXISTS facility_isolation ON core_auditlog;
CREATE POLICY facility_isolation ON core_auditlog
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
    )
    WITH CHECK (
        facility_id IS NULL
        OR facility_id::text = current_setting('app.current_facility_id', true)
    );
"""


def _forward_sql() -> str:
    parts: list[str] = []
    for table in _DIRECT_TABLES:
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(
            f"CREATE POLICY facility_isolation ON {table} "
            f"USING ({_DIRECT_POLICY_FORWARD});"
        )
    for table, using_forward, _using_reverse in _JOIN_TABLES:
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(
            f"CREATE POLICY facility_isolation ON {table} USING ({using_forward});"
        )
    parts.append(_AUDITLOG_FORWARD)
    return "\n".join(parts)


def _reverse_sql() -> str:
    parts: list[str] = []
    for table in _DIRECT_TABLES:
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(
            f"CREATE POLICY facility_isolation ON {table} "
            f"USING ({_DIRECT_POLICY_REVERSE});"
        )
    for table, _using_forward, using_reverse in _JOIN_TABLES:
        parts.append(f"DROP POLICY IF EXISTS facility_isolation ON {table};")
        parts.append(
            f"CREATE POLICY facility_isolation ON {table} USING ({using_reverse});"
        )
    parts.append(_AUDITLOG_REVERSE)
    return "\n".join(parts)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0084_user_role_super_admin"),
    ]

    operations = [
        migrations.RunSQL(sql=_forward_sql(), reverse_sql=_reverse_sql()),
    ]
