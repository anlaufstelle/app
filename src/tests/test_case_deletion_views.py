"""Oberflaeche des Fall-Loeschwegs (Refs #1818, ADR-044).

Gegenstueck zu ``test_client_deletion_workflow.py``/``test_client_anonymize_now.py``
auf der View-Ebene. Geprueft wird, was die Services NICHT entscheiden koennen:
welcher der beiden Wege der Nutzerin angeboten wird (P1c), was die Seite dabei
sagt, wo die Papierkorb-Grenze in der Oberflaeche verlaeuft und dass der
zentrale Review-Einstieg den dritten Zieltyp wirklich dispatcht.
"""

import pytest
from django.urls import reverse

from core.models import AuditLog, Case, Client, DeletionRequest
from core.services.case import request_case_deletion, soft_delete_case

pytestmark = pytest.mark.django_db


@pytest.fixture
def qualified_case(facility, staff_user):
    """Fall einer Person der Stufe „Mit Zusatzangaben" — Vier-Augen-Weg (P1c)."""
    client = Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym="Q-4711",
        created_by=staff_user,
    )
    return Case.objects.create(
        facility=facility,
        client=client,
        title="Qualifizierter Fall",
        status=Case.Status.OPEN,
        created_by=staff_user,
    )


class TestDeleteRequestPageShowsTheApplicableWay:
    """Der Ausgang haengt an der Kontaktstufe — die Seite sagt ihn VOR dem Klick."""

    def test_direct_way_is_announced_for_a_non_qualified_person(self, client, staff_user, case_open):
        client.force_login(staff_user)
        body = client.get(reverse("core:case_delete_request", args=[case_open.pk])).content.decode()
        assert 'data-testid="case-delete-direct-hint"' in body
        assert 'data-testid="case-delete-four-eyes-hint"' not in body
        # Ohne Vier-Augen gibt es keine Begruendung zu erfassen.
        assert 'name="reason"' not in body

    def test_four_eyes_way_is_announced_for_a_qualified_person(self, client, staff_user, qualified_case):
        client.force_login(staff_user)
        body = client.get(reverse("core:case_delete_request", args=[qualified_case.pk])).content.decode()
        assert 'data-testid="case-delete-four-eyes-hint"' in body
        assert 'name="reason"' in body

    def test_missing_confirmer_is_flagged_before_submitting(self, client, lead_user, qualified_case, facility):
        """Setup-Guard (Refs #1053): ein Antrag ohne moeglichen Genehmiger blieb liegen."""
        from core.models import User

        User.objects.filter(facility=facility).exclude(pk=lead_user.pk).update(can_confirm_deletion=False)
        lead_user.can_confirm_deletion = True
        lead_user.save(update_fields=["can_confirm_deletion"])
        client.force_login(lead_user)
        body = client.get(reverse("core:case_delete_request", args=[qualified_case.pk])).content.decode()
        assert 'data-testid="case-delete-no-confirmer"' in body

    def test_trashed_case_is_not_reachable(self, client, staff_user, case_open):
        soft_delete_case(case_open, staff_user)
        client.force_login(staff_user)
        assert client.get(reverse("core:case_delete_request", args=[case_open.pk])).status_code == 404


