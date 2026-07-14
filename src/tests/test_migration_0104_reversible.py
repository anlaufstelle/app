"""Rueckwaerts-Sicherheit der Migration 0104 (Refs #1347).

Migration ``0104_ondelete_hardening_legalhold_workitem_deletionrequest``
tauscht die urspruenglichen ``NO ACTION``-FK-Constraints der drei
Compliance-FKs gegen echte ``ON DELETE RESTRICT``/``ON DELETE SET NULL``-
Constraints aus (DAT-03, je FK ein DROP- + ein ADD-``RunSQL``). Ein
``migrate core 0103`` (Unapply) muss moeglich sein, ohne dass Postgres
mit "constraint already exists" abbricht.

Der Test faehrt die Migration innerhalb der ohnehin laufenden
Test-Transaktion einmal zurueck und wieder vor — dank ``pytest.mark.django_db``
wird die gesamte Transaktion am Testende zurueckgerollt, sodass das
Test-Schema fuer nachfolgende Tests unveraendert bleibt.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

APP = "core"
TARGET_FORWARD = [(APP, "0104_ondelete_hardening_legalhold_workitem_deletionrequest")]
TARGET_BACKWARD = [(APP, "0103_alter_auditlog_action")]


@pytest.mark.django_db(transaction=False)
def test_migration_0104_ist_rueckwaerts_anwendbar():
    """Unapply (migrate core 0103) und erneutes Apply duerfen nicht mit
    einem Postgres-Fehler abbrechen — vorher schlug das reverse_sql der
    ADD-RunSQL-Operationen mit 'constraint ... already exists' fehl, weil
    reverse_sql zwischen DROP- und ADD-Operation vertauscht war.
    """
    executor = MigrationExecutor(connection)

    executor.migrate(TARGET_BACKWARD)

    executor = MigrationExecutor(connection)
    executor.migrate(TARGET_FORWARD)
