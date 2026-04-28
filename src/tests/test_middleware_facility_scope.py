"""Dedizierte Unit-Tests für FacilityScopeMiddleware (Refs #598 T-5).

Ergänzt die Coverage aus ``test_rls.py::TestRLSFunctional`` um explizite
Behavioral-Tests auf der Middleware-Grenze (request.current_facility,
DB-Variable, Cursor-Auf-Schluss).
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory

from core.middleware.facility_scope import FacilityScopeMiddleware


def _call_middleware(request):
    """Middleware auf *request* laufen lassen und die Response zurückgeben.

    ``get_response`` ist ein Sentinel — die Tests interessieren sich primär
    für die Mutation von ``request.current_facility`` und die DB-Variable,
    nicht für den Return-Wert.
    """
    return FacilityScopeMiddleware(lambda r: "ok")(request)


@pytest.mark.django_db
class TestFacilityScopeRequestAttribute:
    def test_authenticated_user_sets_request_current_facility(self, facility, staff_user):
        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user  # fixture: facility = facility

        _call_middleware(request)

        assert request.current_facility == facility

    def test_anonymous_user_sets_none(self):
        rf = RequestFactory()
        request = rf.get("/login/")
        request.user = AnonymousUser()

        _call_middleware(request)

        assert request.current_facility is None

    def test_authenticated_user_without_facility_sets_none(self, staff_user):
        staff_user.facility = None
        staff_user.save(update_fields=["facility"])

        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user

        _call_middleware(request)

        assert request.current_facility is None


@pytest.mark.django_db
class TestFacilityScopeDbVariable:
    """Prüft, dass die Postgres-Session-Variable korrekt gesetzt wird.

    Echtes RLS-Filtering wird in Tests durch Superuser-Bypass umgangen; die
    Session-Variable ist trotzdem der Policy-Input für Produktion.
    """

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Session-Variable-Check erfordert PostgreSQL")

    def test_db_session_variable_is_set_for_authenticated_user(self, facility, staff_user):
        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user

        _call_middleware(request)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == str(facility.pk)

    def test_db_session_variable_cleared_for_user_without_facility(self, staff_user):
        staff_user.facility = None
        staff_user.save(update_fields=["facility"])

        # Vorher einen Marker setzen, damit wir sehen, ob die Middleware
        # ihn ueberschreibt (muss sie, sonst Leak).
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_facility_id', 'leak-marker', false)")

        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user

        _call_middleware(request)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == ""


@pytest.mark.django_db
class TestFacilityScopeCursorHygiene:
    """Regression #591 (ffb5666): Anonyme Requests sollen keinen DB-Cursor
    öffnen, damit Anonymous-Routes (Login, Static, Health) keinen
    SET-Round-Trip verursachen."""

    def test_anonymous_request_does_not_open_cursor(self):
        rf = RequestFactory()
        request = rf.get("/login/")
        request.user = AnonymousUser()

        with patch.object(connection, "cursor", wraps=connection.cursor) as cursor_spy:
            _call_middleware(request)

        assert cursor_spy.call_count == 0, (
            "Anonymous Request sollte keinen DB-Cursor öffnen (Regression-Schutz gegen unnötige SET-Roundtrips)."
        )

    def test_authenticated_request_opens_cursor(self, facility, staff_user):
        if connection.vendor != "postgresql":
            pytest.skip("Cursor-Open-Check nur auf PostgreSQL relevant")
        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user

        with patch.object(connection, "cursor", wraps=connection.cursor) as cursor_spy:
            _call_middleware(request)

        assert cursor_spy.call_count >= 1, (
            "Authenticated Request muss Cursor öffnen, um app.current_facility_id zu setzen."
        )
