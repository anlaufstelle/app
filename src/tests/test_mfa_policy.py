"""The shared, side-effect-free MFA access policy (Refs #2568).

``decide_mfa_step`` decides between access, setup and verify from explicit
state values and a given instant; ``read_mfa_state`` only reads. Starting a
missing grace period stays an explicit mutation of the enforcement path
(``MFAEnforcementMiddleware``); the display predicate
``session_fully_authenticated`` uses the same decision without importing the
middleware.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
NO_DEVICE = {"has_confirmed_device": False}
GRACE = {**NO_DEVICE, "enforced_with_grace": True}
IMMEDIATE = {**NO_DEVICE, "enforced_immediately": True}


def _policy():
    """Local import: before the extraction the tests fail on their promises,
    not on collection."""
    from core.services.security import mfa_policy

    return mfa_policy


def _state(**values):
    return _policy().MFAState(**values)


def _decide(state, now=NOW):
    return _policy().decide_mfa_step(state, now=now)


class TestDecision:
    """Pure decision table; no database access (no ``django_db`` mark)."""

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            ({"has_confirmed_device": False}, "access"),
            ({"has_confirmed_device": False, "enforced_immediately": True}, "setup"),
            ({**IMMEDIATE, "setup_deadline": NOW + timedelta(days=5)}, "setup"),
            (
                {
                    "has_confirmed_device": False,
                    "enforced_immediately": True,
                    "enforced_with_grace": True,
                    "setup_deadline": NOW + timedelta(days=5),
                },
                "setup",
            ),
            ({"has_confirmed_device": False, "enforced_with_grace": True, "setup_deadline": None}, "access"),
            ({**GRACE, "setup_deadline": NOW + timedelta(days=5)}, "access"),
            ({**GRACE, "setup_deadline": NOW + timedelta(microseconds=1)}, "access"),
            ({"has_confirmed_device": False, "enforced_with_grace": True, "setup_deadline": NOW}, "setup"),
            ({**GRACE, "setup_deadline": NOW - timedelta(days=1)}, "setup"),
            ({"has_confirmed_device": False, "setup_deadline": NOW - timedelta(days=1)}, "access"),
            ({"has_confirmed_device": True}, "verify"),
            ({"has_confirmed_device": True, "session_verified": True}, "access"),
            ({"has_confirmed_device": True, "enforced_immediately": True}, "verify"),
            ({"has_confirmed_device": True, "enforced_immediately": True, "session_verified": True}, "access"),
            (
                {"has_confirmed_device": True, "enforced_with_grace": True, "setup_deadline": NOW - timedelta(days=1)},
                "verify",
            ),
            ({"has_confirmed_device": False, "session_verified": True, "enforced_immediately": True}, "setup"),
        ],
    )
    def test_decision_table(self, values, expected):
        assert _decide(_state(**values)) == _policy().MFAStep(expected)

    def test_the_given_instant_decides_not_the_clock(self):
        state = _state(has_confirmed_device=False, enforced_with_grace=True, setup_deadline=NOW)
        assert _decide(state, now=NOW - timedelta(seconds=1)) == _policy().MFAStep.ACCESS
        assert _decide(state, now=NOW) == _policy().MFAStep.SETUP

    def test_state_is_immutable(self):
        state = _state(has_confirmed_device=False)
        with pytest.raises(AttributeError):
            state.setup_deadline = NOW

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            ({"has_confirmed_device": False, "enforced_with_grace": True}, True),
            ({"has_confirmed_device": False, "enforced_with_grace": True, "setup_deadline": NOW}, False),
            ({"has_confirmed_device": False, "enforced_with_grace": True, "enforced_immediately": True}, False),
            ({"has_confirmed_device": True, "enforced_with_grace": True}, False),
            ({"has_confirmed_device": False}, False),
        ],
    )
    def test_grace_period_missing(self, values, expected):
        assert _state(**values).grace_period_missing is expected


@pytest.mark.django_db
class TestReadState:
    def test_reads_device_and_session(self, staff_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        state = _policy().read_mfa_state(staff_user, {"mfa_verified": True})
        assert state.has_confirmed_device is True
        assert state.session_verified is True

    def test_reads_grace_enforcement_and_deadline(self, settings, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        settings.MFA_ENFORCED_ROLES_IMMEDIATE = []
        settings.MFA_ENFORCED_ROLES_GRACE = [staff_user.role]
        staff_user.mfa_setup_deadline = NOW
        state = _policy().read_mfa_state(staff_user, {})
        assert state == _state(has_confirmed_device=False, enforced_with_grace=True, setup_deadline=NOW)

    def test_reads_deliberate_enforcement_as_immediate(self, staff_user):
        staff_user.mfa_required = True
        state = _policy().read_mfa_state(staff_user, {})
        assert state.enforced_immediately is True
        assert state.has_confirmed_device is False

    def test_reading_writes_nothing(self, settings, staff_user, django_assert_max_num_queries):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        settings.MFA_ENFORCED_ROLES_IMMEDIATE = []
        settings.MFA_ENFORCED_ROLES_GRACE = [staff_user.role]
        session = {}
        with CaptureQueriesContext(connection) as captured:
            state = _policy().read_mfa_state(staff_user, session)
            _decide(state)
        assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in captured.captured_queries)
        assert session == {}
        assert staff_user.mfa_setup_deadline is None


class TestModuleBoundaries:
    @staticmethod
    def _imported_modules(module):
        tree = ast.parse(inspect.getsource(module))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_session_state_does_not_import_middleware(self):
        from core.services.security import session_state

        imported = self._imported_modules(session_state)
        assert not [name for name in imported if name.startswith("core.middleware")]

    def test_policy_does_not_import_middleware(self):
        imported = self._imported_modules(_policy())
        assert not [name for name in imported if name.startswith("core.middleware")]

    def test_read_only_user_proxy_is_gone(self):
        from core.services.security import session_state

        assert not hasattr(session_state, "_ReadOnlyGraceUser")
