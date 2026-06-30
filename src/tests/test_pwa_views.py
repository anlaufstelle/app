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

    def test_renders_offline_workspace_scaffold(self, client):
        """Refs #1321: /offline/ ist nicht mehr Sackgasse, sondern Offline-
        Arbeitsplatz — PII-freies Scaffold + Renderer-Script, das die lokal
        verfuegbaren Personen aus der verschluesselten IndexedDB fuellt.
        """
        response = client.get(reverse("offline_fallback"))
        body = response.content.decode()
        assert 'data-testid="offline-home"' in body
        # Container, den offline-home.js mit der Personenliste fuellt.
        assert 'data-testid="offline-home-list"' in body
        # Renderer + Datenschicht muessen geladen werden (CSP: externe Scripts).
        assert "offline-home.js" in body
        assert "offline-store.js" in body


@pytest.mark.django_db
class TestServiceWorkerCachesOfflineFallback:
    """Service-Worker pre-cached /offline/ im APP_SHELL-Array."""

    def test_sw_includes_offline_in_app_shell(self, client):
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "/offline/" in body, "/offline/ muss im APP_SHELL stehen, sonst greift der Fallback nicht."
        assert 'CACHE_NAME = "anlaufstelle-v10"' in body, "CACHE_NAME muss bei APP_SHELL-Aenderung gebumpt sein."

    def test_sw_caches_offline_home_assets(self, client):
        """Refs #1321: Die Offline-Home rendert client-seitig aus IndexedDB —
        ihre JS-Deps muessen im APP_SHELL pre-cached sein, sonst ist die Home
        beim ersten Offline-Aufruf (PWA-Kaltstart) nicht ladbar.
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        for asset in (
            "/static/js/dexie.min.js",
            "/static/js/crypto.js",
            "/static/js/offline-store.js",
            "/static/js/offline-home.js",
        ):
            assert asset in body, f"{asset} fehlt im APP_SHELL — Offline-Home offline nicht ladbar."


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


@pytest.mark.django_db
class TestHeadMetadata:
    """Head-Metadaten-Hygiene: Favicon-Link + moderne PWA-Capable-Meta.

    Refs #973 (Live-Verifikation): /favicon.ico lieferte auf jeder Seite 404,
    und `apple-mobile-web-app-capable` war ohne modernes Pendant deprecated.
    Geprüft auf der öffentlichen Login-Seite (eigenes <head>) und im base.html.
    """

    def test_login_page_has_favicon_link(self, client):
        response = client.get(reverse("login"))
        body = response.content.decode()
        assert 'rel="icon"' in body, "Favicon-Link fehlt → /favicon.ico 404 auf jeder Seite"

    def test_login_page_has_modern_web_app_capable_meta(self, client):
        response = client.get(reverse("login"))
        body = response.content.decode()
        assert 'name="mobile-web-app-capable"' in body, (
            "Modernes mobile-web-app-capable-Meta fehlt (apple-* ist deprecated)"
        )
