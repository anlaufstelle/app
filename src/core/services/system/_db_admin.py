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


# A1.4 (Refs #1026): Die legitimen Wartungspfade umgehen GENAU diese drei
# Append-Only-Trigger — kein pauschales ``session_replication_role = replica``
# mehr, das auch FK-/Constraint-Trigger stillgelegt haette.
_APPEND_ONLY_TRIGGERS = (
    ("core_auditlog", "auditlog_immutable"),
    ("core_eventhistory", "eventhistory_no_update"),
    ("core_eventhistory", "eventhistory_no_delete"),
)

# ``pg_trigger.tgenabled`` -> passendes ENABLE-Statement, um den Vorzustand
# exakt wiederherzustellen (``A`` = ENABLE ALWAYS ist der gehaertete Ruhezustand,
# Migration 0107). Ein bereits ``D`` (disabled) markierter Trigger wird nicht
# angefasst.
_TGENABLED_TO_ENABLE = {
    "O": "ENABLE",  # origin (frueherer Default)
    "A": "ENABLE ALWAYS",  # gehaertet, Refs #1026
    "R": "ENABLE REPLICA",
}


@contextmanager
def bypass_replication_triggers():
    """Umgeht die Append-Only-Trigger fuer den umschlossenen Block per Owner-DDL.

    A1.4 (Refs #1026): Frueher lief das ueber ``SET LOCAL
    session_replication_role = replica``. Das setzt aber (a) voraus, dass die
    Rolle ``GRANT SET ON PARAMETER`` hat, und (b) wirkt NICHT gegen einen
    ``ENABLE ALWAYS``-Trigger — der feuert auch im ``replica``-Modus. Da die
    Trigger jetzt ``ENABLE ALWAYS`` sind (Defense-in-Depth gegen den
    ``replica``-Vektor) und die Wartungs-Rolle ohnehin **Owner** der Tabellen
    ist (Runtime-/Owner-Trennung), umgehen wir sie gezielt per
    ``ALTER TABLE ... DISABLE TRIGGER`` / ``ENABLE ...`` — nur fuer die drei
    Append-Only-Trigger, nicht pauschal alle Trigger.

    SIGKILL-Sicherheit: DISABLE, Arbeit und Re-ENABLE laufen in EINEM
    ``transaction.atomic()``. Bricht der Prozess vor dem Commit ab, rollt
    PostgreSQL das transaktionale DDL (inkl. DISABLE) zurueck — der Trigger
    bleibt aktiv. Das war die urspruengliche Motivation, weg von
    ``ALTER TABLE`` zu gehen (Refs #781); durch die Transaktionsklammer ist
    genau dieser Fall jetzt abgedeckt. Der Vorzustand jedes Triggers wird aus
    ``pg_trigger.tgenabled`` gelesen und exakt wiederhergestellt.

    Aufrufer (alle laufen als Owner-/Admin-Wartungsrolle): ``prune_auditlog``,
    ``Client.anonymize()`` (DSGVO Art. 17), ``backfill_audit_chain``,
    ``reencrypt_fields``.

    No-op on non-PostgreSQL backends (test SQLite) — callers must
    accept that behaviour.
    """
    if connection.vendor != "postgresql":
        yield
        return

    with transaction.atomic(), connection.cursor() as cur:
        # Django legt FK-Constraints als DEFERRABLE INITIALLY DEFERRED an. Hat
        # die umschliessende Transaktion vorher schon in eine der Tabellen
        # geschrieben (z.B. ``anonymize_client`` fuegt EventHistory-Zeilen ein,
        # bevor es sie redigiert), stehen deren Constraint-Trigger-Events noch
        # aus — dann bricht ``ALTER TABLE ... DISABLE TRIGGER`` mit
        # ``cannot ALTER TABLE ... because it has pending trigger events`` ab.
        # ``SET CONSTRAINTS ALL IMMEDIATE`` feuert die (gueltigen) Deferred-
        # Checks jetzt und raeumt die Warteschlange, sodass das Owner-DDL greift.
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        restore: list[tuple[str, str, str]] = []
        for table, trigger in _APPEND_ONLY_TRIGGERS:
            cur.execute(
                "SELECT tgenabled FROM pg_trigger WHERE tgname = %s AND tgrelid = %s::regclass",
                [trigger, table],
            )
            row = cur.fetchone()
            if row is None:
                continue  # Trigger existiert (noch) nicht — nichts zu umgehen.
            enable_kw = _TGENABLED_TO_ENABLE.get(row[0])
            if enable_kw is None:
                continue  # bereits DISABLE ('D') — Vorzustand belassen.
            cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")  # noqa: S608 — konstante Identifier
            restore.append((table, trigger, enable_kw))
        yield
        # Erfolgspfad: Vorzustand exakt wiederherstellen. Bei einer Exception im
        # yield rollt die Transaktion das DISABLE ohnehin mit zurueck.
        for table, trigger, enable_kw in restore:
            cur.execute(f"ALTER TABLE {table} {enable_kw} TRIGGER {trigger}")  # noqa: S608 — konstante Identifier


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
