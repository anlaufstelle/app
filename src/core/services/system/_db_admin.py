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
#
# Die REIHENFOLGE ist sicherheitsrelevant und kein Zufall (ADR-050 D5.5,
# Refs #2065): ``ALTER TABLE ... DISABLE TRIGGER`` nimmt einen
# ``SHARE ROW EXCLUSIVE``-Lock, der mit dem ``ROW EXCLUSIVE`` gewoehnlicher
# Schreiber kollidiert. Bis 0.89 stand ``core_auditlog`` zuerst — der
# Schreib-Reihenfolge der Anwendung genau entgegengesetzt: ``create_event``/
# ``update_event`` schreiben erst die ``EventHistory``-Zeile und (ausdruecklich
# als letzten Schritt, Refs #1345/#1956) den AuditLog-Eintrag. Das war ein
# ABBA-Zyklus — Bypass haelt ``core_auditlog`` und wartet auf
# ``core_eventhistory``, der Request haelt ``core_eventhistory`` und wartet auf
# ``core_auditlog`` —, den PostgreSQL mit ``deadlock detected`` aufloest, indem
# es EINE der beiden Seiten abbricht. Mit einem naechtlichen Historien-Sweep
# wuerde das zur Routine. Die Ordnung folgt deshalb jetzt der Anwendung:
# ``core_eventhistory`` vor ``core_auditlog``. Der Guard-Test dazu:
# ``test_append_only_trigger_reihenfolge_ist_eventhistory_vor_auditlog``.
_APPEND_ONLY_TRIGGERS = (
    ("core_eventhistory", "eventhistory_no_update"),
    ("core_eventhistory", "eventhistory_no_delete"),
    ("core_auditlog", "auditlog_immutable"),
)


def _selected_triggers(tables):
    """Die zu oeffnenden Trigger — ``None`` heisst „alle" (Bestandsverhalten).

    Die Auswahl ist bewusst **tabellen**- und nicht triggergenau (ADR-050 M-6):
    die DDL-Sperre haengt an der Tabelle, nicht am einzelnen Trigger, und genau
    sie ist das, was ein Aufrufer vermeiden will. Wer ``core_eventhistory``
    oeffnet, braucht ohnehin beide dortigen Trigger.
    """
    if tables is None:
        return list(_APPEND_ONLY_TRIGGERS)
    wanted = set(tables)
    return [entry for entry in _APPEND_ONLY_TRIGGERS if entry[0] in wanted]


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
def bypass_replication_triggers(tables=None):
    """Umgeht die Append-Only-Trigger fuer den umschlossenen Block per Owner-DDL.

    ``tables`` schraenkt ein, WELCHE Tabellen geoeffnet werden (ADR-050 M-6,
    Refs #2065); ``None`` = alle drei Trigger, also das unveraenderte
    Bestandsverhalten der vier Alt-Aufrufer. Der Sinn der Verengung ist nicht
    Sparsamkeit, sondern der Deadlock-Pfad: wer nur ``core_eventhistory``
    oeffnet, nimmt auf ``core_auditlog`` gar keine DDL-Sperre mehr, sondern nur
    das ``ROW EXCLUSIVE`` seines eigenen Nachweis-Inserts — dieselbe Ordnung
    wie jeder Request, kein Zyklus mehr moeglich. Nebeneffekt und fuer sich
    schon richtig: ein Historien-Sweep oeffnet nicht mehr auch das AuditLog.

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
    ``reencrypt_fields`` — alle vier ohne ``tables`` und damit unveraendert —
    sowie der EventHistory-Sweep aus ADR-050, der als einziger die Verengung
    auf ``core_eventhistory`` nutzt.

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
        for table, trigger in _selected_triggers(tables):
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


def current_connection_owns(table: str) -> bool:
    """Darf die AKTUELLE Verbindungsrolle ``ALTER TABLE`` auf *table* fahren?

    Refs #2085. Katalogbasiert, nie env-basiert:
    ``pg_has_role(current_user, relowner, 'USAGE')`` ist exakt die Bedingung,
    die PostgreSQL fuer Owner-DDL stellt (Owner **oder** Mitglied der
    Owner-Rolle; ein Superuser ist Mitglied von allem).

    Warum nicht ``POSTGRES_ADMIN_USER`` vergleichen: ``settings/prod.py``
    verbietet dem Runtime-Prozess hart (``ImproperlyConfigured``), die
    Admin-/Bootstrap-Zugangsdaten ueberhaupt zu **sehen** (Refs #1962) — ein
    env-basierter Check waere im Webprozess strukturell blind. Und ein
    Vergleich gegen ``settings.DATABASES['default']['USER']`` beantwortete die
    falsche Frage: der Rollenname sagt nichts ueber Ownership.

    No-op (``True``) auf Nicht-PostgreSQL: dort ist auch
    :func:`bypass_replication_triggers` ein No-op, der Direktweg also immer
    gangbar.
    """
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cur:
        cur.execute(
            "SELECT pg_catalog.pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_catalog.pg_class c WHERE c.oid = %s::regclass",
            [table],
        )
        row = cur.fetchone()
    return bool(row and row[0])
