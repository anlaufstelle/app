"""Rollen-Matrix + Sudo-Mode-Tests fuer AnlaufstelleAdminSite (Refs #785)."""

from __future__ import annotations

import time

import pytest
from django.test import override_settings


@pytest.mark.django_db
class TestAdminSiteRoleGate:
    """Mit SUDO_MODE_ENABLED=False (test-default) testen wir nur die Rollen-Logik."""

    def test_super_admin_can_access(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 200

    def test_facility_admin_can_access(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 200

    def test_lead_gets_login_redirect(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get("/admin-mgmt/", follow=False)
        # AdminSite redirected zu /admin-mgmt/login/ wenn has_permission False.
        assert response.status_code == 302
        assert "/admin-mgmt/login/" in response["Location"]

    def test_staff_gets_login_redirect(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 302
        assert "/admin-mgmt/login/" in response["Location"]

    def test_assistant_gets_login_redirect(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 302
        assert "/admin-mgmt/login/" in response["Location"]

    def test_anonymous_gets_login_redirect(self, client):
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 302
        assert "/admin-mgmt/login/" in response["Location"]


def _enter_sudo(client):
    """Simuliert eine frische Re-Auth via Sudo-Mode-View."""
    from core.services.security import SUDO_SESSION_KEY

    session = client.session
    session[SUDO_SESSION_KEY] = int(time.time()) + 3600
    session.save()


@pytest.mark.django_db
class TestAdminSiteSudoGate:
    """Mit SUDO_MODE_ENABLED=True testen wir, dass Sudo-Mode-Pflicht greift.

    ``override_settings`` muss pro Test-Funktion stehen — pytest-Klassen sind
    keine SimpleTestCase-Subklassen, deshalb funktioniert der Klassen-Decorator
    nicht.
    """

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_super_admin_without_sudo_redirects_to_sudo(self, client, super_admin_user):
        client.force_login(super_admin_user)
        # follow=True: erst zu /admin-mgmt/login/, dann redirected unser
        # login()-Override zu /sudo/?next=...
        response = client.get("/admin-mgmt/", follow=True)
        # End-URL muss /sudo/ sein:
        assert any("/sudo/" in url for url, _ in response.redirect_chain), (
            f"Erwartet Sudo-Redirect in der Chain, gefunden: {response.redirect_chain}"
        )

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_super_admin_with_sudo_can_access(self, client, super_admin_user):
        client.force_login(super_admin_user)
        _enter_sudo(client)
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 200

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_facility_admin_without_sudo_redirects_to_sudo(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get("/admin-mgmt/", follow=True)
        assert any("/sudo/" in url for url, _ in response.redirect_chain), (
            f"Erwartet Sudo-Redirect in der Chain, gefunden: {response.redirect_chain}"
        )

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_facility_admin_with_sudo_can_access(self, client, admin_user):
        client.force_login(admin_user)
        _enter_sudo(client)
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 200

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_lead_with_sudo_still_blocked(self, client, lead_user):
        """Sudo allein reicht nicht — Rolle muss stimmen."""
        client.force_login(lead_user)
        _enter_sudo(client)
        response = client.get("/admin-mgmt/", follow=False)
        assert response.status_code == 302
        assert "/admin-mgmt/login/" in response["Location"]
