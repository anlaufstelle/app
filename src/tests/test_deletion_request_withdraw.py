"""Selbst-Rueckzug eigener Loeschantraege (Refs #2040).

Ein versehentlich gestellter Loeschantrag war bis hierher eine Sackgasse: die
Antragstellerin sah ihn zwar (Refs #1736 B), konnte ihn aber nur ueber
Leitung/Genehmiger-Pool wieder loswerden. Der Rueckzug ist deshalb ein eigener,
auditierter Endstatus (``ApprovalStatus.WITHDRAWN``) — kein stilles Loeschen
der Antragszeile, weil der Vier-Augen-Vorgang nach Art. 5 Abs. 2 DSGVO
nachweisbar bleiben muss.

Die Guards, die diese Datei pinnt:

* **fremd → 404** (nicht 403): die Existenz eines fremden Antrags darf nicht
  ueber den Statuscode leaken.
* **eigen, aber nicht mehr PENDING → Redirect + Meldung, KEIN Audit-Eintrag**
  (das Rennen gegen eine parallele Genehmigung).
* **``reviewed_by`` bleibt NULL** — der CHECK-Constraint
  ``deletion_request_different_reviewer`` risse bei ``reviewed_by == requested_by``.
* **doppelter POST = genau EIN Audit-Eintrag** (``select_for_update`` +
  Status-Recheck, Muster ``reject_deletion``).
* **nach dem Rueckzug ist ein neuer Antrag moeglich** — der partielle
  Unique-Index ``unique_pending_deletion_request`` greift bei WITHDRAWN nicht.
"""

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event
from core.services.deletion import withdraw_deletion_request
from core.services.events import request_deletion

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_qualified(facility, client_qualified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": "Qualifiziert"},
        created_by=staff_user,
    )


@pytest.fixture
def own_request(event_qualified, staff_user):
    """PENDING-Antrag, gestellt von ``staff_user``."""
    return request_deletion(event_qualified, staff_user, "Versehentlich gestellt")


def _withdraw_audits(dr):
    return AuditLog.objects.filter(
        action=AuditLog.Action.DELETION_WITHDRAWN,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
    )


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# 1. Service
# ---------------------------------------------------------------------------


class TestWithdrawService:
    def test_sets_withdrawn_status_and_keeps_reviewer_null(self, own_request, staff_user):
        withdraw_deletion_request(own_request, staff_user)

        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.WITHDRAWN
        # Der CHECK-Constraint ``deletion_request_different_reviewer`` verbietet
        # ``reviewed_by == requested_by`` — der Zeitpunkt traegt die Information.
        assert own_request.reviewed_by is None
        assert own_request.reviewed_at is not None

    def test_writes_exactly_one_pii_free_audit_entry(self, own_request, staff_user, event_qualified):
        withdraw_deletion_request(own_request, staff_user)

        entries = list(_withdraw_audits(own_request))
        assert len(entries) == 1
        entry = entries[0]
        assert entry.user_id == staff_user.pk
        assert entry.facility_id == own_request.facility_id
        # Muster ``DELETION_REQUESTED``: nur die Ziel-UUID, kein Pseudonym und
        # keine Begruendung (Refs #1093 — ``reason`` lebt am DeletionRequest).
        assert entry.detail == {"target_event": str(event_qualified.pk)}

    def test_second_call_on_stale_object_raises_and_writes_no_second_audit(self, own_request, staff_user):
        """TOCTOU: der zweite Aufruf haelt eine veraltete PENDING-Kopie."""
        stale = DeletionRequest.objects.get(pk=own_request.pk)
        withdraw_deletion_request(own_request, staff_user)

        with pytest.raises(ValidationError):
            withdraw_deletion_request(stale, staff_user)

        assert _withdraw_audits(own_request).count() == 1

    def test_foreign_requester_is_rejected(self, own_request, lead_user):
        with pytest.raises(ValidationError):
            withdraw_deletion_request(own_request, lead_user)

        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING
        assert not _withdraw_audits(own_request).exists()

    def test_new_request_possible_after_withdraw(self, own_request, staff_user, event_qualified):
        withdraw_deletion_request(own_request, staff_user)

        neu = request_deletion(event_qualified, staff_user, "Doch loeschen")
        assert neu.pk != own_request.pk
        assert neu.status == DeletionRequest.Status.PENDING
        assert DeletionRequest.objects.filter(target_id=event_qualified.pk).count() == 2


# ---------------------------------------------------------------------------
# 2. View — Gate
# ---------------------------------------------------------------------------


