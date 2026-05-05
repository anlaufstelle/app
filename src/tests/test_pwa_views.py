"""Tests für ServiceWorkerView und ManifestView (core/views/pwa.py).

Covers: Happy-Path (200 + korrekter Content-Type + Scope-Header),
FileNotFoundError-Pfad (404), und die @lru_cache-Idempotenz.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.fixture(autouse=True)
def _clear_caches():
    """lru_cache zwischen Tests leeren, damit FileNotFoundError-Branch wieder greift."""
    from core.views.pwa import _read_manifest, _read_service_worker

    _read_service_worker.cache_clear()
    _read_manifest.cache_clear()
    yield
    _read_service_worker.cache_clear()
    _read_manifest.cache_clear()


@pytest.mark.django_db
class TestServiceWorkerView:
    def test_returns_sw_js_with_correct_headers(self, client):
        response = client.get(reverse("service_worker"))

        assert response.status_code == 200
        assert response["content-type"].startswith("application/javascript")
        assert response["Service-Worker-Allowed"] == "/"
        # Mindestens ein erwartetes SW-Schlüsselwort im Body
        body = response.content.decode()
        assert "CACHE_NAME" in body or "addEventListener" in body

    def test_returns_404_when_file_missing(self, client):
        with patch("core.views.pwa._read_service_worker", side_effect=FileNotFoundError):
            response = client.get(reverse("service_worker"))
        assert response.status_code == 404

    def test_public_access(self, client):
        """Kein Login, kein CSRF — SW muss public sein, sonst greift kein Browser."""
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestOfflineFallbackView:
    """Refs #701: Service-Worker liefert /offline/ als App-Shell-Fallback
    bei Navigation-Requests ohne Cache- und Netz-Hit.
    """

    def test_returns_offline_template(self, client):
        response = client.get(reverse("offline_fallback"))
        assert response.status_code == 200
        assert response["content-type"].startswith("text/html")
        body = response.content.decode()
        # Inline-CSS muss enthalten sein — Template hat kein /static/-Lookup.
        assert "<style>" in body
        # Sprachneutral mit DE-Default; Marker-String aus Template.
        assert "offline" in body.lower()

    def test_public_access(self, client):
        """Offline-Page muss ohne Login + ohne CSRF erreichbar sein."""
        response = client.get(reverse("offline_fallback"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestServiceWorkerCachesOfflineFallback:
    """Service-Worker pre-cached /offline/ im APP_SHELL-Array."""

    def test_sw_includes_offline_in_app_shell(self, client):
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "/offline/" in body, "/offline/ muss im APP_SHELL stehen, sonst greift der Fallback nicht."
        assert 'CACHE_NAME = "anlaufstelle-v8"' in body, "CACHE_NAME muss bei APP_SHELL-Aenderung gebumpt sein."


@pytest.mark.django_db
class TestManifestView:
    def test_returns_manifest_with_correct_content_type(self, client):
        response = client.get(reverse("manifest"))

        assert response.status_code == 200
        assert response["content-type"].startswith("application/manifest+json")
        # Manifest ist JSON
        body = response.content.decode()
        assert body.strip().startswith("{")

    def test_returns_404_when_file_missing(self, client):
        with patch("core.views.pwa._read_manifest", side_effect=FileNotFoundError):
            response = client.get(reverse("manifest"))
        assert response.status_code == 404

    def test_public_access(self, client):
        """Manifest muss public sein (PWA-Install-Prompt-Standard)."""
        response = client.get(reverse("manifest"))
        assert response.status_code == 200