class TestDeleteRequestPost:
    def test_non_qualified_person_goes_straight_to_the_trash(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.post(reverse("core:case_delete_request", args=[case_open.pk]))
        assert response.status_code == 302
        assert response.url == reverse("core:case_list")

        case_open.refresh_from_db()
        assert case_open.is_deleted is True
        assert case_open.deleted_by_id == staff_user.pk
        assert not DeletionRequest.objects.filter(target_type=DeletionRequest.TargetType.CASE).exists()

    def test_qualified_person_creates_a_request_instead(self, client, staff_user, qualified_case):
        client.force_login(staff_user)
        client.post(reverse("core:case_delete_request", args=[qualified_case.pk]), {"reason": "Doppelt angelegt"})

        qualified_case.refresh_from_db()
        assert qualified_case.is_deleted is False
        dr = DeletionRequest.objects.get(target_type=DeletionRequest.TargetType.CASE, target_id=qualified_case.pk)
        assert dr.reason == "Doppelt angelegt"
        assert dr.status == DeletionRequest.Status.PENDING

    def test_missing_reason_on_the_four_eyes_way_is_refused(self, client, staff_user, qualified_case):
        client.force_login(staff_user)
        response = client.post(reverse("core:case_delete_request", args=[qualified_case.pk]), {"reason": "   "})
        assert response.url == reverse("core:case_delete_request", args=[qualified_case.pk])
        assert not DeletionRequest.objects.filter(target_type=DeletionRequest.TargetType.CASE).exists()

    def test_second_request_reports_the_idempotent_truth(self, client, staff_user, qualified_case):
        """Der Service legt nichts an — ohne diese Meldung meldete die View Erfolg."""
        request_case_deletion(qualified_case, staff_user, "Erster Grund")
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_delete_request", args=[qualified_case.pk]),
            {"reason": "Zweiter Grund"},
            follow=True,
        )
        meldungen = [m.message for m in response.context["messages"]]
        assert any("bereits ein Löschantrag" in str(m) for m in meldungen)
        dr = DeletionRequest.objects.get(target_type=DeletionRequest.TargetType.CASE)
        assert dr.reason == "Erster Grund"

    def test_pending_request_disables_the_action_and_shows_the_badge(self, client, staff_user, qualified_case):
        request_case_deletion(qualified_case, staff_user, "Grund")
        client.force_login(staff_user)
        body = client.get(reverse("core:case_detail", args=[qualified_case.pk])).content.decode()
        assert 'data-testid="case-deletion-pending-badge"' in body
        assert 'data-testid="case-deletion-pending-banner"' in body
        assert 'data-testid="case-delete-request-pending"' in body
        assert 'data-testid="case-delete-request-btn"' not in body

    def test_list_row_carries_the_pending_badge(self, client, staff_user, qualified_case):
        request_case_deletion(qualified_case, staff_user, "Grund")
        client.force_login(staff_user)
        body = client.get(reverse("core:case_list")).content.decode()
        assert 'data-testid="case-row-deletion-pending-badge"' in body


