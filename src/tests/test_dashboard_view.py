"""View-Smoke-Tests fuer die Rollenbezogene Arbeitszentrale (Refs #920)."""

from __future__ import annotations

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestRoleDashboardView:
    def _login(self, client, user):
        client.force_login(user)

    def test_staff_sees_staff_template(self, client, staff_user):
        self._login(client, staff_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200
        assert b"Arbeitszentrale" in response.content
        assert b"staff-dashboard-cards" in response.content

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

    def test_assistant_sees_staff_template(self, client, assistant_user):
        self._login(client, assistant_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200
        assert b"staff-dashboard-cards" in response.content

    def test_unauthenticated_redirects_to_login(self, client):
        response = client.get(reverse("core:dashboard"))
        # Mixin sollte 302 zur Login-Page liefern
        assert response.status_code in (302, 403)
