"""Art.-15-Auskunft: Fall-Löschanträge und DeletionRequest-Auditzeilen (Refs #2048).

Zwei getrennte Befunde am selben Vorgang:

1. **Der Antrag selbst.** ``_gather_deletion_requests`` filterte urspruenglich
   nur ``EVENT``/``CLIENT``-Ziele, obwohl ADR-044 ``TargetType.CASE``
   nachgereicht hat — der Fall-Loeschantrag samt Antragsgrund fehlte in der
   eigenen Auskunft. Der gemeinsame Ziel-Builder
   (``client_and_case_targets_q``) hat das mit
   #2071 geschlossen;
   die Tests hier pinnen den Zustand, damit er nicht erneut wegdriftet — genau
   diese Drift zwischen Art.-15- und Art.-17-Zielmenge war der Befund.

2. **Die Auditzeilen.** ``DELETION_REQUESTED``/``APPROVED``/``REJECTED`` setzen
   ``target_id`` auf die **Antrags**-PK (nicht auf Person, Fall oder Ereignis)
   — genau wie der Art.-16-Workflow seit
   #1796. Fuer die
   Berichtigung gibt es dafuer einen PK-Zweig in ``_gather_audit_trail``, fuer
   den Loeschantrag fehlte er: der gesamte Vier-Augen-Vorgang war im
   Zugriffsprotokoll der betroffenen Person unsichtbar, obwohl der Antrag
   selbst darin steht.
"""

import pytest

from core.models import AuditLog, DeletionRequest
from core.services.client import export_client_data, export_client_data_pdf

CASE_REASON = "Fall irrtuemlich fuer die falsche Person angelegt"
CLIENT_REASON = "Betroffene verlangt Loeschung der gesamten Akte"


def _audit_actions(data):
    return [row["action"] for row in data["audit_trail"]]


def _audit_categories(data):
    return {row["category"] for row in data["audit_trail"]}


# ---------------------------------------------------------------------------
# (1) Der Fall-Löschantrag in JSON und PDF
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseDeletionRequestInExport:
    def test_json_contains_the_case_request_with_reason(self, facility, client_identified, case_open, staff_user):
        from core.services.case.deletion import request_case_deletion

        request_case_deletion(case_open, staff_user, CASE_REASON)
        data = export_client_data(client_identified, facility, staff_user)
        reasons = [row["reason"] for row in data["deletion_requests"]]
        assert CASE_REASON in reasons

    def test_pdf_contains_the_case_request_with_reason(self, facility, client_identified, case_open, admin_user):
        """Cross-Review-Befund 6: geprueft wird der gerenderte INHALT.

        Die frühere Fassung endete bei ``%PDF`` und Mindestgroesse — sie waere
        auch gruen geblieben, wenn Fall-Antrag und Antragsgrund vollstaendig
        gefehlt haetten. Die HTML-Stufe ist die Schicht, an der eine
        Template-Regression entsteht; das Byte-Ergebnis wird zusaetzlich auf
        seinen Vertrag geprueft.
        """
        from core.services.case.deletion import request_case_deletion
        from core.services.client import render_client_export_html

        request_case_deletion(case_open, admin_user, CASE_REASON)
        html = render_client_export_html(client_identified, facility, admin_user)
        assert "Löschanträge" in html
        assert CASE_REASON in html

        pdf = export_client_data_pdf(client_identified, facility, admin_user)
        assert pdf[:4] == b"%PDF"
        # Zusaetzlich zur Inhalts-Assertion, nicht statt ihr: ``%PDF`` allein
        # akzeptierte auch einen abgeschnittenen oder leeren Rueckgabewert.
        assert len(pdf) > 1000

    def test_foreign_case_request_stays_out(self, facility, client_identified, case_open, staff_user, other_facility):
        """Ueber-Matching-Probe: der Antrag einer fremden Person darf nicht
        in dieser Auskunft auftauchen."""
        from core.models import Client

        other = Client.objects.create(facility=facility, pseudonym="Fremd-01", created_by=staff_user)
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=other.pk,
            reason="Fremder Antrag",
            requested_by=staff_user,
        )
        data = export_client_data(client_identified, facility, staff_user)
        assert "Fremder Antrag" not in [row["reason"] for row in data["deletion_requests"]]


# ---------------------------------------------------------------------------
# (2) Die DeletionRequest-Auditzeilen im Zugriffsprotokoll
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeletionAuditRowsInExport:
    def test_case_deletion_requested_row_appears(self, facility, client_identified, case_open, staff_user):
        from core.services.case.deletion import request_case_deletion

        request_case_deletion(case_open, staff_user, CASE_REASON)
        data = export_client_data(client_identified, facility, staff_user)
        assert AuditLog.Action.DELETION_REQUESTED.label in _audit_actions(data)

    def test_client_deletion_requested_row_appears(self, facility, client_identified, staff_user):
        from core.services.client import request_client_deletion

        request_client_deletion(client_identified, staff_user, CLIENT_REASON)
        data = export_client_data(client_identified, facility, staff_user)
        assert AuditLog.Action.DELETION_REQUESTED.label in _audit_actions(data)

    def test_rejected_row_appears(self, facility, client_identified, case_open, staff_user, lead_user):
        from core.services.case.deletion import reject_case_deletion, request_case_deletion

        dr = request_case_deletion(case_open, staff_user, CASE_REASON)
        reject_case_deletion(dr, lead_user)
        data = export_client_data(client_identified, facility, staff_user)
        actions = _audit_actions(data)
        assert AuditLog.Action.DELETION_REQUESTED.label in actions
        assert AuditLog.Action.DELETION_REJECTED.label in actions

    def test_approved_row_appears(self, facility, client_identified, case_open, staff_user, admin_user):
        """Der dritte Workflow-Schritt — bis hierher deckten die Tests nur
        REQUESTED und REJECTED ab (Cross-Review-Befund 6)."""
        from core.services.case.deletion import approve_case_deletion, request_case_deletion

        dr = request_case_deletion(case_open, staff_user, CASE_REASON)
        approve_case_deletion(dr, admin_user)
        data = export_client_data(client_identified, facility, staff_user)
        actions = _audit_actions(data)
        assert AuditLog.Action.DELETION_REQUESTED.label in actions
        assert AuditLog.Action.DELETION_APPROVED.label in actions

    def test_rows_carry_a_readable_category(self, facility, client_identified, case_open, staff_user):
        from core.services.case.deletion import request_case_deletion

        request_case_deletion(case_open, staff_user, CASE_REASON)
        data = export_client_data(client_identified, facility, staff_user)
        assert "Löschantrag" in _audit_categories(data)

    def test_foreign_deletion_audit_row_stays_out(self, facility, client_identified, staff_user):
        """Der PK-Zweig darf nur Antraege dieser Person ziehen."""
        from core.models import Client
        from core.services.client import request_client_deletion

        other = Client.objects.create(facility=facility, pseudonym="Fremd-02", created_by=staff_user)
        request_client_deletion(other, staff_user, "Fremder Antrag")
        data = export_client_data(client_identified, facility, staff_user)
        assert AuditLog.Action.DELETION_REQUESTED.label not in _audit_actions(data)
