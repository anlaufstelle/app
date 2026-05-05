"""Tests fuer MaintenanceModeMiddleware (Refs #700)."""

import os
import tempfile

import pytest
from django.test import RequestFactory, override_settings

from core.middleware.maintenance import MaintenanceModeMiddleware


@pytest.fixture
def flag_file():
    fd, path = tempfile.mkstemp(prefix="maintenance_test_", suffix=".flag")
    os.close(fd)
    os.unlink(path)  # Default: kein Flag, Test setzt es bei Bedarf.
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Klassen-Cache zwischen Tests leeren — sonst leakt State."""
    MaintenanceModeMiddleware._cache = None
    yield
    MaintenanceModeMiddleware._cache = None


class TestMaintenanceModeOff:
    """Default: kein Flag-File → Middleware ist No-Op."""

    @override_settings(MAINTENANCE_FLAG_FILE=None)
    def test_no_flag_file_passes_through(self):
        rf = RequestFactory()
        request = rf.get("/")
        sentinel = object()
        response = MaintenanceModeMiddleware(lambda r: sentinel)(request)
        assert response is sentinel

    def test_flag_file_missing_passes_through(self, flag_file):
        with override_settings(MAINTENANCE_FLAG_FILE=flag_file):
            rf = RequestFactory()
            request = rf.get("/")
            sentinel = object()
            response = MaintenanceModeMiddleware(lambda r: sentinel)(request)
        assert response is sentinel


class TestMaintenanceModeOn:
    """Flag-File existiert → 503 + Retry-After + Template."""

    def test_returns_503_with_retry_after(self, flag_file):
        open(flag_file, "w").close()
        with override_settings(MAINTENANCE_FLAG_FILE=flag_file, MAINTENANCE_RETRY_AFTER=42):
            rf = RequestFactory()
            request = rf.get("/clients/")
            response = MaintenanceModeMiddleware(lambda r: r)(request)
        assert response.status_code == 503
        assert response["Retry-After"] == "42"
        assert response["content-type"].startswith("text/html")

    def test_template_contains_maintenance_marker(self, flag_file):
        open(flag_file, "w").close()
        with override_settings(MAINTENANCE_FLAG_FILE=flag_file):
            rf = RequestFactory()
            response = MaintenanceModeMiddleware(lambda r: r)(rf.get("/clients/"))
        body = response.content.decode()
        assert "503" in body
        assert "Wartung" in body  # Marker aus 503.html


class TestMaintenanceModeWhitelist:
    """Health-Check, Static-Assets und Whitelist-IPs muessen durchkommen."""

    def test_health_endpoint_passes_through(self, flag_file):
        open(flag_file, "w").close()
        with override_settings(MAINTENANCE_FLAG_FILE=flag_file):
            rf = RequestFactory()
            request = rf.get("/health/")
            sentinel = object()
            response = MaintenanceModeMiddleware(lambda r: sentinel)(request)
        assert response is sentinel, "Health-Check muss auch im Wartungsmodus erreichbar sein."

    def test_static_assets_pass_through(self, flag_file):
        open(flag_file, "w").close()
        with override_settings(MAINTENANCE_FLAG_FILE=flag_file):
            rf = RequestFactory()
            request = rf.get("/static/css/styles.css")
            sentinel = object()
            response = MaintenanceModeMiddleware(lambda r: sentinel)(request)
        assert response is sentinel

    def test_whitelisted_ip_passes_through(self, flag_file):
        open(flag_file, "w").close()
        with override_settings(
            MAINTENANCE_FLAG_FILE=flag_file,
            MAINTENANCE_ALLOW_IPS=["10.0.0.42"],
        ):
            rf = RequestFactory()
            request = rf.get("/clients/", HTTP_X_FORWARDED_FOR="10.0.0.42")
            sentinel = object()
            response = MaintenanceModeMiddleware(lambda r: sentinel)(request)
        assert response is sentinel

    def test_non_whitelisted_ip_blocked(self, flag_file):
        open(flag_file, "w").close()
        with override_settings(
            MAINTENANCE_FLAG_FILE=flag_file,
            MAINTENANCE_ALLOW_IPS=["10.0.0.42"],
        ):
            rf = RequestFactory()
            request = rf.get("/clients/", HTTP_X_FORWARDED_FOR="192.168.1.1")
            response = MaintenanceModeMiddleware(lambda r: r)(request)
        assert response.status_code == 503

    def test_xff_spoof_attempt_blocked(self, flag_file):
        """Refs #773: Angreifer setzt ``X-Forwarded-For: <ops-ip>, <attacker>``,
        um die IP-Allowlist zu umgehen. Vor dem Fix nahm ``_client_ip`` das
        **erste** Element (vom Client setzbar) — Wartungsmauer faellt.

        Mit dem ``get_client_ip``-Helper aus ``signals/audit`` wird der
        rechtsbuendige Eintrag (TRUSTED_PROXY_HOPS=1) genommen — der
        attacker-Eintrag wird sichtbar, der Spoof haelt die Mauer.
        """
        open(flag_file, "w").close()
        with override_settings(
            MAINTENANCE_FLAG_FILE=flag_file,
            MAINTENANCE_ALLOW_IPS=["10.0.0.42"],
            TRUSTED_PROXY_HOPS=1,
        ):
            rf = RequestFactory()
            # Angreifer: client setzt XFF mit Ops-IP an erster Stelle.
            request = rf.get("/clients/", HTTP_X_FORWARDED_FOR="10.0.0.42, 203.0.113.99")
            response = MaintenanceModeMiddleware(lambda r: r)(request)
        assert response.status_code == 503, (
            "XFF-Spoof darf die IP-Allowlist nicht umgehen — die ops-IP an "
            "erster Stelle ist client-kontrolliert; nur der rechte Eintrag "
            "stammt vom vertrauenswuerdigen Reverse-Proxy."
        )


class TestMaintenanceModeCache:
    """File-Exists-Check ist gecached, damit hohe Last den Disk nicht hammert."""

    def test_repeated_calls_use_cache_within_ttl(self, flag_file):
        open(flag_file, "w").close()
        with override_settings(MAINTENANCE_FLAG_FILE=flag_file, MAINTENANCE_CACHE_TTL=999):
            mw = MaintenanceModeMiddleware(lambda r: r)
            rf = RequestFactory()
            response_a = mw(rf.get("/clients/"))
            assert response_a.status_code == 503

            # Flag waehrenddessen entfernen — Cache haelt 999s, also
            # sehen wir weiter 503.
            os.unlink(flag_file)
            response_b = mw(rf.get("/clients/"))
            assert response_b.status_code == 503, (
                "Cache muss den Flag-Status fuer TTL-Sekunden festhalten — "
                "sonst hammern wir den Filesystem bei hohem Traffic."
            )
