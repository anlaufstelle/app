"""AuditLog-Spur fuer den 4-Augen-Loeschungs-Workflow.

Refs Matrix DEV-DEL-06 (Welle 3 / Master #922).

Der DSGVO-Loeschungs-Workflow auf Events durchlaeuft drei Service-
Funktionen aus :mod:`core.services.events.deletion`:

- :func:`request_deletion` (legt ``DeletionRequest`` an)
- :func:`approve_deletion` (genehmigt + ruft :func:`soft_delete_event`)
- :func:`reject_deletion` (lehnt ab)

IST-Verhalten der AuditLog-Spur:

- **Request**: schreibt *keinen* AuditLog (nur das DeletionRequest-
  Objekt landet in der DB). Pruefspur ergibt sich erst durch Approve
  oder direkten ``EventDeleteView``-Pfad.
- **Approve**: schreibt einen ``AuditLog.Action.DELETE`` ueber den
  intern aufgerufenen :func:`soft_delete_event` (in
  ``services/events/crud.py``).
- **Reject**: schreibt keinen AuditLog. Existierende Tests in
  ``test_deletion_requests.py`` decken das ab.

Tests dokumentieren den IST-Zustand 1:1 — Aufgaben sagen ausdruecklich
„Falls weniger Audits geschrieben werden als erwartet -> dokumentieren
(xfail), nicht Service aendern." Hier markiere ich die Request-Test-
Erwartung als xfail, weil eine vollstaendige DSGVO-Spur den Antrag
mitprotokollieren sollte (Rechenschaftspflicht), aktuell aber nicht tut.
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

    @pytest.mark.xfail(
        reason=(
            "IST-Verhalten: request_deletion schreibt keinen AuditLog-Eintrag "
            "(nur DeletionRequest-Row). Eine vollstaendige DSGVO-Pruefspur "
            "wuerde den Antrag mitprotokollieren — DEV-DEL-06 dokumentiert "
            "diese Luecke. Service bleibt unveraendert (Wave 3 nur Tests)."
        ),
        strict=True,
    )
    def test_deletion_request_creation_writes_audit(self, event_qualified, staff_user):
        """SOLL: Anlage eines DeletionRequest schreibt einen AuditLog-Eintrag
        (z.B. DELETE-Antrag oder eigene Action). IST: schreibt nichts —
        siehe Service-Body in ``services/events/deletion.py``.
        """
        before = AuditLog.objects.filter(facility=event_qualified.facility).count()

        request_deletion(event_qualified, staff_user, "DSGVO-Anfrage")

        after = AuditLog.objects.filter(facility=event_qualified.facility).count()
        assert after > before, (
            "Erwartet: mindestens ein AuditLog-Eintrag nach request_deletion. "
            "Aktuell schreibt der Service keinen — Luecke in der DSGVO-Spur."
        )

    def test_approve_deletion_writes_audit(self, event_qualified, staff_user, lead_user):
        """approve_deletion ruft intern soft_delete_event, das einen
        ``AuditLog.Action.DELETE``-Eintrag mit ``target_type='Event'``
        schreibt (siehe ``services/events/crud.py:267``).
        """
        # Setup: PENDING DeletionRequest existiert.
        dr = request_deletion(event_qualified, staff_user, "Klient bittet um Loeschung")
        assert dr.status == DeletionRequest.Status.PENDING

        before = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_qualified.pk),
        ).count()

        approve_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == lead_user

        # AuditLog.Action.DELETE ist geschrieben — geprueft wird Reviewer
        # als ``user``, weil ``soft_delete_event(event, reviewer)`` mit
        # dem Reviewer als ``user`` aufgerufen wird.
        audits = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_qualified.pk),
        )
        assert audits.count() == before + 1, "approve_deletion muss genau einen DELETE-AuditLog produzieren."
        audit = audits.order_by("-timestamp").first()
        assert audit.user == lead_user
        assert audit.facility == event_qualified.facility
        # detail enthaelt mindestens document_type — Service-Body in
        # crud.py:272 setzt es. Pseudonym kann None sein, wenn der Event
        # waehrend des Approves keinen Client mehr referenziert (defensive).
        assert audit.detail.get("document_type") == "Kontakt"

    def test_reject_deletion_writes_no_audit(self, event_qualified, staff_user, lead_user):
        """IST-Verhalten: reject_deletion schreibt *keinen* AuditLog-Eintrag.
        Das spiegelt das bestehende ``test_deletion_requests.py
        ::TestRejectDeletion::test_no_audit_log_on_reject``. Wenn die
        Pruefspur erweitert wird, muss dieser Test angepasst werden.
        """
        dr = request_deletion(event_qualified, staff_user, "Antrag wird abgelehnt")
        before = AuditLog.objects.filter(
            facility=event_qualified.facility,
            target_id=str(event_qualified.pk),
        ).count()

        reject_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by == lead_user

        after = AuditLog.objects.filter(
            facility=event_qualified.facility,
            target_id=str(event_qualified.pk),
        ).count()
        assert after == before, (
            "IST: reject_deletion schreibt keinen AuditLog. Falls geaendert: Erwartung anpassen und dokumentieren."
        )
