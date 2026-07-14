"""DB-Admin-Helfer für privilegierte Operationen.

Kapselt sensible Eingriffe wie das transaktionale Umgehen append-only
Trigger. Alles, was hier liegt, ist Service-Layer-Wissen über die
Datenbank-Topologie und darf nicht in Models/Views auftauchen.
"""

from contextlib import contextmanager

from django.db import connection, transaction


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


@contextmanager
def rls_bypass_for_read():
    """Transaktionslokaler ``app.is_super_admin``-Bypass fuer lesende Zugriffe auf
    facility-lose (``facility_id IS NULL``) RLS-geschuetzte Zeilen (Refs #1335).

    Migration 0047/0085 macht ``facility_id IS NULL``-Zeilen (installationsweite
    AuditLog-Marker wie Cron-Last-Run) NUR sichtbar fuer SUPERUSER/BYPASSRLS-
    Rollen oder wenn das GUC ``app.is_super_admin`` auf ``true`` steht. Die
    Produktions-App-Rolle ist NOBYPASSRLS (ops-runbook §9); ``FacilityScopeMiddleware``
    setzt das GUC nur fuer authentifizierte super_admin-Sessions — ein anonymer
    Token-Caller (z.B. ``X-Health-Token``) oder eine nicht-super_admin
    authentifizierte Session sehen die Zeilen sonst NIE.

    Setzt das GUC — analog zu
    :func:`core.services.compliance.breach_detection.run_system_detections`
    (Refs #1368) — ``SET LOCAL`` (transaktionslokal) innerhalb eines
    ``transaction.atomic()``-Blocks und stellt im ``finally`` den Vorwert
    wieder her, damit die Erhoehung nicht auf einer (potenziell
    wiederverwendeten) Connection stehen bleibt. No-op auf Nicht-PostgreSQL
    (SQLite-Tests).

    NUR fuer lesende Zugriffe gedacht — wer zusaetzlich facility-lose Zeilen
    SCHREIBEN muss, braucht die Reads und Writes im selben GUC-Scope (siehe
    ``run_system_detections``).
    """
    if connection.vendor != "postgresql":
        yield
        return

    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.is_super_admin', true)")
        previous = cur.fetchone()[0] or ""
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        try:
            yield
        finally:
            cur.execute("SELECT set_config('app.is_super_admin', %s, true)", [previous])
