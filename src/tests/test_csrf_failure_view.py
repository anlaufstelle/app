"""Tests fuer die Custom-CSRF-Failure-View (Refs #699, #970)."""

from unittest.mock import patch

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

    def test_csrf_failure_logs_reason_and_request_context(self):
        """Refs #970: csrf_failure loggt WARNING mit reason + path + referer + origin + htmx.

        Revidiert #699-Entscheidung ("reason loggen wir nicht"): Production-Vorfaelle
        wie der CSRF-403-nach-Login-Bug sind ohne ``reason`` nicht diagnostizierbar.
        Der User sieht das Log nicht — es geht ausschliesslich an DevOps.

        Mocked Logger statt ``caplog``: Der ``django.security.csrf``-Logger erbt
        von ``django`` mit ``propagate=False`` (settings/base.py), daher fängt
        caplog am Root nichts ab.
        """
        client = Client(enforce_csrf_checks=True)
        with patch("core.views.errors.logger") as mock_logger:
            response = client.post(
                "/login/",
                {"username": "x", "password": "y"},
                HTTP_REFERER="https://example.test/somewhere/",
                HTTP_ORIGIN="https://example.test",
                HTTP_HX_REQUEST="true",
            )
        assert response.status_code == 403

        assert mock_logger.warning.call_count == 1
        fmt, *args = mock_logger.warning.call_args.args
        # Format-String prüfen
        assert "CSRF failure" in fmt
        assert "reason=%r" in fmt
        assert "path=%s" in fmt
        assert "referer=%s" in fmt
        assert "origin=%s" in fmt
        assert "htmx=%s" in fmt
        # Positional args: (reason, path, referer, origin, htmx, user)
        reason, path, referer, origin, htmx, user = args
        assert reason  # Django füllt reason mit "Origin checking failed - ..."
        assert path == "/login/"
        assert referer == "https://example.test/somewhere/"
        assert origin == "https://example.test"
        assert htmx is True
        assert user == "anonymous"
