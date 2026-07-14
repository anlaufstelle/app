"""Tests fuer NoStoreCacheMiddleware (Refs #1342).

DSGVO-Haertung: JEDE Response an einen authentifizierten User traegt
``Cache-Control: no-store, private`` — Blanket-Schutz gegen PII im
Browser-/Festplatten-Cache und bfcache, robust gegen kuenftige Views, die
den Header selbst vergessen. ``setdefault``: bereits gesetzte, bewusst
gewaehlte Header (z.B. ``OfflineCsrfTokenView``) werden nicht ueberschrieben.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from core.middleware.no_store_cache import NoStoreCacheMiddleware


def _make_middleware(response):
    """Baut eine Middleware mit festem Response-Callback fuer Tests."""
    return NoStoreCacheMiddleware(lambda r: response)


class TestNoStoreCacheMiddlewareUnit:
    """Middleware-Verhalten isoliert per RequestFactory + manuell gesetztem user."""

    def test_authenticated_user_gets_no_store_private(self, staff_user):
        rf = RequestFactory()
        request = rf.get("/clients/")
        request.user = staff_user

        response = _make_middleware(HttpResponse("ok"))(request)

        assert response["Cache-Control"] == "no-store, private"

    def test_anonymous_user_is_not_touched(self):
        rf = RequestFactory()
        request = rf.get("/login/")
        request.user = AnonymousUser()

        response = _make_middleware(HttpResponse("ok"))(request)

        assert "Cache-Control" not in response

    def test_missing_user_attribute_is_no_op(self):
        """Falls AuthenticationMiddleware (aus welchem Grund auch immer) noch
        nicht gelaufen ist, darf die Middleware nicht crashen."""
        rf = RequestFactory()
        request = rf.get("/health/")

        response = _make_middleware(HttpResponse("ok"))(request)

        assert "Cache-Control" not in response

    def test_existing_cache_control_header_is_not_overwritten(self, staff_user):
        """setdefault: eine View, die den Header schon bewusst setzt, behaelt
        ihren Wert — z.B. ``no-store`` ohne ``private`` bei ``OfflineCsrfTokenView``."""
        rf = RequestFactory()
        request = rf.get("/api/v1/offline/csrf/")
        request.user = staff_user
        preset = HttpResponse("ok")
        preset["Cache-Control"] = "no-store"

        response = _make_middleware(preset)(request)

        assert response["Cache-Control"] == "no-store"


@pytest.mark.django_db
class TestNoStoreCacheMiddlewareIntegration:
    """End-to-End ueber den vollen Middleware-Stack (echter Request-Zyklus)."""

    def test_authenticated_facility_view_gets_no_store(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"))
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store, private"

    def test_anonymous_public_offline_shell_is_unaffected(self, client):
        """Der oeffentliche, pk-lose Offline-Shell (kein Login-Gate, muss per
        Service-Worker ``cache.addAll`` vorcachebar bleiben) bekommt KEINEN
        no-store-Header von dieser Middleware aufgezwungen."""
        response = client.get(reverse("core:offline_client_shell"))
        assert response.status_code == 200
        assert response.get("Cache-Control") != "no-store, private"

    def test_offline_csrf_endpoint_keeps_its_own_no_store_value(self, client, staff_user):
        """Der Endpoint setzt explizit ``no-store`` (ohne ``private``) —
        die Blanket-Middleware darf das nicht auf ``no-store, private`` aendern."""
        client.force_login(staff_user)
        response = client.get(reverse("core:offline_csrf"))
        assert response["Cache-Control"] == "no-store"
