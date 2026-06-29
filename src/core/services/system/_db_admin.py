"""DB-Admin-Helfer für privilegierte Operationen.

Kapselt sensible Eingriffe wie das transaktionale Umgehen append-only
Trigger. Alles, was hier liegt, ist Service-Layer-Wissen über die
Datenbank-Topologie und darf nicht in Models/Views auftauchen.
"""

from contextlib import contextmanager

from django.db import connection


def has_rls_bypass_context() -> bool:
    """Refs #1016 A1.1: Kann der aktuelle DB-Kontext RLS umgehen?

    True, wenn die Verbindungsrolle SUPERUSER/BYPASSRLS ist ODER die Session-GUC
    ``app.is_super_admin`` auf ``true`` steht. Andernfalls filtert RLS einen
    Wartungs-/Cron-Lauf auf 0 Zeilen (kein Request setzt ``app.current_facility_id``)
    — er waere wirkungslos und wuerde faelschlich Erfolg melden. No-op (``True``)
    auf Nicht-PostgreSQL (SQLite-Tests).

    Gemeinsame Quelle fuer ``enforce_retention`` (Refs #1016) sowie
    ``verify_audit_chain`` / ``backfill_audit_chain`` (Refs #1070), damit die
    Fail-Loud-Pruefung nicht dreifach driftet.
    """
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
        if row and row[0]:
            return True
        cur.execute("SELECT current_setting('app.is_super_admin', true) = 'true'")
        return bool(cur.fetchone()[0])


@contextmanager
def bypass_replication_triggers():
    """Disable session-local trigger firing for the wrapped block.

    Sets ``session_replication_role = replica`` on PostgreSQL so triggers
    marked ``ENABLE TRIGGER`` (the default) skip firing for writes
    inside the block. The flag is restored to ``origin`` in a ``finally``
    so the protection re-engages even on errors.

    Used for redacting ``EventHistory`` rows during DSGVO Art. 17
    aggregate-anonymization (see :func:`core.services.client.main.anonymize_client`)
    where the append-only trigger from migration 0012 would otherwise
    block the ``UPDATE``.

    No-op on non-PostgreSQL backends (test SQLite) — callers must
    accept that behaviour.
    """
    if connection.vendor != "postgresql":
        yield
        return

    with connection.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        try:
            yield
        finally:
            cur.execute("SET LOCAL session_replication_role = origin")
