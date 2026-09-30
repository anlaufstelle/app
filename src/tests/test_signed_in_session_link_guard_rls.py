"""Invite, reset and unlock links in a signed-in browser session under RLS (Refs #2391).

A signed-in session carries the database context of its own account, while
the writes behind these links (``LOGIN_UNLOCK``, ``CREDENTIALS_REVOKED``,
``BACKUP_CODES_USED``) belong to the account of the link, possibly in another
facility. Processing them in a signed-in session either violated the WITH
CHECK policy of ``core_auditlog`` or switched the database context of a
session that stays signed in. The links are therefore processed only in a
session that is not signed in. A signed-in session gets a notice with the
regular sign-out form instead: no token is used up, the database context of
the session keeps pointing at its own account, and nothing is written. Once
signed out, the same link works as before.

The same notice reaches a signed-in session whose second factor is still
pending or which has to change its password first: the middlewares that
enforce those steps leave the link routes to the notice instead of
redirecting to their own pages, and keep redirecting everywhere else.
"""

from urllib.parse import urlencode

import pytest
from django.contrib.auth import views as auth_views
from django.contrib.auth.tokens import default_token_generator
from django.db import connection
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AuditLog, User
from core.models.mfa import EncryptedTOTPDevice
from core.services.security import build_recovery_token, generate_backup_codes, invite_token_generator
from core.services.security.login_lockout import LOCKOUT_THRESHOLD, is_locked
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

OLD_PASSWORD = "Old!Passw0rd"
NEW_PASSWORD = "SetupChos3n!Pwd"
REMOTE_ADDR = "203.0.113.231"
NOTICE_TEMPLATE = "auth/signed_in_link_notice.html"
NOTICE_TEXT = "Sie sind derzeit angemeldet. Melden Sie sich ab und öffnen Sie den Link danach erneut."

link_routes = pytest.mark.parametrize(
    ("route", "token_generator", "trigger"),
    [
        ("/invite/{uid}/{token}/", invite_token_generator, "invite_setup"),
        ("/password-reset/{uid}/{token}/", default_token_generator, "password_reset"),
    ],
    ids=["invite", "password_reset"],
)

signed_in_actors = pytest.mark.parametrize(
    ("actor_fixture", "target_in_actor_facility"),
    [
        ("staff_user", False),
        ("staff_user", True),
        ("super_admin_user", False),
    ],
    ids=["staff_other_facility", "staff_same_facility", "super_admin"],
)


@pytest.fixture
def actor(request, actor_fixture):
    return request.getfixturevalue(actor_fixture)


@pytest.fixture
def target_facility(actor, target_in_actor_facility, second_facility):
    return actor.facility if target_in_actor_facility else second_facility


def _target_user(facility, name):
    user = User.objects.create_user(
        username=f"link-target-{name}",
        email=f"link-target-{name}@example.org",
        role=User.Role.STAFF,
        facility=facility,
    )
    user.set_password(OLD_PASSWORD)
    user.save(update_fields=["password"])
    return user


def _locked_target_with_recovery_token(facility):
    user = _target_user(facility, "recovery")
    AuditLog.objects.create(
        user=user,
        facility=facility,
        action=AuditLog.Action.LOGIN_FAILED,
        detail={"username": user.username},
    )
    return user, build_recovery_token(user)


def _link(route, token_generator, user):
    return route.format(uid=urlsafe_base64_encode(force_bytes(user.pk)), token=token_generator.make_token(user))


