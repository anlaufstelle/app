"""Security R3: Pre-Auth-Audit-Writes muessen die RLS-WITH-CHECK-Policy erfuellen.

Login-Lockout (views/auth.py, form_valid) und Password-Reset schreiben mit
explizitem ``facility=``-Override einen AuditLog, waehrend die Session-GUCs
(``app.current_facility_id``/``app.is_super_admin``) fuer den anonymen Request
noch auf ``''`` stehen (FacilityScopeMiddleware). Unter der Prod-App-Rolle
(NOSUPERUSER/NOBYPASSRLS) verletzt der INSERT die WITH-CHECK-Policy aus
Migration 0085 — 500er statt Audit-Eintrag, stille Audit-Luecke. Der Fix
synct die GUCs im SSOT-Helper ``log_audit_event`` (wie der Signal-Pfad
``on_user_login_failed`` es bereits tut).
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory

from core.models import AuditLog, User
from core.services.audit import log_audit_event
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    facility_a_with_data,
    rls_test_role,
)


def _preauth_request():
    request = RequestFactory().post("/login/", REMOTE_ADDR="203.0.113.10")
    request.user = AnonymousUser()
    return request


@pytest.mark.django_db(transaction=True)
class TestPreAuthAuditRlsWithCheck:
    def test_lockout_write_with_facility_override_passes_with_check(
        self,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
    ):
        """Facility-Override + anonyme GUCs: INSERT muss unter der App-Rolle durchgehen."""
        user = User.objects.create_user(username="r3-locked", role=User.Role.STAFF, facility=facility_a_with_data)
        before = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).count()
        # as_rls_role(facility_id="") stellt die Middleware-Ausgangslage des
        # anonymen Requests her: GUC leer, Rolle ohne Superuser/BYPASSRLS.
        with as_rls_role(rls_test_role, facility_id=""):
            log_audit_event(
                _preauth_request(),
                AuditLog.Action.LOGIN_FAILED,
                user=user,
                facility=user.facility,
                detail={"message": "Login blockiert durch Account-Lockout", "reason": "locked"},
            )
        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).order_by("-timestamp").first()
        assert AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).count() == before + 1
        assert entry.facility_id == facility_a_with_data.pk

    def test_unknown_user_null_facility_still_passes(
        self,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
    ):
        """Negativkontrolle: facility=None nutzt den WITH-CHECK-NULL-Branch (ging schon immer)."""
        with as_rls_role(rls_test_role, facility_id=""):
            log_audit_event(
                _preauth_request(),
                AuditLog.Action.LOGIN_FAILED,
                user=None,
                facility=None,
                detail={"message": "Fehlgeschlagener Login-Versuch", "username": "unbekannt"},
            )
        assert AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED, facility__isnull=True).exists()

    def test_guc_not_touched_for_authenticated_requests(
        self,
        facility_a_with_data,  # noqa: F811
        django_user_model,
    ):
        """Kein Pre-Auth-Fall: authentifizierter Request laesst die GUCs in Ruhe."""
        user = django_user_model.objects.create_user(
            username="r3-authed", role=User.Role.STAFF, facility=facility_a_with_data
        )
        request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.11")
        request.user = user
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.current_facility_id', 'sentinel', false)")
        log_audit_event(request, AuditLog.Action.LOGIN, user=user, facility=user.facility)
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.current_facility_id', true)")
            assert cur.fetchone()[0] == "sentinel"
