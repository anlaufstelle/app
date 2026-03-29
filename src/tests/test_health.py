"""Tests für den Health-Endpoint."""

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