class TestReviewDispatchesTheCaseTarget:
    """Der zentrale Review-Einstieg kennt drei Zieltypen, nicht zwei."""

    def test_get_renders_the_case_review_page(self, client, staff_user, lead_user, qualified_case):
        dr = request_case_deletion(qualified_case, staff_user, "Doppelt angelegt")
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_review", args=[dr.pk]))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Fall-Löschantrag prüfen" in body
        assert qualified_case.title in body

    def test_approve_moves_the_case_to_the_trash(self, client, staff_user, lead_user, qualified_case):
        dr = request_case_deletion(qualified_case, staff_user, "Doppelt angelegt")
        client.force_login(lead_user)
        client.post(reverse("core:deletion_review", args=[dr.pk]), {"action": "approve"})

        qualified_case.refresh_from_db()
        dr.refresh_from_db()
        assert qualified_case.is_deleted is True
        assert dr.status == DeletionRequest.Status.APPROVED

    def test_reject_leaves_the_case_alone(self, client, staff_user, lead_user, qualified_case):
        dr = request_case_deletion(qualified_case, staff_user, "Doppelt angelegt")
        client.force_login(lead_user)
        client.post(reverse("core:deletion_review", args=[dr.pk]), {"action": "reject"})

        qualified_case.refresh_from_db()
        dr.refresh_from_db()
        assert qualified_case.is_deleted is False
        assert dr.status == DeletionRequest.Status.REJECTED

    def test_case_target_is_labelled_in_the_request_list(self, client, staff_user, lead_user, qualified_case):
        request_case_deletion(qualified_case, staff_user, "Doppelt angelegt")
        client.force_login(lead_user)
        body = client.get(reverse("core:deletion_request_list")).content.decode()
        assert qualified_case.title in body

    def test_assistant_confirmer_sees_no_case_title(self, client, assistant_user, staff_user, qualified_case):
        """``can_confirm_deletion`` ist rollenunabhaengig — Faelle sind es nicht.

        Fuer die Assistenz ist die Fall-Domaene durchgaengig geschlossen
        (``case_detail`` ist STAFF_PLUS). Der Freitext-Titel und ein
        ``case_detail``-Link waeren hier eine Gate-Umgehung.
        """
        assistant_user.can_confirm_deletion = True
        assistant_user.save(update_fields=["can_confirm_deletion"])
        request_case_deletion(qualified_case, staff_user, "Doppelt angelegt")
        client.force_login(assistant_user)
        body = client.get(reverse("core:deletion_request_list")).content.decode()
        assert qualified_case.title not in body
        assert reverse("core:case_detail", args=[qualified_case.pk]) not in body

    def test_assistant_confirmer_sees_no_case_title_on_the_review_page(
        self, client, assistant_user, staff_user, qualified_case
    ):
        """Dasselbe Gate wie in der Liste — einen Klick weiter (Refs #1818).

        ``annotate_deletion_targets`` haelt der Assistenz den Falltitel fern,
        die Pruefseite lag hinter demselben ``DeletionConfirmerRequiredMixin``
        aber ungegated dahinter: der „Pruefen"-Link steht fuer jede
        ``can_confirm_deletion``-Traegerin in der Liste. Ohne Gate bekaeme sie
        dort genau den Freitext-Titel und das Pseudonym, die ihr die
        Fall-Domaene (``case_detail`` ist STAFF_PLUS) verwehrt.
        """
        assistant_user.can_confirm_deletion = True
        assistant_user.save(update_fields=["can_confirm_deletion"])
        dr = request_case_deletion(qualified_case, staff_user, "Doppelt angelegt")
        client.force_login(assistant_user)

        response = client.get(reverse("core:deletion_review", args=[dr.pk]))

        assert response.status_code == 200
        body = response.content.decode()
        assert qualified_case.title not in body
        assert qualified_case.client.pseudonym not in body
        assert 'data-testid="case-review-not-visible"' in body
        # Die Entscheidung bleibt moeglich — nur der Inhalt ist zu.
        assert 'value="approve"' in body

    def test_staff_confirmer_still_sees_the_case_summary(self, client, staff_user, lead_user, qualified_case):
        dr = request_case_deletion(qualified_case, staff_user, "Doppelt angelegt")
        client.force_login(lead_user)
        body = client.get(reverse("core:deletion_review", args=[dr.pk])).content.decode()
        assert qualified_case.title in body
        assert 'data-testid="case-review-not-visible"' not in body


class TestTrashView:
    def test_lists_trashed_cases_with_both_actions(self, client, admin_user, staff_user, case_open):
        soft_delete_case(case_open, staff_user)
        client.force_login(admin_user)
        body = client.get(reverse("core:case_trash")).content.decode()
        assert case_open.title in body
        assert 'data-testid="case-restore-btn"' in body
        assert 'data-testid="case-anonymize-now-btn"' in body
        assert 'data-testid="case-trash-anonymized-badge"' not in body

    def test_anonymized_case_offers_no_restore(self, client, admin_user, staff_user, case_open):
        """„Wiederherstellen" holte sonst eine inhaltsleere Huelle zurueck."""
        from core.services.case import anonymize_case_now

        soft_delete_case(case_open, staff_user)
        anonymize_case_now(case_open, admin_user)
        client.force_login(admin_user)
        body = client.get(reverse("core:case_trash")).content.decode()
        assert 'data-testid="case-trash-anonymized-badge"' in body
        assert 'data-testid="case-restore-btn"' not in body
        assert 'data-testid="case-anonymize-now-btn"' not in body

    def test_case_without_a_recorded_deleter_still_renders(self, client, admin_user, case_open):
        """Automated or account-less removals leave ``deleted_by`` empty."""
        case_open.soft_delete()
        client.force_login(admin_user)

        response = client.get(reverse("core:case_trash"))

        assert response.status_code == 200
        assert case_open.title in response.content.decode()

    def test_live_cases_do_not_appear(self, client, admin_user, case_open):
        client.force_login(admin_user)
        assert case_open.title not in client.get(reverse("core:case_trash")).content.decode()

    def test_list_offers_the_trash_only_to_the_admin(self, client, admin_user, staff_user):
        client.force_login(admin_user)
        assert 'data-testid="case-trash-link"' in client.get(reverse("core:case_list")).content.decode()
        client.force_login(staff_user)
        assert 'data-testid="case-trash-link"' not in client.get(reverse("core:case_list")).content.decode()

    def test_staff_may_not_open_the_trash(self, client, staff_user):
        client.force_login(staff_user)
        assert client.get(reverse("core:case_trash")).status_code in (403, 404)