def _set_password_link(route, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return route.format(uid=uid, token=auth_views.PasswordResetConfirmView.reset_url_token)


def _open_link(client, url):
    response = client.get(url, follow=True)
    assert response.status_code == 200
    assert response.context["validlink"] is True
    return response


def _submit_new_password(client, set_password_url):
    return client.post(
        set_password_url,
        {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        REMOTE_ADDR=REMOTE_ADDR,
    )


def _db_context():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('app.current_facility_id', true), current_setting('app.is_super_admin', true)"
        )
        return cursor.fetchone()


def _actor_db_context(actor):
    return (str(actor.facility_id) if actor.facility_id else "", "true" if actor.is_super_admin else "")


def _assert_notice(response):
    assert response.status_code == 403
    assert NOTICE_TEMPLATE in [template.name for template in response.templates]
    content = response.content.decode()
    assert NOTICE_TEXT in content
    # The regular sign-out form: a POST to the logout view with a CSRF token,
    # on a page that loads the offline store and the client-side sign-out
    # clean-up, so both the Clear-Site-Data header and the local wipe apply.
    assert f'<form method="post" action="{reverse("logout")}"' in content
    assert 'name="csrfmiddlewaretoken"' in content
    for script in ("js/crypto", "js/offline-store", "js/sw-register"):
        assert script in content


def _sign_out(client):
    response = client.post(reverse("logout"))
    assert response.status_code == 302
    assert response["Clear-Site-Data"] == '"storage", "cache"'
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestPasswordLinksInSignedInSessionUnderProdRole:
    @link_routes
    @signed_in_actors
    def test_opening_the_link_shows_a_notice_and_leaves_the_token_usable(
        self,
        client,
        rls_test_role,  # noqa: F811
        actor,
        target_facility,
        route,
        token_generator,
        trigger,
    ):
        target = _target_user(target_facility, trigger)
        url = _link(route, token_generator, target)
        client.force_login(actor)
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            context_after_open = _db_context()

        assert context_after_open == _actor_db_context(actor)
        _assert_notice(opened)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert auth_views.INTERNAL_RESET_SESSION_TOKEN not in client.session
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            reopened = _open_link(client, url)
            completed = _submit_new_password(client, reopened.redirect_chain[-1][0])

        assert completed.status_code == 302
        assert completed["Location"] == reverse("password_reset_complete")
        target.refresh_from_db()
        assert target.check_password(NEW_PASSWORD)
        for action in (AuditLog.Action.LOGIN_UNLOCK, AuditLog.Action.CREDENTIALS_REVOKED):
            rows = AuditLog.objects.filter(user=target, action=action)
            assert list(rows.values_list("facility_id", flat=True)) == [target_facility.pk]

    @link_routes
    @signed_in_actors
    def test_password_is_not_set_from_a_session_that_signed_in_after_opening_the_link(
        self,
        client,
        rls_test_role,  # noqa: F811
        actor,
        target_facility,
        route,
        token_generator,
        trigger,
    ):
        target = _target_user(target_facility, trigger)
        url = _link(route, token_generator, target)
        with as_rls_role(rls_test_role, facility_id=""):
            opened = _open_link(client, url)
        set_password_url = opened.redirect_chain[-1][0]
        assert set_password_url == _set_password_link(route, target)
        client.force_login(actor)
        assert auth_views.INTERNAL_RESET_SESSION_TOKEN in client.session
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            shown = client.get(set_password_url)
            submitted = _submit_new_password(client, set_password_url)
            context_after_submit = _db_context()

        assert context_after_submit == _actor_db_context(actor)
        _assert_notice(shown)
        _assert_notice(submitted)
        assert client.session["_auth_user_id"] == str(actor.pk)
        target.refresh_from_db()
        assert target.check_password(OLD_PASSWORD)
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            reopened = _open_link(client, url)
            completed = _submit_new_password(client, reopened.redirect_chain[-1][0])

        assert completed.status_code == 302
        target.refresh_from_db()
        assert target.check_password(NEW_PASSWORD)


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestUnlockLinksInSignedInSessionUnderProdRole:
    @signed_in_actors
    def test_recovery_link_neither_switches_the_session_context_nor_unlocks(
        self,
        client,
        rls_test_role,  # noqa: F811
        actor,
        target_facility,
    ):
        target, token = _locked_target_with_recovery_token(target_facility)
        url = reverse("core:lockout_recovery_confirm", kwargs={"token": token})
        client.force_login(actor)
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            context_after_open = _db_context()
            submitted = client.post(url, REMOTE_ADDR=REMOTE_ADDR)
            context_after_submit = _db_context()

        assert context_after_open == _actor_db_context(actor)
        assert context_after_submit == _actor_db_context(actor)
        _assert_notice(opened)
        _assert_notice(submitted)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            reopened = client.get(url)
            completed = client.post(url, REMOTE_ADDR=REMOTE_ADDR)

        assert reopened.status_code == 200
        assert completed.status_code == 302
        assert completed["Location"] == "/login/?recovered=1"
        unlock = AuditLog.objects.get(user=target, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlock.facility_id == target_facility.pk
        assert unlock.ip_address == REMOTE_ADDR

    @signed_in_actors
    def test_backup_code_is_neither_used_up_nor_unlocks(
        self,
        client,
        rls_test_role,  # noqa: F811
        actor,
        target_facility,
    ):
        target = _target_user(target_facility, "backup-code")
        code = generate_backup_codes(target)[0]
        url = reverse("core:lockout_recovery_backup_code")
        form = {"username": target.username, "backup_code": code}
        client.force_login(actor)
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            submitted = client.post(url, form, REMOTE_ADDR=REMOTE_ADDR)
            context_after_submit = _db_context()

        assert context_after_submit == _actor_db_context(actor)
        _assert_notice(opened)
        _assert_notice(submitted)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            completed = client.post(url, form, REMOTE_ADDR=REMOTE_ADDR)

        assert completed.status_code == 302
        assert completed["Location"] == "/login/?recovered=1"
        for action in (AuditLog.Action.LOGIN_UNLOCK, AuditLog.Action.BACKUP_CODES_USED):
            rows = AuditLog.objects.filter(user=target, action=action)
            assert list(rows.values_list("facility_id", flat=True)) == [target_facility.pk]


link_requests = pytest.mark.parametrize(
    ("route", "requested_flow"),
    [
        ("password_reset", "password_reset"),
        ("core:lockout_recovery_request", "lockout_recovery_token"),
    ],
    ids=["password_reset", "lockout_recovery"],
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestLinkRequestsInSignedInSessionUnderProdRole:
    @link_requests
    @signed_in_actors
    def test_requesting_a_link_shows_a_notice_and_sends_nothing(
        self,
        client,
        mailoutbox,
        rls_test_role,  # noqa: F811
        actor,
        target_facility,
        route,
        requested_flow,
    ):
        target = _target_user(target_facility, requested_flow)
        url = reverse(route)
        client.force_login(actor)
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            submitted = client.post(url, {"email": target.email}, REMOTE_ADDR=REMOTE_ADDR)
            context_after_submit = _db_context()

        assert context_after_submit == _actor_db_context(actor)
        _assert_notice(opened)
        _assert_notice(submitted)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert mailoutbox == []
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            requested = client.post(url, {"email": target.email}, REMOTE_ADDR=REMOTE_ADDR)

        assert requested.status_code == 302
        assert [message.to for message in mailoutbox] == [[target.email]]
        rows = AuditLog.objects.filter(user=target, action=AuditLog.Action.PASSWORD_RESET_REQUESTED)
        assert list(rows.values_list("facility_id", flat=True)) == [target_facility.pk]


PENDING_SECOND_FACTOR = "pending_second_factor"
PENDING_PASSWORD_CHANGE = "pending_password_change"

pending_session_steps = pytest.mark.parametrize(
    "pending_step",
    [PENDING_SECOND_FACTOR, PENDING_PASSWORD_CHANGE],
)

# ``None`` stands for the account the link belongs to.
pending_step_actors = pytest.mark.parametrize(
    "actor_fixture",
    ["staff_user", "super_admin_user", None],
    ids=["staff_other_facility", "super_admin", "link_account"],
)


def _pending_step_actor(request, actor_fixture, target):
    return target if actor_fixture is None else request.getfixturevalue(actor_fixture)


def _sign_in_with_pending_step(client, user, pending_step):
    """Sign in the way the login view leaves a session before its next step."""
    if pending_step == PENDING_SECOND_FACTOR:
        EncryptedTOTPDevice.objects.create(user=user, name="authenticator", confirmed=True)
    else:
        User.objects.filter(pk=user.pk).update(must_change_password=True)
    client.force_login(user)
    if pending_step == PENDING_SECOND_FACTOR:
        session = client.session
        session["mfa_verified"] = False
        session.save()


def _pending_step_redirect(pending_step, path):
    if pending_step == PENDING_SECOND_FACTOR:
        return f"/mfa/verify/?{urlencode({'next': path})}"
    return reverse("password_change")


def _lock(user):
    for _ in range(LOCKOUT_THRESHOLD):
        AuditLog.objects.create(
            user=user,
            facility=user.facility,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
        )
    assert is_locked(user)


def _htmx_post(client, url, data=None):
    return client.post(url, data or {}, REMOTE_ADDR=REMOTE_ADDR, headers={"HX-Request": "true"})


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestLinksInSessionWithPendingStepUnderProdRole:
    @link_routes
    @pending_step_actors
    @pending_session_steps
    def test_password_link_shows_the_notice_instead_of_the_pending_step(
        self,
        request,
        client,
        rls_test_role,  # noqa: F811
        second_facility,
        actor_fixture,
        pending_step,
        route,
        token_generator,
        trigger,
    ):
        target = _target_user(second_facility, trigger)
        actor = _pending_step_actor(request, actor_fixture, target)
        _sign_in_with_pending_step(client, actor, pending_step)
        url = _link(route, token_generator, target)
        set_password_url = _set_password_link(route, target)
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            submitted = _submit_new_password(client, url)
            submitted_via_htmx = _htmx_post(
                client, set_password_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}
            )
            context_after_submit = _db_context()

        assert context_after_submit == _actor_db_context(actor)
        for response in (opened, submitted, submitted_via_htmx):
            _assert_notice(response)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert auth_views.INTERNAL_RESET_SESSION_TOKEN not in client.session
        target.refresh_from_db()
        assert target.check_password(OLD_PASSWORD)
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            reopened = _open_link(client, url)
            completed = _submit_new_password(client, reopened.redirect_chain[-1][0])

        assert completed.status_code == 302
        assert completed["Location"] == reverse("password_reset_complete")
        target.refresh_from_db()
        assert target.check_password(NEW_PASSWORD)

    @pending_step_actors
    @pending_session_steps
    def test_recovery_link_shows_the_notice_and_keeps_the_lock(
        self,
        request,
        client,
        rls_test_role,  # noqa: F811
        second_facility,
        actor_fixture,
        pending_step,
    ):
        target = _target_user(second_facility, "recovery")
        actor = _pending_step_actor(request, actor_fixture, target)
        _sign_in_with_pending_step(client, actor, pending_step)
        _lock(target)
        url = reverse("core:lockout_recovery_confirm", kwargs={"token": build_recovery_token(target)})
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            context_after_open = _db_context()
            submitted = client.post(url, REMOTE_ADDR=REMOTE_ADDR)
            submitted_via_htmx = _htmx_post(client, url)
            context_after_submit = _db_context()

        assert context_after_open == _actor_db_context(actor)
        assert context_after_submit == _actor_db_context(actor)
        for response in (opened, submitted, submitted_via_htmx):
            _assert_notice(response)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert is_locked(target)
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            reopened = client.get(url)
            completed = client.post(url, REMOTE_ADDR=REMOTE_ADDR)

        assert reopened.status_code == 200
        assert completed.status_code == 302
        assert completed["Location"] == "/login/?recovered=1"
        assert not is_locked(target)

    @pending_step_actors
    @pending_session_steps
    def test_backup_code_shows_the_notice_and_is_not_used_up(
        self,
        request,
        client,
        rls_test_role,  # noqa: F811
        second_facility,
        actor_fixture,
        pending_step,
    ):
        target = _target_user(second_facility, "backup-code")
        actor = _pending_step_actor(request, actor_fixture, target)
        _sign_in_with_pending_step(client, actor, pending_step)
        _lock(target)
        code = generate_backup_codes(target)[0]
        url = reverse("core:lockout_recovery_backup_code")
        form = {"username": target.username, "backup_code": code}
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            submitted = client.post(url, form, REMOTE_ADDR=REMOTE_ADDR)
            submitted_via_htmx = _htmx_post(client, url, form)
            context_after_submit = _db_context()

        assert context_after_submit == _actor_db_context(actor)
        for response in (opened, submitted, submitted_via_htmx):
            _assert_notice(response)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert is_locked(target)
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            completed = client.post(url, form, REMOTE_ADDR=REMOTE_ADDR)

        assert completed.status_code == 302
        assert completed["Location"] == "/login/?recovered=1"
        assert not is_locked(target)

    @pending_step_actors
    @pending_session_steps
    def test_unlock_request_shows_the_notice_and_sends_nothing(
        self,
        request,
        client,
        mailoutbox,
        rls_test_role,  # noqa: F811
        second_facility,
        actor_fixture,
        pending_step,
    ):
        target = _target_user(second_facility, "unlock-request")
        actor = _pending_step_actor(request, actor_fixture, target)
        _sign_in_with_pending_step(client, actor, pending_step)
        url = reverse("core:lockout_recovery_request")
        audit_rows_before = AuditLog.objects.count()

        with as_rls_role(rls_test_role, facility_id=""):
            opened = client.get(url)
            submitted = client.post(url, {"email": target.email}, REMOTE_ADDR=REMOTE_ADDR)
            submitted_via_htmx = _htmx_post(client, url, {"email": target.email})
            context_after_submit = _db_context()

        assert context_after_submit == _actor_db_context(actor)
        for response in (opened, submitted, submitted_via_htmx):
            _assert_notice(response)
        assert client.session["_auth_user_id"] == str(actor.pk)
        assert mailoutbox == []
        assert AuditLog.objects.count() == audit_rows_before

        with as_rls_role(rls_test_role, facility_id=""):
            _sign_out(client)
            requested = client.post(url, {"email": target.email}, REMOTE_ADDR=REMOTE_ADDR)

        assert requested.status_code == 302
        assert [message.to for message in mailoutbox] == [[target.email]]


@pytest.mark.django_db
class TestPendingStepStillGuardsOrdinaryPages:
    @pytest.mark.parametrize("route", ["core:account_profile", "core:dashboard"])
    @pending_session_steps
    def test_ordinary_page_redirects_to_the_pending_step(self, client, staff_user, pending_step, route):
        path = reverse(route)
        _sign_in_with_pending_step(client, staff_user, pending_step)

        response = client.get(path)

        assert response.status_code == 302
        assert response["Location"] == _pending_step_redirect(pending_step, path)
