"""Tests fuer SudoMode (Refs #683)."""

import time

import pytest
from django.test import RequestFactory
from django.urls import reverse

from core.models import AuditLog
from core.services.sudo_mode import SUDO_SESSION_KEY, clear_sudo, enter_sudo, is_in_sudo


class TestSudoModeService:
    """Service-Level: enter/is_in/clear."""

    def _request_with_session(self):
        rf = RequestFactory()
        request = rf.get("/")

        class _Sess(dict):
            modified = False

        request.session = _Sess()
        return request

    def test_enter_sudo_sets_session_key(self):
        request = self._request_with_session()
        enter_sudo(request)
        assert SUDO_SESSION_KEY in request.session
        assert is_in_sudo(request)

    def test_is_in_sudo_false_without_key(self):
        request = self._request_with_session()
        assert is_in_sudo(request) is False

    def test_is_in_sudo_false_after_ttl(self):
        request = self._request_with_session()
        # TTL in der Vergangenheit setzen
        request.session[SUDO_SESSION_KEY] = int(time.time()) - 10
        assert is_in_sudo(request) is False

    def test_clear_sudo_removes_key(self):
        request = self._request_with_session()
        enter_sudo(request)
        clear_sudo(request)
        assert is_in_sudo(request) is False


@pytest.mark.django_db
class TestSudoModeViewGET:
    def test_get_renders_form(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("sudo_mode") + "?next=/clients/")
        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="password"' in body
        assert 'name="next"' in body


@pytest.mark.django_db
class TestSudoModeViewPOST:
    def test_correct_password_enters_sudo(self, client, admin_user):
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "next": "/clients/"},
        )
        assert response.status_code == 302
        assert response.url == "/clients/"
        # Session muss SudoMode-Key tragen
        assert SUDO_SESSION_KEY in client.session
        # AuditLog SUDO_MODE_ENTERED geschrieben
        assert AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_ENTERED,
        ).exists()

    def test_wrong_password_returns_403(self, client, admin_user):
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "WRONG", "next": "/clients/"},
        )
        assert response.status_code == 403
        assert SUDO_SESSION_KEY not in client.session

    def test_open_redirect_protection(self, client, admin_user):
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "next": "https://evil.example.com/"},
        )
        assert response.status_code == 302
        assert response.url == "/", "Externer Redirect muss auf '/' gefiltert werden."


@pytest.mark.django_db
class TestRequireSudoModeMixin:
    """Mixin redirected zu /sudo/?next= wenn nicht im SudoMode.

    SUDO_MODE_ENABLED ist in test.py auf False — diese Tests aktivieren
    es explizit per pytest-django ``settings``-Fixture.
    """

    def test_dsgvo_package_redirects_without_sudo(self, client, admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(admin_user)
        response = client.get(reverse("core:dsgvo_package"))
        assert response.status_code == 302
        assert "/sudo/" in response.url
        assert "next=" in response.url

    def test_dsgvo_package_passes_with_sudo(self, client, admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(admin_user)
        session = client.session
        session[SUDO_SESSION_KEY] = int(time.time()) + 900
        session.save()
        response = client.get(reverse("core:dsgvo_package"))
        assert response.status_code == 200
