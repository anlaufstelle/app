"""Mutation-Pins v0.96.0 — Ziel-Treue des Lock-Lookups beim Antrags-Rueckzug.

``withdraw_deletion_request`` holt die zu sperrende Zeile ueber
``select_for_update().get(pk=…, facility_id=…)``. Beide Bestandteile dieses
Lookups hatten keine Zusage: ``test_deletion_request_withdraw.py`` arbeitet
durchgehend mit genau EINEM offenen Antrag je Einrichtung, und die
Fremd-Mandanten-Probe (``test_cross_facility_is_404``) prueft nur den
View-Pfad, nicht den Service.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import AuditLog, Client, DeletionRequest, DocumentType, Event
from core.services.deletion import withdraw_deletion_request
from core.services.events import request_deletion

pytestmark = pytest.mark.django_db


def _withdraw_audits(dr):
    return AuditLog.objects.filter(
        action=AuditLog.Action.DELETION_WITHDRAWN,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
    )


def _event(facility, client, document_type, user, *, note):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": note},
        created_by=user,
    )


class TestWithdrawLockLookup:
    def test_withdraws_exactly_the_passed_request(
        self, facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins x_withdraw_deletion_request__mutmut_4: ohne ``pk`` im Lookup
        entscheidet allein die Einrichtung, welche Zeile gesperrt und
        zurueckgezogen wird — bei zwei offenen Antraegen derselben Einrichtung
        traefe es irgendeine (bzw. ``MultipleObjectsReturned``)."""
        event_a = _event(facility, client_qualified, doc_type_contact, staff_user, note="Antrag A")
        event_b = _event(facility, client_qualified, doc_type_contact, staff_user, note="Antrag B")
        request_a = request_deletion(event_a, staff_user, "Versehentlich gestellt")
        request_b = request_deletion(event_b, lead_user, "Bleibt offen")
        assert request_a.pk != request_b.pk

        withdrawn = withdraw_deletion_request(request_a, staff_user)

        assert withdrawn.pk == request_a.pk
        request_a.refresh_from_db()
        assert request_a.status == DeletionRequest.Status.WITHDRAWN
        request_b.refresh_from_db()
        assert request_b.status == DeletionRequest.Status.PENDING
        assert _withdraw_audits(request_a).count() == 1
        assert not _withdraw_audits(request_b).exists()

    def test_lookup_is_scoped_to_the_facility_of_the_passed_request(
        self, facility, second_facility, second_facility_user
    ):
        """Pins x_withdraw_deletion_request__mutmut_5: ``facility_id`` im Lookup
        ist die Einrichtungsgrenze des Services selbst — faellt sie weg, laedt
        und beendet der Service eine Zeile allein anhand der PK, also auch
        eine, die gar nicht zur Einrichtung des uebergebenen Antrags gehoert.
        Dann haengt die Grenze allein an der DB-seitigen RLS."""
        foreign_type = DocumentType.objects.create(facility=second_facility, name="Fremder Kontakt")
        foreign_client = Client.objects.create(
            facility=second_facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            pseudonym="Fremd-QU-01",
            created_by=second_facility_user,
        )
        foreign_event = _event(
            second_facility, foreign_client, foreign_type, second_facility_user, note="Fremder Antrag"
        )
        foreign_request = request_deletion(foreign_event, second_facility_user, "Fremd gestellt")

        # Aufrufer-Sicht einer anderen Einrichtung auf dieselbe PK: der Lookup
        # muss an der Einrichtungsgrenze scheitern, nicht an der PK vorbeigehen.
        mismatched = DeletionRequest.objects.get(pk=foreign_request.pk)
        mismatched.facility_id = facility.pk
        # The requester now belongs to the facility the instance names, so the
        # facility check before the lock lets the call through (Refs #2374) and
        # only the scoped lookup keeps it away from the row of the other one.
        second_facility_user.facility = facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            withdraw_deletion_request(mismatched, second_facility_user)

        foreign_request.refresh_from_db()
        assert foreign_request.status == DeletionRequest.Status.PENDING
        assert not _withdraw_audits(foreign_request).exists()
