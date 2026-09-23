"""Rueckwaerts-Sicherheit der RLS-Migration 0180 (Refs #2042).

``0180_rls_shifthandovernote`` schaltet RLS fuer die neue Tabelle ein und legt
die ``facility_isolation``-Policy an. Der Rueckweg (``migrate core 0179``) muss
beides vollstaendig zuruecknehmen: die Tabelle wird in 0179 NEU eingefuehrt,
der Zustand davor ist „kein RLS". Bliebe die Policy beim Unapply stehen,
scheiterte das erneute Apply mit ``policy ... already exists`` — genau der
Fehler, den ``test_migration_0104_reversible.py`` fuer die FK-Constraints
festhaelt.

Zwei Sicherungen, absichtlich beide (Refs #2042, Befund E-10):

* ``transaction=False`` — die Test-Transaktion rollt am Testende alles zurueck.
* ein ``finally``, das ausdruecklich auf die zu Testbeginn ermittelten
  ``graph.leaf_nodes()`` zurueckmigriert. Anfangs behauptete der
  Docstring das, der Code migrierte aber nur auf das fest kodierte ``0180``.
  Solange 0180 das Blatt ist, ist beides dasselbe — bei der naechsten
  Migration waere es das nicht mehr, und ein abgebrochener Test liesse das
  Schema auf einem Zwischenstand liegen (Lehre 83).
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

APP = "core"
TABLE = "core_shifthandovernote"
POLICY = "facility_isolation"
TARGET_BACKWARD = [(APP, "0179_shifthandovernote")]
TARGET_FORWARD = [(APP, "0180_rls_shifthandovernote")]


def _rls_state():
    """(``relrowsecurity``, Anzahl Policies) der Schichtnotiz-Tabelle."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s", [TABLE])
        row = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM pg_policies WHERE tablename = %s AND policyname = %s",
            [TABLE, POLICY],
        )
        policies = cursor.fetchone()[0]
    return row, policies


def _leaf_nodes():
    """Die aktuellen Blattknoten des Migrationsgraphen (Lehre 83)."""
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.mark.django_db(transaction=False)
def test_migration_0180_ist_rueckwaerts_anwendbar():
    blaetter = _leaf_nodes()
    enabled, forced = _rls_state()[0]
    assert enabled is True, "Vorbedingung: RLS ist auf der Schichtnotiz-Tabelle aktiv"
    assert forced is True, "Vorbedingung: FORCE RLS ist gesetzt (auch der Tabellen-Eigentuemer wird gefiltert)"
    assert _rls_state()[1] == 1

    try:
        MigrationExecutor(connection).migrate(TARGET_BACKWARD)
        (enabled_nachher, _forced_nachher), policies_nachher = _rls_state()
        assert enabled_nachher is False, "Unapply muss RLS abschalten — sonst bleibt eine Tabelle ohne Policy gesperrt"
        assert policies_nachher == 0, "Unapply muss die Policy loeschen — sonst scheitert das erneute Apply"

        MigrationExecutor(connection).migrate(TARGET_FORWARD)
        (enabled_wieder, forced_wieder), policies_wieder = _rls_state()
        assert enabled_wieder is True
        assert forced_wieder is True
        assert policies_wieder == 1
    finally:
        # Auch wenn oben eine Assertion reisst: das Schema gehoert zurueck auf
        # den Stand, den die Nachbartests erwarten (Refs #2042, E-10).
        MigrationExecutor(connection).migrate(blaetter)


@pytest.mark.django_db(transaction=False)
def test_policy_traegt_den_superadmin_bypass_zweig():
    """Ohne den OR-Zweig waere super_admin fuer diese Tabelle cross-facility blind."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT qual FROM pg_policies WHERE tablename = %s AND policyname = %s", [TABLE, POLICY])
        qual = cursor.fetchone()[0]
    assert "app.current_facility_id" in qual
    assert "app.is_super_admin" in qual
