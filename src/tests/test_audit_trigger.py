"""Regressionstests für den AuditLog-Immutable-Trigger (Refs #733).

Migration ``0024_auditlog_immutable_trigger.py`` installiert einen
Postgres-Trigger ``BEFORE UPDATE OR DELETE ON core_auditlog``, der jede
Mutation mit einem ``RAISE EXCEPTION`` blockt. Damit sind AuditLog-
Eintraege nicht nur Python-seitig (siehe ``AuditLog.save``/``delete``-
Override) sondern auch via Raw SQL und ORM-Bypasses unveraenderlich.

Audit-Massnahme #14 + C.1.2 fordert verifizieren, dass der Trigger
greift und nach Restore erhalten bleibt — diese Tests sind die
schnelle Smoke-Regression dafuer.
"""

import pytest
from django.db import DatabaseError, connection, transaction

from core.models import AuditLog


@pytest.mark.django_db(transaction=True)
class TestAuditLogImmutableTrigger:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger existiert nur auf PostgreSQL")

    def _create_log(self, facility):
        return AuditLog.objects.create(
            facility=facility,
            action=AuditLog.Action.LOGIN,
        )

    def test_raw_update_is_blocked(self, facility):
        log = self._create_log(facility)
        with pytest.raises(DatabaseError) as excinfo:
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    "UPDATE core_auditlog SET action = %s WHERE id = %s",
                    ["logout", str(log.pk)],
                )
        assert "immutable" in str(excinfo.value).lower(), (
            f"Erwartet 'immutable' im Trigger-Fehler, erhalten: {excinfo.value!r}"
        )

    def test_raw_delete_is_blocked(self, facility):
        log = self._create_log(facility)
        with pytest.raises(DatabaseError) as excinfo:
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute("DELETE FROM core_auditlog WHERE id = %s", [str(log.pk)])
        assert "immutable" in str(excinfo.value).lower(), (
            f"Erwartet 'immutable' im Trigger-Fehler, erhalten: {excinfo.value!r}"
        )

    def test_orm_queryset_delete_is_blocked(self, facility):
        # Auch QuerySet.delete() (das ``AuditLog.delete()`` umgeht) muss am
        # DB-Trigger scheitern. Damit ist der Schutz unabhaengig von
        # Python-Override.
        self._create_log(facility)
        with pytest.raises(DatabaseError) as excinfo:
            with transaction.atomic():
                AuditLog.objects.filter(facility=facility).delete()
        assert "immutable" in str(excinfo.value).lower()

    def test_insert_is_allowed(self, facility):
        # Smoke: Insert bleibt erlaubt — sonst wuerde das halbe Audit-Log
        # nicht mehr funktionieren.
        before = AuditLog.objects.filter(facility=facility).count()
        AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN)
        after = AuditLog.objects.filter(facility=facility).count()
        assert after == before + 1
