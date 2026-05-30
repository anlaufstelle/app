"""AuditLog-Spur fuer den 4-Augen-Loeschungs-Workflow.

Refs Matrix DEV-DEL-06 (Welle 3 / Master #922) + DSGVO-Lift #932 (Welle 2).

Der DSGVO-Loeschungs-Workflow auf Events durchlaeuft drei Service-
Funktionen aus :mod:`core.services.events.deletion`:

- :func:`request_deletion` (legt ``DeletionRequest`` an)
- :func:`approve_deletion` (genehmigt + ruft :func:`soft_delete_event`)
- :func:`reject_deletion` (lehnt ab)

SOLL-Verhalten der AuditLog-Spur nach #932:

- **Request**: schreibt einen ``AuditLog.Action.DELETION_REQUESTED``
  mit ``target_type='DeletionRequest'`` und ``target_id=str(dr.pk)``.
  Idempotent: zweiter Request-Aufruf auf dasselbe Event erzeugt kein
  zweites Audit-Event.
- **Approve**: schreibt zwei Eintraege — (a) ``Action.DELETE`` mit
  ``target_type='Event'`` (via :func:`soft_delete_event`), (b)
  ``Action.DELETION_APPROVED`` mit ``target_type='DeletionRequest'``.
- **Reject**: schreibt einen ``Action.DELETION_REJECTED``-Eintrag mit
  ``target_type='DeletionRequest'``. Es entsteht KEIN
  ``Action.DELETE``-Eintrag auf das Event (nur Approve loescht).

Das gleiche Audit-Pattern gilt fuer ``request_client_deletion`` aus
:mod:`core.services.clients` (vgl. ``TestClientDeletionAudit``).
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event
from core.services.events.deletion import (
    approve_deletion,
    reject_deletion,
    request_deletion,
)


@pytest.fixture
def event_qualified(facility, client_qualified, doc_type_contact, staff_user):
    """Event auf einem QUALIFIED-Klienten (Voraussetzung fuer 4-Augen-Pfad)."""
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": "Qualifizierte Doku"},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestDeletionApprovalAudit:
    """Tests fuer AuditLog-Spur des 4-Augen-Loeschungs-Workflows."""

    def test_deletion_request_creation_writes_audit(self, event_qualified, staff_user):
        """SOLL: Anlage eines DeletionRequest schreibt einen AuditLog-Eintrag
        mit Action.DELETION_REQUESTED und target_type='DeletionRequest'.
        DSGVO Art. 5(2) Rechenschaftspflicht. Refs #932.
        """
        dr = request_deletion(event_qualified, staff_user, "DSGVO-Anfrage")

        audit = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        )
        assert audit.user == staff_user
        assert audit.facility == event_qualified.facility
        assert audit.detail["reason"] == "DSGVO-Anfrage"
        assert audit.detail["target_event"] == str(event_qualified.pk)

    def test_request_deletion_is_idempotent_no_duplicate_audit(self, event_qualified, staff_user):
        """Zweiter request_deletion-Aufruf fuer dasselbe Event darf KEIN
        zweites Audit-Event erzeugen (Idempotenz-Schutz, Refs #932).
        """
        dr1 = request_deletion(event_qualified, staff_user, "Erster Antrag")
        count_after_first = AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
        ).count()

        dr2 = request_deletion(event_qualified, staff_user, "Zweiter Versuch")

        assert dr2.pk == dr1.pk, "Idempotenz: gleicher DeletionRequest erwartet"
        count_after_second = AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
        ).count()
        assert count_after_second == count_after_first, (
            "Zweiter request_deletion-Aufruf darf kein neues Audit-Event erzeugen"
        )

    def test_approve_deletion_writes_audit(self, event_qualified, staff_user, lead_user):
        """approve_deletion schreibt ZWEI AuditLog-Eintraege:
        (a) Action.DELETE mit target_type='Event' (via soft_delete_event)
        (b) Action.DELETION_APPROVED mit target_type='DeletionRequest' (Refs #932)
        """
        # Setup: PENDING DeletionRequest existiert.
        dr = request_deletion(event_qualified, staff_user, "Klient bittet um Loeschung")
        assert dr.status == DeletionRequest.Status.PENDING

        before_delete = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_qualified.pk),
        ).count()

        approve_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == lead_user

        # (a) DELETE-Eintrag fuer das Event (via soft_delete_event):
        delete_audits = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_qualified.pk),
        )
        assert delete_audits.count() == before_delete + 1, (
            "approve_deletion muss genau einen DELETE-AuditLog produzieren."
        )
        delete_audit = delete_audits.order_by("-timestamp").first()
        assert delete_audit.user == lead_user
        assert delete_audit.facility == event_qualified.facility
        assert delete_audit.detail.get("document_type") == "Kontakt"

        # (b) DELETION_APPROVED-Eintrag fuer den DeletionRequest (Refs #932):
        approved = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_APPROVED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        )
        assert approved.user == lead_user
        assert approved.facility == event_qualified.facility
        assert approved.detail["target_event"] == str(event_qualified.pk)

    def test_reject_deletion_writes_audit(self, event_qualified, staff_user, lead_user):
        """reject_deletion schreibt einen Action.DELETION_REJECTED-Eintrag
        (Refs #932). Wichtig: KEIN Action.DELETE-Eintrag fuer das Event —
        nur Approve loescht das Event.
        """
        dr = request_deletion(event_qualified, staff_user, "Antrag wird abgelehnt")

        reject_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by == lead_user

        # SOLL: DELETION_REJECTED-Eintrag mit target=DeletionRequest:
        rejected = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_REJECTED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        )
        assert rejected.user == lead_user
        assert rejected.facility == event_qualified.facility
        assert rejected.detail["target_event"] == str(event_qualified.pk)

        # Negativ-Check: KEIN DELETE-Eintrag fuer das Event (nur Approve
        # loescht). Schuetzt davor, dass reject versehentlich auch
        # soft_delete_event aufruft.
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_qualified.pk),
        ).exists()


@pytest.mark.django_db
class TestClientDeletionAudit:
    """Tests fuer AuditLog-Spur des 4-Augen-Client-Loesch-Antrags (#932)."""

    def test_request_client_deletion_writes_audit(self, client_qualified, staff_user):
        """request_client_deletion schreibt einen Action.DELETION_REQUESTED
        mit target_type='DeletionRequest' (Refs #932).
        """
        from core.services.clients import request_client_deletion

        dr = request_client_deletion(client_qualified, staff_user, "DSGVO-Antrag Klient")

        audit = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        )
        assert audit.user == staff_user
        assert audit.facility == client_qualified.facility
        assert audit.detail["reason"] == "DSGVO-Antrag Klient"
        assert audit.detail["target_client"] == str(client_qualified.pk)

    def test_request_client_deletion_is_idempotent(self, client_qualified, staff_user):
        """Zweiter request_client_deletion-Aufruf erzeugt kein zweites Audit-Event."""
        from core.services.clients import request_client_deletion

        dr1 = request_client_deletion(client_qualified, staff_user, "Erst")
        count_after_first = AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
        ).count()

        dr2 = request_client_deletion(client_qualified, staff_user, "Zweit")

        assert dr2.pk == dr1.pk
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.DELETION_REQUESTED,
                target_type="DeletionRequest",
            ).count()
            == count_after_first
        )
