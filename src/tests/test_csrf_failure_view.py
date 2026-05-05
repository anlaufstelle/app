"""Tests fuer die Custom-CSRF-Failure-View (Refs #699)."""

import pytest
from django.test import Client


@pytest.mark.django_db
class TestCSRFFailureView:
    def test_csrf_failure_returns_custom_template(self):
        """POST ohne CSRF-Token → 403 + ``403_csrf.html``-Template."""
        # ``enforce_csrf_checks=True`` schaltet das Test-Default-Skip
        # fuer CSRF aus, sodass die echte Middleware greift.
        client = Client(enforce_csrf_checks=True)
        response = client.post("/login/", {"username": "x", "password": "y"})
        assert response.status_code == 403
        body = response.content.decode()
        # Marker aus dem Custom-Template — nicht in Djangos Default.
        assert "Sicherheits-Token abgelaufen" in body or "Token" in body
        # Reload-Button + Startseiten-Link
        assert "Seite neu laden" in body
        assert "Startseite" in body

    def test_csrf_failure_view_is_configured(self):
        """``CSRF_FAILURE_VIEW``-Setting zeigt auf unsere View."""
        from django.conf import settings

        assert settings.CSRF_FAILURE_VIEW == "core.views.errors.csrf_failure"
