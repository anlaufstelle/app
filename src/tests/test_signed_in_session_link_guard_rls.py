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
"""

import pytest
from django.contrib.auth import views as auth_views
from django.contrib.auth.tokens import default_token_generator
from django.db import connection
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AuditLog, User
from core.services.security import build_recovery_token, generate_backup_codes, invite_token_generator
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
