"""Fail-Loud-Guards fuer Datenmigrationen (Refs #1746).

**Problem.** ``RunPython``-Backfills auf RLS-Tabellen setzen stillschweigend
voraus, dass sie als BYPASSRLS-Migrationsrolle laufen ([ADR-020](../../../docs/adr/020-three-role-postgres-model.md)).
Liefe eine solche Migration versehentlich unter der NOBYPASSRLS-App-Rolle,
griffe die ``facility_isolation``-Policy: ohne gesetztes
``app.current_facility_id`` traefe der Backfill **still 0 Zeilen**. Die
Migration gilt als erfolgreich, der Backfill fehlt — und faellt erst auf, wenn
Nutzerinnen die fehlenden Daten bemerken.

**Loesung.** :func:`assert_migration_role_bypasses_rls` als erste Zeile jeder
``RunPython``-Funktion, die RLS-Tabellen liest oder schreibt::

    from django.db import migrations

    from core.utils.migration_guards import assert_migration_role_bypasses_rls


    def backfill(apps, schema_editor):
        assert_migration_role_bypasses_rls(schema_editor)
        Client = apps.get_model("core", "Client")
        ...


    class Migration(migrations.Migration):
        operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]

Abgrenzung zu den bestehenden Pruefungen:

* ``core.management.commands.check_db_roles.check_db_roles`` prueft die
  Rollen-*Topologie* (existieren App-/Admin-Rolle mit den richtigen
  Attributen?), nicht die laufende Verbindung.
* ``current_connection_bypasses_rls`` (Refs #1375) prueft die Migrate-*Job*-
  Verbindung einmal vorab in ``docker-migrate.sh`` — greift also nur im
  Container-Deploy, nicht bei einem manuellen ``manage.py migrate``, im
  Restore-/Drill-Pfad oder in einer Self-Hosting-Umgebung ohne dieses Skript.
* Dieser Guard sitzt **in** der Migration und schuetzt damit jeden Aufrufweg.
* ``core.services.system._db_admin.has_rls_bypass_context`` akzeptiert
  zusaetzlich die Session-GUC ``app.is_super_admin`` — fuer Wartungs-/Cron-Pfade
  richtig, fuer Migrationen bewusst **nicht**: ADR-020 legt die Migrationsrolle
  auf ``POSTGRES_ADMIN_USER`` fest, und kein Migrationspfad setzt diese GUC.

**Bestandsaufnahme (Refs #1746, Stand 0122 — reine Doku, kein Rueckwirken).**
Datenmigrationen *nach* ``0047`` (RLS-Setup), die RLS-Tabellen anfassen und
damit vom Fehllauf betroffen gewesen waeren:

===========  ==========================================================
Migration    Beruehrte RLS-Tabellen
===========  ==========================================================
``0068``     ``core_attachment`` (JOIN-Policy)
``0071``     ``core_fieldtemplate``
``0074``     ``core_eventhistory`` (JOIN-Policy)
``0080``     ``core_case``
``0081``     ``core_event``, ``core_documenttypefield``
``0099``     ``core_documenttype``, ``core_settings``
``0115``     ``core_documenttype``, ``core_documenttypefield``, ``core_fieldgroup``
``0117``     ``core_documenttype``
``0120``     ``core_fieldtemplate``, ``core_client``, ``core_case``, ``core_event``, ``core_quicktemplate``
``0122``     ``core_statisticssnapshot``
===========  ==========================================================

Nicht betroffen: ``0018``/``0023``/``0035``/``0037`` (liefen *vor* ``0047``, RLS
existierte zur Laufzeit noch nicht), ``0084``/``0094`` (``core_user`` —
Auth-Grenze, bewusst ohne RLS), ``0093`` (Cache-Tabelle), ``0102``
(``otp_totp_totpdevice``), ``0049`` (Materialized View, bewusst ohne RLS).

Diese Migrationen werden **nicht** nachtraeglich mit dem Guard versehen. Zwei
Klassen mit unterschiedlicher Begruendung:

* ``0068``–``0099`` sind Bestandteil von ``v0.23.0`` (Snapshot endet bei
  ``0109``) und auf allen Instanzen laengst angewandt — ein nachtraeglicher
  Guard liefe dort nie und schriebe nur die Migrationshistorie um.
* ``0115``/``0117``/``0120``/``0122`` liegen zwischen dem ``v0.23.0``-Snapshot
  (endet bei ``0109``) und ``0123``; beim Update einer Instanz laufen sie im
  selben ``migrate``-Aufruf wie ``0123``. Da ``0123`` den Guard traegt und bei
  falscher Rolle fail-loud abbricht, *bevor* die Batch committet, ist der ganze
  Lauf transitiv geschuetzt. Ein Retrofit auf diese vier bliebe moeglich, wurde
  aber bewusst unterlassen, um releasefertige Migrationsdateien nicht erneut
  anzufassen — der Guard ist ab ``0123`` die Konvention fuer *neue*
  Datenmigrationen.
"""

from __future__ import annotations

from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


class MigrationRoleError(RuntimeError):
    """Die Migrations-Verbindung umgeht RLS nicht — Datenmigration abgebrochen."""


def connection_bypasses_rls(db_connection: BaseDatabaseWrapper) -> bool:
    """True, wenn die Rolle von ``db_connection`` Row Level Security umgeht.

    "Umgeht RLS" = ``SUPERUSER`` ODER ``rolbypassrls`` — ein Superuser umgeht RLS
    per Definition, auch wenn ``rolbypassrls`` im Katalog ``false`` ist.

    Gemeinsame Quelle fuer :func:`assert_migration_role_bypasses_rls` (Refs
    #1746) und die Deploy-Gate-Assertion
    ``check_db_roles.current_connection_bypasses_rls`` (Refs #1375), damit die
    Pruefung nicht doppelt driftet.
    """
    with db_connection.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
    if not row:
        return False
    return bool(row[0]) or bool(row[1])


def assert_migration_role_bypasses_rls(schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Bricht eine ``RunPython``-Datenmigration ab, wenn ihre Rolle RLS nicht umgeht.

    Aufruf als **erste Zeile** jeder ``RunPython``-Funktion, die RLS-Tabellen
    liest oder schreibt (Beispiel im Modul-Docstring). ``schema_editor`` kommt
    aus der ``RunPython``-Signatur; der Guard prueft dessen Verbindung, nicht die
    globale Default-Connection.

    No-op auf Nicht-PostgreSQL-Backends (dort gibt es weder ``pg_roles`` noch
    RLS). Raises :class:`MigrationRoleError` — Django bricht die Migration damit
    ab und rollt die umschliessende Transaktion zurueck.
    """
    db_connection = schema_editor.connection
    if db_connection.vendor != "postgresql":
        return
    if connection_bypasses_rls(db_connection):
        return

    with db_connection.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
    role = row[0] if row else "<unbekannt>"

    raise MigrationRoleError(
        f"Datenmigration abgebrochen: die Verbindungsrolle '{role}' umgeht Row Level Security nicht "
        "(weder SUPERUSER noch BYPASSRLS). Unter RLS traefe dieser Lauf ohne gesetztes "
        "'app.current_facility_id' still 0 Zeilen und die Migration wuerde faelschlich als "
        "erfolgreich gelten. Migrationen laufen laut ADR-020 als Admin-Rolle "
        "(POSTGRES_ADMIN_USER, NOSUPERUSER BYPASSRLS): DB-Zugangsdaten des Migrate-Laufs pruefen "
        "(docker-migrate.sh setzt POSTGRES_USER auf POSTGRES_ADMIN_USER) und erneut migrieren."
    )