class TestRestoreAndAnonymizeViews:
    def test_restore_brings_the_case_back(self, client, admin_user, staff_user, case_open):
        soft_delete_case(case_open, staff_user)
        client.force_login(admin_user)
        response = client.post(reverse("core:case_restore", args=[case_open.pk]))
        assert response.url == reverse("core:case_detail", args=[case_open.pk])
        case_open.refresh_from_db()
        assert case_open.is_deleted is False

    def test_restore_of_a_live_case_is_a_404(self, client, admin_user, case_open):
        client.force_login(admin_user)
        assert client.post(reverse("core:case_restore", args=[case_open.pk])).status_code == 404

    def test_anonymize_redacts_and_reports(self, client, admin_user, staff_user, case_open):
        soft_delete_case(case_open, staff_user)
        client.force_login(admin_user)
        response = client.post(reverse("core:case_anonymize", args=[case_open.pk]), follow=True)
        case_open.refresh_from_db()
        assert "Anonymisiert" in case_open.title
        assert case_open.anonymized_at is not None
        assert any("endgültig anonymisiert" in str(m.message) for m in response.context["messages"])
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_ANONYMIZED, target_id=str(case_open.pk)).exists()

    def test_active_hold_surfaces_as_a_message(self, client, admin_user, staff_user, case_open, facility, lead_user):
        from core.models import LegalHold

        soft_delete_case(case_open, staff_user)
        LegalHold.objects.create(
            facility=facility,
            target_type="Case",
            target_id=case_open.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        client.force_login(admin_user)
        response = client.post(reverse("core:case_anonymize", args=[case_open.pk]), follow=True)
        assert any("Aufbewahrungssperre" in str(m.message) for m in response.context["messages"])
        case_open.refresh_from_db()
        assert "Anonymisiert" not in case_open.title


class TestConfigDeleteBlockerHasAWayOut:
    """Ein Fall im Papierkorb blockiert eine Konfigurationsloeschung (Refs #1737-Muster)."""

    def test_doctype_delete_dialog_links_to_the_case_trash(
        self, client, admin_user, staff_user, case_open, doc_type_case_form
    ):
        case_open.document_type = doc_type_case_form
        case_open.save(update_fields=["document_type", "data_json"])
        soft_delete_case(case_open, staff_user)

        client.force_login(admin_user)
        body = client.get(reverse("core:config_doctype_delete", args=[doc_type_case_form.pk])).content.decode()
        assert 'data-testid="config-delete-case-trash-count"' in body
        assert reverse("core:case_trash") in body

    def test_blocker_reason_names_the_trash_share(self, admin_user, staff_user, case_open, doc_type_case_form):
        from core.services.config import ConfigDeleteBlocked, delete_document_type

        case_open.document_type = doc_type_case_form
        case_open.save(update_fields=["document_type", "data_json"])
        soft_delete_case(case_open, staff_user)

        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(None, doc_type_case_form)
        assert any("Papierkorb" in reason for reason in exc.value.reasons)
