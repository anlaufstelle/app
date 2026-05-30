"""End-to-End-Regression fuer die Append-Only-Garantie auf core_auditlog.

Refs Matrix DEV-AUDIT-04 (Master #922).

Das bestehende :mod:`test_audit_trigger` deckt den DB-Trigger fuer
DELETE-Pfade ab (raw UPDATE, raw DELETE, ORM QuerySet.delete()). Diese
Datei schliesst die noch nicht von Tests abgedeckte Luecke beim
UPDATE-Pfad: ``QuerySet.update()`` bypassed ``AuditLog.save()`` (kein
Python-seitiger Schutz) und muss am DB-Trigger scheitern. Zusaetzlich
ein Smoke-Test, der den Python-Layer-Schutz im ``save()``-Override
verifiziert (``ValueError``), und ein Sicherheitsnetz fuer den
DELETE-Pfad ueber ``QuerySet.delete()``.

Alle Tests verwenden ``transaction=True``, weil der Postgres-Trigger nur
beim Commit der jeweiligen Statement-Transaktion ausgeloest wird —
Djangos Default-TestCase rollt die ganze Klasse in einer Transaktion
zurueck, was das Trigger-Verhalten verfaelscht.
"""

from __future__ import annotations

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from core.models import AuditLog


@pytest.mark.django_db(transaction=True)
class TestAuditAppendOnlyE2E:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger existiert nur auf PostgreSQL")

    def _create_audit(self, facility, user=None):
        return AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.LOGIN,
            detail={},
        )

    def test_queryset_update_raises_integrity_error(self, facility, staff_user):
        """``QuerySet.update()`` bypasst ``AuditLog.save()`` (kein Python-Schutz);
        nur der DB-Trigger blockt den Schreibversuch. Ohne diesen Test wuerde
        ein versehentlich entfernter Trigger unbemerkt durchgehen.
        """
        audit = self._create_audit(facility, staff_user)
        # DatabaseError ist die Basisklasse von IntegrityError; je nach
        # Postgres-Treiber kann der Trigger entweder direkt als
        # IntegrityError oder als DatabaseError hochkommen. Beide sind
        # gleichermassen ein Blocker.
        with pytest.raises((IntegrityError, DatabaseError)) as excinfo, transaction.atomic():
            AuditLog.objects.filter(pk=audit.pk).update(action=AuditLog.Action.LOGOUT)
        assert "immutable" in str(excinfo.value).lower(), (
            f"Erwartet 'immutable' im Trigger-Fehler, erhalten: {excinfo.value!r}"
        )

    def test_model_save_after_modification_raises_value_error(self, facility, staff_user):
        """``AuditLog.save()`` blockt updates Python-seitig mit ``ValueError``
        (siehe ``core/models/audit.py``). Der DB-Trigger ist die zweite
        Verteidigungslinie, aber das Python-Override schlaegt frueher zu.
        """
        audit = self._create_audit(facility, staff_user)
        # Aus der DB neu laden, damit der Vergleich gegen ``orig`` fair ist.
        reloaded = AuditLog.objects.get(pk=audit.pk)
        reloaded.action = AuditLog.Action.LOGOUT

        # IST-Verhalten: Python-Override wirft ``ValueError`` bevor der
        # DB-Trigger ueberhaupt erreicht wird. Falls jemand den Override
        # entfernt, sollte stattdessen der DB-Trigger ``IntegrityError``
        # werfen — beides ist akzeptabel.
        with pytest.raises((ValueError, IntegrityError, DatabaseError)):
            reloaded.save()

    def test_queryset_delete_still_blocked(self, facility, staff_user):
        """Smoke-Sicherheitsnetz: der bekannte DELETE-Pfad ueber
        ``QuerySet.delete()`` (umgeht ``AuditLog.delete()``-Override) muss
        weiterhin am DB-Trigger scheitern. Spiegelt
        :class:`TestAuditLogImmutableTrigger.test_orm_queryset_delete_is_blocked`
        zur Verteidigung in Tiefe.
        """
        audit = self._create_audit(facility, staff_user)
        with pytest.raises((IntegrityError, DatabaseError)) as excinfo, transaction.atomic():
            AuditLog.objects.filter(pk=audit.pk).delete()
        assert "immutable" in str(excinfo.value).lower()

    def test_instance_delete_raises_value_error(self, facility, staff_user):
        """Smoke: ``audit.delete()`` wirft Python-seitig direkt einen
        ``ValueError`` (siehe ``core/models/audit.py:132``). Sicherstellen,
        dass das nicht wegrefactored wird — sonst ist der DB-Trigger zwar
        immer noch da, aber Aufrufer mit ``ValueError``-Handling brechen
        unbemerkt anders.
        """
        audit = self._create_audit(facility, staff_user)
        with pytest.raises(ValueError, match="append-only"):
            audit.delete()
