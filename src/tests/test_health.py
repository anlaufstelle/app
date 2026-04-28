"""Tests für den Health-Endpoint."""

from unittest.mock import patch

import pytest
from django.test import Client


@pytest.mark.django_db
class TestHealthEndpoint:
    """Health-Endpoint: 200, JSON-Format, kein Auth nötig."""

    def test_returns_200_and_json(self):
        client = Client()
        response = client.get("/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"
        assert data["version"] == "dev"

    def test_no_auth_required(self):
        client = Client()
        response = client.get("/health/")
        assert response.status_code == 200

    def test_content_type_is_json(self):
        client = Client()
        response = client.get("/health/")
        assert response["Content-Type"] == "application/json"

    def test_virus_scanner_disabled_by_default(self):
        """In Tests ist CLAMAV_ENABLED=False — der Scanner gilt als ``disabled``."""
        client = Client()
        response = client.get("/health/")
        data = response.json()
        assert data["virus_scanner"] == "disabled"

    def test_virus_scanner_connected_when_reachable(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=True):
            response = Client().get("/health/")
        data = response.json()
        assert data["virus_scanner"] == "connected"
        assert data["status"] == "ok"

    def test_virus_scanner_unavailable_degrades_status(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            response = Client().get("/health/")
        data = response.json()
        assert data["virus_scanner"] == "unavailable"
        assert data["status"] == "degraded"