class TestWithdrawViewGate:
    def test_foreign_request_is_404_not_403(self, client, own_request, lead_user):
        """403 wuerde die Existenz eines fremden Antrags verraten."""
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 404

        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 404
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING

    def test_cross_facility_is_404(self, client, own_request, second_facility_user):
        client.force_login(second_facility_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 404

    def test_unknown_pk_is_404(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": "00000000-0000-0000-0000-000000000000"}))
        assert response.status_code == 404

    def test_anonymous_is_redirected_to_login(self, client, own_request):
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 302
        assert "/login" in response.url


# ---------------------------------------------------------------------------
# 3. View — GET zeigt nur den Dialog
# ---------------------------------------------------------------------------


class TestWithdrawViewGet:
    def test_get_renders_consequence_dialog(self, client, own_request, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 200
        content = response.content.decode()
        assert "deletion-withdraw-confirm" in content
        # Konsequenztext: was passiert — und was NICHT passiert.
        assert "Vier-Augen" in content
        assert "Es wird nichts gelöscht" in content

    def test_get_changes_nothing(self, client, own_request, staff_user):
        client.force_login(staff_user)
        client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING
        assert not _withdraw_audits(own_request).exists()

    def test_get_on_already_decided_request_redirects_with_message(self, client, own_request, staff_user, lead_user):
        from core.services.events import reject_deletion

        reject_deletion(own_request, lead_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}), follow=False)

        assert response.status_code == 302
        assert _messages(response)
        assert not _withdraw_audits(own_request).exists()


# ---------------------------------------------------------------------------
# 4. View — POST
# ---------------------------------------------------------------------------


class TestWithdrawViewPost:
    def test_post_withdraws_and_audits_once(self, client, own_request, staff_user):
        client.force_login(staff_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 302
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.WITHDRAWN
        assert own_request.reviewed_by is None
        assert _withdraw_audits(own_request).count() == 1

    def test_double_post_is_idempotent(self, client, own_request, staff_user):
        client.force_login(staff_user)
        url = reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk})
        client.post(url)
        response = client.post(url)

        assert response.status_code == 302
        assert _withdraw_audits(own_request).count() == 1
        assert _messages(response)

    def test_post_on_approved_request_redirects_without_audit(self, client, own_request, staff_user, lead_user):
        from core.services.events import approve_deletion

        approve_deletion(own_request, lead_user)
        client.force_login(staff_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 302
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.APPROVED
        assert not _withdraw_audits(own_request).exists()
        assert _messages(response)

    def test_requester_without_other_access_lands_on_a_reachable_page(self, client, own_request, staff_user):
        """Refs #2040: ``can_access_deletion_requests`` faellt mit dem letzten
        eigenen PENDING-Antrag weg — ein blinder Redirect auf die Liste waere
        fuer die Antragstellerin ein 403 direkt nach der eigenen Aktion."""
        assert staff_user.can_access_deletion_requests is True
        client.force_login(staff_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}), follow=True)

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. Liste + Pruefansicht
# ---------------------------------------------------------------------------


class TestWithdrawUi:
    def test_list_offers_withdraw_only_on_own_pending_request(self, client, own_request, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_request_list"))
        content = response.content.decode()

        assert "deletion-withdraw-link" in content
        assert reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}) in content

    def test_list_hides_withdraw_on_foreign_request(self, client, own_request, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))

        assert "deletion-withdraw-link" not in response.content.decode()

    def test_confirmer_sees_withdrawn_end_status_in_list(self, client, own_request, staff_user, lead_user):
        withdraw_deletion_request(own_request, staff_user)
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        content = response.content.decode()

        assert "deletion-withdrawn-section" in content
        assert "Zurückgezogen" in content

    def test_review_get_on_withdrawn_shows_end_status_without_buttons(self, client, own_request, staff_user, lead_user):
        withdraw_deletion_request(own_request, staff_user)
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": own_request.pk}))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="approve"' not in content
        assert 'value="reject"' not in content
        assert "deletion-withdrawn-notice" in content

    def test_review_post_on_withdrawn_is_rejected_cleanly(self, client, own_request, staff_user, lead_user):
        withdraw_deletion_request(own_request, staff_user)
        client.force_login(lead_user)
        response = client.post(reverse("core:deletion_review", kwargs={"pk": own_request.pk}), {"action": "approve"})

        assert response.status_code == 302
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.WITHDRAWN
        assert _messages(response)


# ---------------------------------------------------------------------------
# 6. Wertelisten-Scan: der geteilte Enum-Wert darf den Art.-16-Pfad nicht
#    veraendern (``ApprovalStatus`` ist seit Refs #1834 geteilt).
# ---------------------------------------------------------------------------


class TestSharedEnumDoesNotLeakIntoRectification:
    def test_rectification_list_keeps_exactly_three_buckets(self, client, lead_user):
        """Der geteilte Enum-Wert darf im Art.-16-Pfad keine vierte Sektion erzeugen."""
        client.force_login(lead_user)
        response = client.get(reverse("core:rectification_list"))

        assert response.status_code == 200
        # ``.keys()`` ist hier NICHT redundant: ``response.context`` ist eine
        # ``ContextList``; ein ``in``/``for`` darauf iteriert die Kontexte, nicht
        # deren Schlüssel.
        buckets = {key for key in set(response.context.keys()) if key.endswith("_requests")}
        assert buckets == {"pending_requests", "approved_requests", "rejected_requests"}
        assert "Zurückgezogen" not in response.content.decode()

    def test_no_service_sets_withdrawn_on_a_rectification_request(self):
        """Quelltext-Guard: ``WITHDRAWN`` bleibt auf den Löschantrag beschränkt.

        Ein ``RectificationRequest.Status.WITHDRAWN`` wäre kein Syntaxfehler,
        sondern ein stiller Zustand ohne UI, ohne Audit-Aktion und ohne
        Rückweg — die Art.-16-Prüfansicht kennt ihn nicht.
        """
        from pathlib import Path

        quelle = Path(__file__).resolve().parents[1] / "core"
        treffer = [
            f"{pfad.relative_to(quelle)}:{nr}"
            for pfad in quelle.rglob("*.py")
            if "migrations" not in pfad.parts
            for nr, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1)
            if "RectificationRequest.Status.WITHDRAWN" in zeile
        ]
        assert treffer == []
