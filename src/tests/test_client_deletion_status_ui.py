"""Sichtbarkeit des laufenden Personen-Löschantrags (Refs #1736).

Der Vier-Augen-Antrag war bisher nirgends am Datensatz sichtbar: Detail- und
Listenansicht zeigten nichts, „Löschen beantragen" blieb aktiv, und ein
zweiter Klick meldete erneut Erfolg, obwohl der Service idempotent nichts tat
und die neu eingegebene Begründung still verwarf.
"""

import pytest
from django.urls import reverse

from core.models.workitem import DeletionRequest
from core.services.client import request_client_deletion

pytestmark = pytest.mark.django_db


class TestClientDetailPendingBanner:
    def test_context_carries_pending_request(self, client, staff_user, client_identified):
        dr = request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        assert response.status_code == 200
        assert response.context["pending_deletion_request"] == dr

    def test_context_none_without_request(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        assert response.context["pending_deletion_request"] is None

    def test_banner_names_requester_and_is_rendered(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(staff_user)
        body = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "client-deletion-pending-banner" in body
        assert staff_user.username in body

    def test_badge_shown_in_header(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(staff_user)
        body = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "client-deletion-pending-badge" in body

    def test_no_banner_without_pending_request(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        body = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "client-deletion-pending-banner" not in body

    def test_rejected_request_does_not_count_as_pending(self, client, staff_user, lead_user, client_identified):
        from core.services.client import reject_client_deletion

        dr = request_client_deletion(client_identified, staff_user, "Widerruf")
        reject_client_deletion(dr, lead_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        assert response.context["pending_deletion_request"] is None


class TestClientDetailDeleteAction:
    def test_request_link_visible_without_pending_request(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        body = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "client-delete-request-btn" in body
        assert "client-delete-request-pending" not in body

    def test_request_link_replaced_while_pending(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(staff_user)
        body = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "client-delete-request-btn" not in body
        assert "client-delete-request-pending" in body


class TestClientListBadge:
    def test_list_marks_client_with_pending_request(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(staff_user)
        body = client.get(reverse("core:client_list")).content.decode()
        assert "client-row-deletion-pending-badge" in body

    def test_list_without_pending_request_has_no_badge(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        body = client.get(reverse("core:client_list")).content.decode()
        assert "client-row-deletion-pending-badge" not in body


class TestSecondRequestIsHonest:
    """Zweiter Klick auf „Löschen beantragen": kein Fake-Erfolg mehr."""

    def test_get_shows_pending_state_instead_of_form(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Erster Grund")
        client.force_login(staff_user)
        response = client.get(reverse("core:client_delete_request", args=[client_identified.pk]))
        assert response.status_code == 200
        body = response.content.decode()
        assert "delete-request-already-pending" in body
        assert 'name="reason"' not in body

    def test_post_does_not_report_success_again(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Erster Grund")
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_delete_request", args=[client_identified.pk]),
            {"reason": "Zweiter Grund"},
        )
        assert response.status_code == 302
        msgs = [m.message for m in response.wsgi_request._messages]
        assert not any("Löschantrag gestellt" in str(m) for m in msgs)
        assert any("läuft bereits" in str(m) for m in msgs)

    def test_post_keeps_the_original_request_and_reason(self, client, staff_user, client_identified):
        dr = request_client_deletion(client_identified, staff_user, "Erster Grund")
        client.force_login(staff_user)
        client.post(
            reverse("core:client_delete_request", args=[client_identified.pk]),
            {"reason": "Zweiter Grund"},
        )
        assert DeletionRequest.objects.filter(target_id=client_identified.pk).count() == 1
        dr.refresh_from_db()
        assert dr.reason == "Erster Grund"

    def test_first_request_still_reports_success(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_delete_request", args=[client_identified.pk]),
            {"reason": "Erster Grund"},
        )
        msgs = [str(m.message) for m in response.wsgi_request._messages]
        assert any("Löschantrag gestellt" in m for m in msgs)
