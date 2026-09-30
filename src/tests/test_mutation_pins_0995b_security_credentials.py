"""Pins for the RLS session context of the lockout lift (``core.services.security.credentials``).

Source: mutation delta of checkpoint release-0.99.5, 17 mutants.
Groups:

* ``_sync_rls_context_for_anonymous_request`` (15) — the request-less early
  return is inverted, the authentication check loses the request user, the
  attribute name or the ``and``, a request without an attached user raises
  instead of counting as anonymous, and the synced facility is dropped.
* ``revoke_credentials`` (2) — the sync call loses the target user or the
  request.

``unlock`` writes ``LOGIN_UNLOCK`` through the request-less audit helper,
which does not sync the RLS session settings itself. For an anonymous reset or
invite request the settings must therefore point at the target user's facility
BEFORE ``unlock`` runs; for an authenticated request (or none at all) they must
keep the acting context. The pins record ``app.current_facility_id`` and
``app.is_super_admin`` at the moment ``unlock`` is called (a recording wrapper
around the real ``unlock``), so they observe the database context the insert
sees without switching the database role. The role-switching helpers of the
functional RLS suite are deliberately not imported: that module skips itself
while a mutant is under test, which would silently disable every pin here.

Target users belong to the second facility of the organisation, so a context
that falls back to the first facility (or to no facility) is distinguishable
from the expected one.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory

from core.models import AuditLog
from core.services.security import credentials
from core.services.security.credentials import RevocationReason, revoke_credentials

pytestmark = [pytest.mark.django_db, pytest.mark.postgres]

LIFTING_REASONS = [RevocationReason.PASSWORD_RESET, RevocationReason.INVITE_SETUP]


def _set_rls_context(facility_id: str, is_super_admin: str) -> None:
    """Put the session settings into the state a middleware would leave behind."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_facility_id', %s, false), set_config('app.is_super_admin', %s, false)",
            [facility_id, is_super_admin],
        )


def _read_rls_context() -> tuple[str, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('app.current_facility_id', true), current_setting('app.is_super_admin', true)"
        )
        facility_id, is_super_admin = cursor.fetchone()
    return facility_id or "", is_super_admin or ""


def _request(user=None):
    request = RequestFactory().post("/password-reset/confirm/")
    if user is not None:
        request.user = user
    request.session = {}
    return request


@pytest.fixture
def context_at_unlock(monkeypatch):
    """Session settings as seen by each ``unlock`` call of the credentials service."""
    seen: list[tuple[str, str]] = []
    real_unlock = credentials.unlock

    def recording_unlock(user, *args, **kwargs):
        seen.append(_read_rls_context())
        return real_unlock(user, *args, **kwargs)

    monkeypatch.setattr(credentials, "unlock", recording_unlock)
    return seen


def _unlock_rows(user):
    return AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK, target_id=str(user.pk))


class TestAnonymousRequestSyncsTargetContext:
    """Anonymous reset/invite: ``unlock`` runs under the target user's facility."""

    @pytest.mark.parametrize("reason", LIFTING_REASONS, ids=lambda reason: str(reason))
    def test_unlock_sees_target_facility(self, reason, context_at_unlock, staff_user, second_facility_user):
        """x__sync_rls_context_for_anonymous_request__mutmut_1 (early return for every real
        request), __mutmut_10 (``or`` makes an anonymous user return early), __mutmut_21,
        __mutmut_25, __mutmut_30, __mutmut_31 (facility dropped or looked up under a wrong
        name, the setting becomes empty), x_revoke_credentials__mutmut_24 (sync for no user)
        and x_revoke_credentials__mutmut_25 (sync without the request returns early).
        """
        _set_rls_context("", "")

        revoke_credentials(second_facility_user, reason=reason, request=_request(AnonymousUser()))

        assert context_at_unlock == [(str(second_facility_user.facility_id), "")]
        assert _unlock_rows(second_facility_user).get().facility_id == second_facility_user.facility_id

    def test_request_without_user_counts_as_anonymous(self, second_facility_user):
        """x__sync_rls_context_for_anonymous_request__mutmut_7: without the ``None`` default
        a request that carries no user (no authentication middleware in front of it)
        raises ``AttributeError`` instead of being treated as anonymous.
        """
        _set_rls_context("", "")

        credentials._sync_rls_context_for_anonymous_request(second_facility_user, _request())

        assert _read_rls_context() == (str(second_facility_user.facility_id), "")


class TestAuthenticatedRequestKeepsActingContext:
    """Authenticated request: the acting person's context is not switched to the target."""

    def test_staff_of_other_facility_keeps_own_facility(
        self, context_at_unlock, facility, staff_user, second_facility_user
    ):
        """x__sync_rls_context_for_anonymous_request__mutmut_2, __mutmut_3, __mutmut_8,
        __mutmut_9 (request user lost), __mutmut_11 (``is None``), __mutmut_12, __mutmut_18,
        __mutmut_19 (authentication flag read from the wrong object or attribute): each
        treats the logged-in person as anonymous and moves the session to the target's
        facility.
        """
        _set_rls_context(str(facility.pk), "")

        revoke_credentials(second_facility_user, reason=RevocationReason.PASSWORD_RESET, request=_request(staff_user))

        assert context_at_unlock == [(str(facility.pk), "")]

    def test_super_admin_keeps_installation_wide_context(
        self, context_at_unlock, super_admin_user, staff_user, second_facility_user
    ):
        """Same mutants as above for a super_admin session: switching would drop the
        installation-wide flag and pin the session to the target's facility.
        """
        _set_rls_context("", "true")

        revoke_credentials(
            second_facility_user, reason=RevocationReason.INVITE_SETUP, request=_request(super_admin_user)
        )

        assert context_at_unlock == [("", "true")]


class TestWithoutRequestKeepsCallerContext:
    """Service/command context without a request: the caller owns the database context."""

    def test_caller_context_is_left_alone(self, context_at_unlock, facility, staff_user, second_facility_user):
        """x__sync_rls_context_for_anonymous_request__mutmut_1: the inverted early return
        syncs for ``request=None`` and moves the caller's session to the target's facility.
        """
        _set_rls_context(str(facility.pk), "")

        revoke_credentials(second_facility_user, reason=RevocationReason.PASSWORD_RESET, request=None)

        assert context_at_unlock == [(str(facility.pk), "")]
        assert _unlock_rows(second_facility_user).count() == 1
