"""Characterization of the MFA access decision (Refs #2568, behaviour basis #2286).

Pins the observable behaviour of the two public paths that share the decision
"access, setup or verify":

* ``MFAEnforcementMiddleware`` via the test client (redirects, ``next``,
  exempt paths, the grace-period stamp on first enforcement);
* ``session_fully_authenticated()`` (footer version, ``/health/`` details),
  which must stay free of side effects.

Each scenario of the decision matrix runs through both paths, so a later
extraction of the decision cannot let anybody get further or less far than
before. Read query counts of the predicate are pinned as well: a refactor
must not add database round trips to every rendered page.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import Settings, User
from core.services.security.session_state import session_fully_authenticated

pytestmark = pytest.mark.django_db

ACCESS = "access"
SETUP = "setup"
VERIFY = "verify"

_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE")


# --- scenario builders --------------------------------------------------------


def _grace_role(settings, user):
    settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
    settings.MFA_ENFORCED_ROLES_IMMEDIATE = []
    settings.MFA_ENFORCED_ROLES_GRACE = [user.role]


def _immediate_role(settings, user):
    settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
    settings.MFA_ENFORCED_ROLES_IMMEDIATE = [user.role]
    settings.MFA_ENFORCED_ROLES_GRACE = []


def _set_deadline(user, delta):
    user.mfa_setup_deadline = None if delta is None else timezone.now() + delta
    user.save(update_fields=["mfa_setup_deadline"])


def _require(user):
    user.mfa_required = True
    user.save(update_fields=["mfa_required"])


def _totp(user, *, confirmed=True):
    TOTPDevice.objects.create(user=user, name="test", confirmed=confirmed)


def _passkey(user, *, confirmed=True):
    from django_otp_webauthn.models import WebAuthnCredential

    WebAuthnCredential.objects.create(
        user=user,
        name="test key",
        confirmed=confirmed,
        credential_id=b"cred-characterization",
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def _facility_wide(user):
    Settings.objects.update_or_create(facility=user.facility, defaults={"mfa_enforced_facility_wide": True})


def _no_enforcement(settings, user):
    return {}


def _deliberate_flag(settings, user):
    _require(user)
    return {}


def _facility_wide_flag(settings, user):
    _facility_wide(user)
    return {}


def _role_immediate(settings, user):
    _immediate_role(settings, user)
    return {}


def _role_immediate_with_running_deadline(settings, user):
    _immediate_role(settings, user)
    _set_deadline(user, timedelta(days=5))
    return {}


def _grace_running(settings, user):
    _grace_role(settings, user)
    _set_deadline(user, timedelta(days=5))
    return {}


def _grace_expired(settings, user):
    _grace_role(settings, user)
    _set_deadline(user, -timedelta(days=1))
    return {}


def _grace_missing(settings, user):
    _grace_role(settings, user)
    return {}


def _grace_running_with_deliberate_flag(settings, user):
    _grace_role(settings, user)
    _set_deadline(user, timedelta(days=5))
    _require(user)
    return {}


def _grace_running_with_facility_wide_flag(settings, user):
    _grace_role(settings, user)
    _set_deadline(user, timedelta(days=5))
    _facility_wide(user)
    return {}


def _role_enforcement_switched_off(settings, user):
    settings.MFA_ENFORCE_PRIVILEGED_ROLES = False
    settings.MFA_ENFORCED_ROLES_IMMEDIATE = [user.role]
    settings.MFA_ENFORCED_ROLES_GRACE = []
    _set_deadline(user, -timedelta(days=1))
    return {}


def _role_in_no_set(settings, user):
    settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
    settings.MFA_ENFORCED_ROLES_IMMEDIATE = []
    settings.MFA_ENFORCED_ROLES_GRACE = []
    _set_deadline(user, -timedelta(days=1))
    return {}


def _totp_unverified(settings, user):
    _totp(user)
    return {}


def _totp_verified_false(settings, user):
    _totp(user)
    return {"mfa_verified": False}


def _totp_verified(settings, user):
    _totp(user)
    return {"mfa_verified": True}


def _passkey_unverified(settings, user):
    _passkey(user)
    return {}


def _passkey_verified(settings, user):
    _passkey(user)
    return {"mfa_verified": True}


def _unconfirmed_totp_without_enforcement(settings, user):
    _totp(user, confirmed=False)
    return {}


def _unconfirmed_totp_with_deliberate_flag(settings, user):
    _totp(user, confirmed=False)
    _require(user)
    return {}


def _unconfirmed_passkey_with_deliberate_flag(settings, user):
    _passkey(user, confirmed=False)
    _require(user)
    return {}


def _device_with_expired_grace_unverified(settings, user):
    _grace_expired(settings, user)
    _totp(user)
    return {}


def _device_with_expired_grace_verified(settings, user):
    _grace_expired(settings, user)
    _totp(user)
    return {"mfa_verified": True}


def _device_with_deliberate_flag_verified(settings, user):
    _require(user)
    _passkey(user)
    return {"mfa_verified": True}


def _device_with_immediate_role_unverified(settings, user):
    _immediate_role(settings, user)
    _passkey(user)
    return {}


#: (builder, expected step, predicate read queries on a freshly loaded user).
MATRIX = {
    "no_enforcement": (_no_enforcement, ACCESS, 4),
    "deliberate_flag": (_deliberate_flag, SETUP, 2),
    "facility_wide_flag": (_facility_wide_flag, SETUP, 4),
    "role_immediate": (_role_immediate, SETUP, 4),
    "role_immediate_with_running_deadline": (_role_immediate_with_running_deadline, SETUP, 4),
    "grace_running": (_grace_running, ACCESS, 4),
    "grace_expired": (_grace_expired, SETUP, 4),
    "grace_missing": (_grace_missing, ACCESS, 4),
    "grace_running_with_deliberate_flag": (_grace_running_with_deliberate_flag, SETUP, 2),
    "grace_running_with_facility_wide_flag": (_grace_running_with_facility_wide_flag, SETUP, 4),
    "role_enforcement_switched_off": (_role_enforcement_switched_off, ACCESS, 4),
    "role_in_no_set": (_role_in_no_set, ACCESS, 4),
    "totp_unverified": (_totp_unverified, VERIFY, 1),
    "totp_verified_false": (_totp_verified_false, VERIFY, 1),
    "totp_verified": (_totp_verified, ACCESS, 1),
    "passkey_unverified": (_passkey_unverified, VERIFY, 2),
    "passkey_verified": (_passkey_verified, ACCESS, 2),
    "unconfirmed_totp_without_enforcement": (_unconfirmed_totp_without_enforcement, ACCESS, 4),
    "unconfirmed_totp_with_deliberate_flag": (_unconfirmed_totp_with_deliberate_flag, SETUP, 2),
    "unconfirmed_passkey_with_deliberate_flag": (_unconfirmed_passkey_with_deliberate_flag, SETUP, 2),
    "device_with_expired_grace_unverified": (_device_with_expired_grace_unverified, VERIFY, 1),
    "device_with_expired_grace_verified": (_device_with_expired_grace_verified, ACCESS, 1),
    "device_with_deliberate_flag_verified": (_device_with_deliberate_flag_verified, ACCESS, 2),
    "device_with_immediate_role_unverified": (_device_with_immediate_role_unverified, VERIFY, 2),
}


@pytest.fixture(params=sorted(MATRIX))
def scenario(request, settings, staff_user):
    builder, expected, read_queries = MATRIX[request.param]
    session = builder(settings, staff_user)
    return staff_user, session, expected, read_queries


def _fresh(user):
    return User.objects.get(pk=user.pk)


def _predicate_request(user, session):
    request = RequestFactory().get("/")
    request.user = user
    request.session = session
    return request


def _login(client, user, session_values):
    client.force_login(user)
    if session_values:
        session = client.session
        session.update(session_values)
        session.save()


def _writes(captured):
    return [q["sql"] for q in captured.captured_queries if q["sql"].lstrip().upper().startswith(_WRITE_PREFIXES)]


def _user_writes(captured):
    return [sql for sql in _writes(captured) if '"core_user"' in sql]


def _assert_step(response, expected):
    if expected == ACCESS:
        assert response.status_code == 200, response.headers.get("Location")
    elif expected == SETUP:
        assert response.status_code == 302
        assert response.headers["Location"] == "/mfa/setup/"
    else:
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/mfa/verify/")


# --- decision matrix ----------------------------------------------------------


class TestDecisionMatrix:
    def test_middleware_follows_the_matrix(self, client, scenario):
        user, session, expected, _ = scenario
        _login(client, user, session)
        _assert_step(client.get(reverse("core:zeitstrom")), expected)

    def test_predicate_follows_the_matrix(self, scenario):
        user, session, expected, _ = scenario
        assert session_fully_authenticated(_predicate_request(_fresh(user), dict(session))) is (expected == ACCESS)

    def test_predicate_is_free_of_side_effects(self, scenario):
        user, session, _, _ = scenario
        fresh = _fresh(user)
        deadline_before = fresh.mfa_setup_deadline
        session_copy = dict(session)
        with CaptureQueriesContext(connection) as captured:
            session_fully_authenticated(_predicate_request(fresh, session_copy))
        assert _writes(captured) == []
        assert session_copy == session
        assert fresh.mfa_setup_deadline == deadline_before
        assert _fresh(user).mfa_setup_deadline == deadline_before

    def test_predicate_read_cost_is_unchanged(self, scenario, django_assert_num_queries):
        user, session, _, read_queries = scenario
        fresh = _fresh(user)
        with django_assert_num_queries(read_queries):
            session_fully_authenticated(_predicate_request(fresh, dict(session)))


class TestDeadlineBoundary:
    """``deadline == now`` counts as expired, one microsecond later as running."""

    @pytest.fixture
    def frozen_now(self, monkeypatch):
        instant = timezone.now()
        monkeypatch.setattr(timezone, "now", lambda: instant)
        return instant

    @pytest.mark.parametrize(("offset", "expected"), [(timedelta(0), SETUP), (timedelta(microseconds=1), ACCESS)])
    def test_middleware_boundary(self, client, settings, staff_user, frozen_now, offset, expected):
        _grace_role(settings, staff_user)
        staff_user.mfa_setup_deadline = frozen_now + offset
        staff_user.save(update_fields=["mfa_setup_deadline"])
        client.force_login(staff_user)
        _assert_step(client.get(reverse("core:zeitstrom")), expected)

    @pytest.mark.parametrize(("offset", "expected"), [(timedelta(0), False), (timedelta(microseconds=1), True)])
    def test_predicate_boundary(self, settings, staff_user, frozen_now, offset, expected):
        _grace_role(settings, staff_user)
        staff_user.mfa_setup_deadline = frozen_now + offset
        staff_user.save(update_fields=["mfa_setup_deadline"])
        assert session_fully_authenticated(_predicate_request(_fresh(staff_user), {})) is expected


class TestPendingPasswordChange:
    @pytest.mark.parametrize("name", ["no_enforcement", "grace_missing", "totp_verified", "grace_running"])
    def test_predicate_stays_false(self, settings, staff_user, name):
        builder, _, _ = MATRIX[name]
        session = builder(settings, staff_user)
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        fresh = _fresh(staff_user)
        with CaptureQueriesContext(connection) as captured:
            assert session_fully_authenticated(_predicate_request(fresh, dict(session))) is False
        assert _writes(captured) == []

    def test_password_change_wins_over_mfa_and_starts_no_grace(self, client, settings, staff_user):
        _grace_missing(settings, staff_user)
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/password-change/")
        assert _fresh(staff_user).mfa_setup_deadline is None


# --- grace stamp: only on enforcement, never extended -------------------------


class TestGraceStamp:
    def test_enforcement_starts_a_missing_grace_period_once(self, client, settings, staff_user):
        from core.constants import MFA_SETUP_GRACE_DAYS

        _grace_missing(settings, staff_user)
        client.force_login(staff_user)
        before = timezone.now()
        with CaptureQueriesContext(connection) as captured:
            response = client.get(reverse("core:zeitstrom"))
        after = timezone.now()
        assert response.status_code == 200
        assert len(_user_writes(captured)) == 1
        deadline = _fresh(staff_user).mfa_setup_deadline
        grace = timedelta(days=MFA_SETUP_GRACE_DAYS)
        assert before + grace <= deadline <= after + grace
        # The stamped value is visible to the rest of the same request: the
        # reminder context sees a deadline (outside the reminder window).
        assert response.context["mfa_setup_show_banner"] is False

        with CaptureQueriesContext(connection) as captured:
            assert client.get(reverse("core:zeitstrom")).status_code == 200
        assert _user_writes(captured) == []
        assert _fresh(staff_user).mfa_setup_deadline == deadline

    @pytest.mark.parametrize(
        "name",
        [
            "no_enforcement",
            "deliberate_flag",
            "role_immediate",
            "grace_running",
            "grace_expired",
            "grace_running_with_deliberate_flag",
            "role_enforcement_switched_off",
            "role_in_no_set",
            "totp_unverified",
            "totp_verified",
            "passkey_unverified",
        ],
    )
    def test_other_states_write_no_deadline(self, client, settings, staff_user, name):
        builder, _, _ = MATRIX[name]
        session = builder(settings, staff_user)
        before = _fresh(staff_user).mfa_setup_deadline
        _login(client, staff_user, session)
        with CaptureQueriesContext(connection) as captured:
            client.get(reverse("core:zeitstrom"))
        assert _user_writes(captured) == []
        assert _fresh(staff_user).mfa_setup_deadline == before

    def test_grace_role_with_device_starts_no_grace(self, client, settings, staff_user):
        _grace_missing(settings, staff_user)
        _totp(staff_user)
        client.force_login(staff_user)
        assert client.get(reverse("core:zeitstrom")).headers["Location"].startswith("/mfa/verify/")
        assert _fresh(staff_user).mfa_setup_deadline is None

    def test_display_only_paths_start_no_grace(self, client, settings, staff_user):
        """``/health/`` is exempt from enforcement and renders details through
        the predicate only: it shows them, but the clock does not start."""
        _grace_missing(settings, staff_user)
        client.force_login(staff_user)
        payload = client.get(reverse("health")).json()
        assert "version" in payload
        assert _fresh(staff_user).mfa_setup_deadline is None

    def test_middleware_does_not_mark_the_session_verified(self, client, settings, staff_user):
        _totp(staff_user)
        client.force_login(staff_user)
        client.get(reverse("core:zeitstrom"))
        assert "mfa_verified" not in client.session


# --- per-request cache ----------------------------------------------------------


class TestPerRequestCache:
    @pytest.mark.parametrize("name", ["no_enforcement", "totp_unverified", "grace_missing", "deliberate_flag"])
    def test_second_call_hits_no_database(self, settings, staff_user, name, django_assert_num_queries):
        builder, expected, _ = MATRIX[name]
        session = builder(settings, staff_user)
        request = _predicate_request(_fresh(staff_user), dict(session))
        first = session_fully_authenticated(request)
        with django_assert_num_queries(0):
            assert session_fully_authenticated(request) is first
        assert first is (expected == ACCESS)

    def test_a_new_request_is_evaluated_again(self, staff_user):
        _totp(staff_user)
        user = _fresh(staff_user)
        request = _predicate_request(user, {})
        assert session_fully_authenticated(request) is False
        session_fully_authenticated(request)
        TOTPDevice.objects.filter(user=staff_user).delete()
        assert session_fully_authenticated(_predicate_request(user, {})) is True


# --- redirect exemptions and ``next`` ------------------------------------------

EXEMPT_PATHS = [
    "/login/",
    "/logout/",
    "/password-change/",
    "/password-reset/",
    "/invite/some-token/",
    "/account/recovery/some-token/",
    "/static/does-not-exist.css",
    "/mfa/verify/",
    "/webauthn/anything/",
    "/i18n/setlang/",
    "/health/",
    "/sw.js",
    "/manifest.json",
    "/auth/offline-key-salt/",
]


class TestRedirectExemptions:
    @pytest.mark.parametrize("path", EXEMPT_PATHS)
    def test_exempt_paths_are_not_sent_to_verify(self, client, staff_user, path):
        _totp(staff_user)
        client.force_login(staff_user)
        response = client.get(path)
        assert not response.headers.get("Location", "").startswith("/mfa/")

    @pytest.mark.parametrize("path", ["/health/", "/sw.js", "/auth/offline-key-salt/"])
    def test_exempt_paths_are_not_sent_to_setup(self, client, staff_user, path):
        _require(staff_user)
        client.force_login(staff_user)
        response = client.get(path)
        assert not response.headers.get("Location", "").startswith("/mfa/")

    @pytest.mark.parametrize("path", ["/healthz", "/mfa", "/staticfiles/x.css", "/clients/"])
    def test_non_exempt_lookalikes_are_redirected(self, client, staff_user, path):
        _totp(staff_user)
        client.force_login(staff_user)
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/mfa/verify/")

    def test_anonymous_requests_are_not_redirected_to_mfa(self, client):
        response = client.get(reverse("core:zeitstrom"))
        assert not response.headers.get("Location", "").startswith("/mfa/")


class TestNextParameter:
    def test_get_keeps_the_full_target(self, client, staff_user):
        _totp(staff_user)
        client.force_login(staff_user)
        response = client.get("/clients/?q=a b&page=2")
        assert response.headers["Location"] == "/mfa/verify/?next=%2Fclients%2F%3Fq%3Da%2520b%26page%3D2"

    def test_root_target_has_no_next(self, client, staff_user):
        _totp(staff_user)
        client.force_login(staff_user)
        assert client.get("/").headers["Location"] == "/mfa/verify/"

    @pytest.mark.parametrize("method", ["post", "head"])
    def test_non_get_methods_have_no_next(self, client, staff_user, method):
        _totp(staff_user)
        client.force_login(staff_user)
        response = getattr(client, method)("/clients/")
        assert response.status_code == 302
        assert response.headers["Location"] == "/mfa/verify/"

    def test_setup_redirect_never_carries_next(self, client, staff_user):
        _require(staff_user)
        client.force_login(staff_user)
        assert client.get("/clients/?q=1").headers["Location"] == "/mfa/setup/"


# --- visibility of version and health details ---------------------------------


class TestDetailVisibility:
    DETAIL_FIELDS = ("version", "cache", "smtp", "last_backup_age_hours", "disk_free_pct", "stale_jobs")

    def test_health_details_follow_the_matrix(self, client, scenario):
        user, session, expected, _ = scenario
        _login(client, user, session)
        payload = client.get(reverse("health")).json()
        assert payload["status"]
        for field in self.DETAIL_FIELDS:
            assert (field in payload) is (expected == ACCESS), field

    def test_footer_version_on_the_verify_page_is_hidden(self, client, staff_user):
        _totp(staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("mfa_verify"))
        assert response.status_code == 200
        assert 'data-testid="app-version"' not in response.content.decode()

    def test_footer_version_after_grace_start_is_shown(self, client, settings, staff_user):
        _grace_missing(settings, staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert 'data-testid="app-version"' in response.content.decode()
