"""View-Smoke-Tests fuer die Rollenbezogene Arbeitszentrale (Refs #920)."""

from __future__ import annotations

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestRoleDashboardView:
    def _login(self, client, user):
        client.force_login(user)

    def test_staff_redirected_to_zeitstrom(self, client, staff_user):
        self._login(client, staff_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("core:zeitstrom")

    def test_lead_sees_lead_template(self, client, lead_user):
        self._login(client, lead_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200
        assert b"lead-dashboard-cards" in response.content
        # "Löschanträge" UTF-8-kodiert
        assert "Löschanträge".encode() in response.content

    def test_facility_admin_sees_admin_template(self, client, admin_user):
        self._login(client, admin_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200
        assert b"facility-admin-dashboard-cards" in response.content

    def test_super_admin_sees_super_admin_template(self, client, super_admin_user):
        self._login(client, super_admin_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200
        assert b"super-admin-dashboard-cards" in response.content

    def test_assistant_redirected_to_zeitstrom(self, client, assistant_user):
        self._login(client, assistant_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("core:zeitstrom")

    def test_unauthenticated_redirects_to_login(self, client):
        response = client.get(reverse("core:dashboard"))
        # Mixin sollte 302 zur Login-Page liefern
        assert response.status_code in (302, 403)


@pytest.mark.django_db
class TestSuperAdminDashboardAuditLinks:
    """Refs #1048: super_admin hat ``facility=None`` — ``core:audit_log``
    (``/audit/``) ist ueber ``FacilityAdminRequiredMixin`` gescopt und
    liefert fuer super_admin 403. Die Audit-Karten der
    System-Arbeitszentrale muessen deshalb auf die installationsweite
    View ``core:system_audit_list`` (``/system/audit/``) zeigen — die
    Zaehler im Kontext sind bereits installationsweit.
    """

    def test_audit_cards_link_to_system_audit(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200
        content = response.content.decode()
        assert reverse("core:system_audit_list") in content
        # Exakter href-Match: "/audit/" ist Substring von "/system/audit/".
        assert f'href="{reverse("core:audit_log")}"' not in content

    def test_audit_card_target_returns_200_for_super_admin(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 200
