"""Datenintegritaets-Haertung der Compliance-FKs auf User (Refs #1347).

Drei Foreign Keys zeigten bisher mit ``on_delete=CASCADE`` auf
``settings.AUTH_USER_MODEL``:

* ``LegalHold.created_by`` — eine harte User-Loeschung durfte den Legal
  Hold NICHT mitreissen (Spoliation-/Nachweisrisiko). Jetzt ``PROTECT``.
* ``WorkItem.created_by`` und ``DeletionRequest.requested_by`` — die
  fachliche Historie (Aufgabe/Loeschantrag) soll den User ueberleben.
  Jetzt ``SET_NULL`` (Felder ``null=True``).

Diese Tests decken sowohl die ORM-Ebene (Django-Collector) als auch,
als Defense-in-Depth (DAT-03), die echten Postgres-``ON DELETE``-
Constraints ab, die per RunSQL in Migration 0104 fuer genau diese drei
FKs nachgezogen wurden (Django erzeugt FK-Constraints sonst immer als
``NO ACTION`` — ``on_delete`` wirkt sonst rein im Python-Collector).

Zusaetzlich: DAT-02, eine Partial-Unique gegen doppelte aktive Legal
Holds auf demselben (facility, target_type, target_id).
"""

import uuid

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from core.models import DeletionRequest, LegalHold, User, WorkItem


@pytest.mark.django_db
class TestLegalHoldProtectOnUserDelete:
    """DAT-04: ``LegalHold.created_by`` ist ``PROTECT`` — der Ersteller darf
    nicht verschwinden, solange der Hold besteht (Spoliation-Schutz)."""

    def test_user_delete_raises_protected_error(self, facility, lead_user, sample_event):
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Aufbewahrungspflicht",
            created_by=lead_user,
        )
        with pytest.raises(ProtectedError):
            lead_user.delete()
        assert LegalHold.objects.filter(pk=hold.pk).exists(), "LegalHold wurde trotz PROTECT-Constraint mitgeloescht."
        assert User.objects.filter(pk=lead_user.pk).exists(), (
            "User wurde trotz ProtectedError geloescht — PROTECT hat nicht gegriffen."
        )


@pytest.mark.django_db
class TestWorkItemSetNullOnUserDelete:
    """DAT-04: ``WorkItem.created_by`` ist ``SET_NULL`` — die Aufgabe bleibt
    erhalten, der User ist loeschbar."""

    def test_user_delete_sets_created_by_null(self, staff_user, sample_workitem):
        assert sample_workitem.created_by_id == staff_user.pk
        staff_user.delete()
        sample_workitem.refresh_from_db()
        assert sample_workitem.created_by_id is None, (
            "WorkItem.created_by ist nach User.delete() nicht NULL — SET_NULL hat nicht gegriffen."
        )


@pytest.mark.django_db
class TestDeletionRequestSetNullOnUserDelete:
    """DAT-04: ``DeletionRequest.requested_by`` ist ``SET_NULL`` — der
    4-Augen-Nachweis bleibt bestehen, der User ist loeschbar."""

    def test_user_delete_sets_requested_by_null(self, staff_user, deletion_request):
        assert deletion_request.requested_by_id == staff_user.pk
        staff_user.delete()
        deletion_request.refresh_from_db()
        assert deletion_request.requested_by_id is None, (
            "DeletionRequest.requested_by ist nach User.delete() nicht NULL — SET_NULL hat nicht gegriffen."
        )


@pytest.mark.django_db
class TestLegalHoldUniqueActiveConstraint:
    """DAT-02: Partial-Unique gegen doppelte AKTIVE Legal Holds auf
    demselben (facility, target_type, target_id) — analog
    ``unique_active_retention_proposal``/``unique_pending_deletion_request``."""

    def test_duplicate_active_hold_on_same_target_is_rejected(self, facility, lead_user, sample_event):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Erster Hold",
            created_by=lead_user,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            LegalHold.objects.create(
                facility=facility,
                target_type="Event",
                target_id=sample_event.pk,
                reason="Doppelter Hold",
                created_by=lead_user,
            )

    def test_dismissed_hold_does_not_block_new_hold_on_same_target(self, facility, lead_user, sample_event):
        first = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Erster Hold",
            created_by=lead_user,
        )
        first.dismissed_at = timezone.now()
        first.dismissed_by = lead_user
        first.save(update_fields=["dismissed_at", "dismissed_by"])

        second = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Zweiter Hold nach Aufhebung",
            created_by=lead_user,
        )
        assert LegalHold.objects.filter(facility=facility, target_id=sample_event.pk).count() == 2
        assert second.pk != first.pk

    def test_hold_on_different_target_is_unaffected(self, facility, lead_user, sample_event):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Erster Hold",
            created_by=lead_user,
        )
        # Anderer target_id -> keine Kollision.
        other = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Anderes Ziel",
            created_by=lead_user,
        )
        assert other.pk is not None


@pytest.mark.django_db(transaction=True)
class TestOnDeleteDbLevelConstraints:
    """DAT-03 Defense-in-Depth: die echten Postgres-``ON DELETE``-Constraints
    fuer die drei Compliance-FKs greifen auch bei einer Raw-SQL-Loeschung,
    die den Python-ORM-Collector komplett umgeht (Migration 0104)."""

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("ON DELETE-Constraints existieren nur auf PostgreSQL")

    def _lone_user(self, facility, username):
        """Frischer User ohne weitere FK-Referenzen (Client/Event/Case etc.),
        damit der Raw-SQL-DELETE nur an der jeweils getesteten Compliance-FK
        haengt, nicht an einer der ~60 anderen (bewusst ungeprueften) FKs."""
        user = User.objects.create_user(
            username=username,
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
        )
        user.set_password("testpass123")
        user.save()
        return user

    def test_raw_delete_of_legalhold_creator_is_restricted(self, facility, sample_event):
        creator = self._lone_user(facility, "lonehold_creator")
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="DB-Level-Schutz",
            created_by=creator,
        )
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM core_user WHERE id = %s", [str(creator.pk)])
        assert User.objects.filter(pk=creator.pk).exists(), (
            "Raw-SQL-DELETE hat den LegalHold-Ersteller trotz DB-ON-DELETE-RESTRICT entfernt."
        )

    def test_raw_delete_of_workitem_creator_sets_null(self, facility):
        creator = self._lone_user(facility, "lonework_creator")
        workitem = WorkItem.objects.create(
            facility=facility,
            created_by=creator,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="DB-Level-Test-Aufgabe",
        )
        with connection.cursor() as cur:
            cur.execute("DELETE FROM core_user WHERE id = %s", [str(creator.pk)])
        workitem.refresh_from_db()
        assert workitem.created_by_id is None, (
            "Raw-SQL-DELETE des WorkItem-Erstellers hat created_by nicht auf DB-Ebene NULL gesetzt."
        )

    def test_raw_delete_of_deletionrequest_requester_sets_null(self, facility, sample_event):
        requester = self._lone_user(facility, "lonedeletion_requester")
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="DB-Level-Test-Loeschantrag",
            requested_by=requester,
        )
        with connection.cursor() as cur:
            cur.execute("DELETE FROM core_user WHERE id = %s", [str(requester.pk)])
        dr.refresh_from_db()
        assert dr.requested_by_id is None, (
            "Raw-SQL-DELETE des DeletionRequest-Antragstellers hat requested_by nicht auf DB-Ebene NULL gesetzt."
        )
