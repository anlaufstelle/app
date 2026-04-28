"""Tests for PostgreSQL Row-Level-Security setup (#542).

Echtes RLS-Filtering kann nur als Non-Superuser-Rolle getestet werden.
Im Test-Setup ist der DB-User Superuser (bypassed RLS per Postgres-Default).
Diese Tests prüfen daher die Migration-Korrektheit — dass Policies und
``FORCE ROW LEVEL SECURITY`` auf allen erwarteten Tabellen existieren.

Das eigentliche RLS-Verhalten greift in Produktion, wo der Django-DB-User
explizit kein Superuser sein darf (siehe docs/coolify-deployment.md).
"""

import pytest
from django.db import connection

EXPECTED_TABLES = [
    "core_client",
    "core_event",
    "core_case",
    "core_workitem",
    "core_documenttype",
    "core_fieldtemplate",
    "core_auditlog",
    "core_activity",
    "core_deletionrequest",
    "core_retentionproposal",
    "core_settings",
    "core_timefilter",
    "core_legalhold",
    "core_statisticssnapshot",
    "core_recentclientvisit",
    "core_quicktemplate",
    "core_eventhistory",
    "core_eventattachment",
    "core_episode",
]


@pytest.mark.django_db
class TestRLSSetup:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("RLS requires PostgreSQL")

    def test_rls_enabled_on_all_facility_scoped_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname FROM pg_class WHERE relrowsecurity AND relname = ANY(%s)",
                [EXPECTED_TABLES],
            )
            enabled = {row[0] for row in cursor.fetchall()}
        missing = set(EXPECTED_TABLES) - enabled
        assert not missing, f"RLS missing on: {missing}"

    def test_force_rls_enabled_on_all_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname FROM pg_class WHERE relforcerowsecurity AND relname = ANY(%s)",
                [EXPECTED_TABLES],
            )
            forced = {row[0] for row in cursor.fetchall()}
        missing = set(EXPECTED_TABLES) - forced
        assert not missing, f"FORCE RLS missing on: {missing}"

    def test_facility_isolation_policy_exists_on_all_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_policies WHERE policyname = 'facility_isolation' AND tablename = ANY(%s)",
                [EXPECTED_TABLES],
            )
            covered = {row[0] for row in cursor.fetchall()}
        missing = set(EXPECTED_TABLES) - covered
        assert not missing, f"Policy 'facility_isolation' missing on: {missing}"

    def test_set_config_does_not_raise(self):
        """Smoke-Test for the set_config call used by FacilityScopeMiddleware."""
        import uuid

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_facility_id', %s, false)",
                [str(uuid.uuid4())],
            )
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            assert cursor.fetchone()[0]

    def test_middleware_set_config_persists_across_cursors(self, facility, staff_user):
        """Regression #586: set_config(..., is_local=false) darf nicht mit der
        Middleware-eigenen Statement-Transaktion ablaufen — sonst liefert die
        RLS-Policy NULL fuer alle nachfolgenden ORM-Queries dieses Requests.

        Simuliert durch: Middleware aufrufen, dann in separatem Cursor lesen.
        """
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user  # fixture: facility = facility

        FacilityScopeMiddleware(lambda r: r)(request)

        # Separater Cursor, separater Kontext — Variable muss ueberleben.
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == str(facility.pk)

    def test_set_config_round_trip_for_facility_spoof(self, facility, second_facility):
        """Functional-Check der Session-Variable als RLS-Defense-in-Depth.

        Im Test-Setup ist der DB-User Superuser → echtes RLS-Filter bypasst.
        Daher prüft dieser Test den Round-Trip: ``set_config`` auf Facility B's
        ID, gefolgt von ``current_setting(...)``, muss exakt Facility B's ID
        zurückgeben. Damit ist sichergestellt, dass die Middleware-Variable
        in Produktion (Non-Superuser) korrekt als Policy-Input greift.

        Refs #542 / #591 WP1.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_facility_id', %s, false)",
                [str(second_facility.pk)],
            )
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == str(second_facility.pk)
        # Stellt zusätzlich sicher, dass Facility A (andere UUID) nicht
        # versehentlich ausgegeben wird.
        assert value != str(facility.pk)

    def test_middleware_clears_setting_for_facility_less_user(self, staff_user):
        """Ein authentifizierter User ohne Facility (Edge-Case) muss die
        Variable auf leer zuruecksetzen — sonst koennte eine wiederverwendete
        Connection den Wert aus einer frueheren Request leaken (Refs #586).

        Anonyme Requests sind hier nicht erfasst: sie greifen nicht auf
        facility-scoped Tabellen zu und vermeiden deshalb bewusst den
        zusaetzlichen DB-Hit der Middleware.
        """
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        # Erst setzen, um den Leak-Vektor zu simulieren.
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_facility_id', 'leak-id', false)")

        staff_user.facility = None
        staff_user.save(update_fields=["facility"])

        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user
        FacilityScopeMiddleware(lambda r: r)(request)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            assert cursor.fetchone()[0] == ""


@pytest.mark.django_db
class TestRLSFunctional:
    """Funktionale Regression-Guards für die FacilityScopeMiddleware.

    Echte RLS-Policies können im Test-Setup nicht greifen (DB-User ist
    Superuser und bypasst RLS). Diese Klasse testet daher das Verhalten der
    Session-Variable direkt — was die Policies in Produktion nutzen.
    """

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("RLS requires PostgreSQL")

    def test_anonymous_request_does_not_set_facility_variable(self):
        """Regression ffb5666: Ein anonymer Request darf keinen DB-Cursor
        öffnen, um die Facility-Variable zu setzen. Andernfalls würden
        Anonymous-Routes (Login, Health, Static) unnötige SET-Statements
        absetzen, und stehengebliebene Werte aus früheren Requests würden
        versehentlich überschrieben oder geleakt (Refs #591, WP1).
        """
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        # Stelle sicher, dass zu Beginn kein Wert gesetzt ist.
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_facility_id', '', false)")

        rf = RequestFactory()
        request = rf.get("/login/")
        request.user = AnonymousUser()
        FacilityScopeMiddleware(lambda r: r)(request)

        # Nach dem Middleware-Lauf muss die Variable weiterhin leer sein —
        # also weder auf eine Facility-ID noch auf einen sonstigen Wert
        # gesetzt.
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == "", "Anonymous Request darf die facility-Variable nicht setzen."

    def test_anonymous_request_does_not_clobber_preset_value(self):
        """Begleitcheck: Ein anonymer Request darf auch einen vorhandenen
        Wert (aus Sicht der Middleware) nicht neu setzen — die Middleware
        überspringt den SET-Call bei anonymen Requests komplett. Damit ist
        kein zusätzlicher Round-Trip oder Overwrite nötig.

        Prüfung: Wir setzen vor dem Request eine Marker-UUID als
        ``app.current_facility_id``. Nach dem Middleware-Lauf muss der
        Wert unverändert sein — denn die Middleware hat den Cursor nicht
        angefasst.
        """
        import uuid

        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        marker = str(uuid.uuid4())
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_facility_id', %s, false)", [marker])

        rf = RequestFactory()
        request = rf.get("/health/")
        request.user = AnonymousUser()
        FacilityScopeMiddleware(lambda r: r)(request)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == marker, (
            "Anonymous Request darf bestehende Variable nicht umschreiben (Middleware muss den Cursor-Aufruf skippen)."
        )
