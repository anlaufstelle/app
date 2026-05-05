"""DB-Admin-Helfer für privilegierte Operationen.

Kapselt sensible Eingriffe wie das transaktionale Umgehen append-only
Trigger. Alles, was hier liegt, ist Service-Layer-Wissen über die
Datenbank-Topologie und darf nicht in Models/Views auftauchen.
"""

from contextlib import contextmanager

from django.db import connection


@contextmanager
def bypass_replication_triggers():
    """Disable session-local trigger firing for the wrapped block.

    Sets ``session_replication_role = replica`` on PostgreSQL so triggers
    marked ``ENABLE TRIGGER`` (the default) skip firing for writes
    inside the block. The flag is restored to ``origin`` in a ``finally``
    so the protection re-engages even on errors.

    Used for redacting ``EventHistory`` rows during DSGVO Art. 17
    aggregate-anonymization (see :func:`core.services.clients.anonymize_client`)
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
