"""Password setup via invite or reset link from a signed-in session under RLS (Refs #2391).

A signed-in browser session carries the database context of its own account.
The credential writes after a password setup (``LOGIN_UNLOCK``,
``CREDENTIALS_REVOKED``) target the facility of the account the link belongs
to. From a session of another facility these inserts violated the WITH CHECK
policy of ``core_auditlog`` after the password had already been stored: a
server error, no audit rows, and an active lockout stayed in place. The view
ends the existing session once the password is set, so the writes take the
anonymous path that sets the context to the target account.
"""

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AuditLog, User
from core.services.security import invite_token_generator
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

NEW_PASSWORD = "SetupChos3n!Pwd"
REMOTE_ADDR = "203.0.113.231"

link_routes = pytest.mark.parametrize(
    ("route", "token_generator", "trigger"),
    [
        ("/invite/{uid}/{token}/", invite_token_generator, "invite_setup"),
        ("/password-reset/{uid}/{token}/", default_token_generator, "password_reset"),
    ],
    ids=["invite", "password_reset"],
)


def _target_user(facility, trigger):
    user = User.objects.create_user(
        username=f"link-target-{trigger}",
        email=f"link-target-{trigger}@example.org",
        role=User.Role.STAFF,
        facility=facility,
    )
    user.set_password("Old!Passw0rd")
    user.save(update_fields=["password"])
    return user


def _link(route, token_generator, user):
    return route.format(uid=urlsafe_base64_encode(force_bytes(user.pk)), token=token_generator.make_token(user))


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


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestPasswordSetupFromSignedInSessionUnderProdRole:
    @link_routes
    @pytest.mark.parametrize(
        ("actor_fixture", "target_in_actor_facility"),
        [
            ("staff_user", False),
            ("staff_user", True),
            ("super_admin_user", False),
        ],
        ids=["staff_other_facility", "staff_same_facility", "super_admin"],
    )
    def test_password_setup_redirects_and_ends_the_session(
        self,
        request,
        client,
        rls_test_role,  # noqa: F811
        facility,
        second_facility,
        route,
        token_generator,
        trigger,
        actor_fixture,
        target_in_actor_facility,
    ):
        actor = request.getfixturevalue(actor_fixture)
        target_facility = facility if target_in_actor_facility else second_facility
        target = _target_user(target_facility, trigger)
        client.force_login(actor)
        url = _link(route, token_generator, target)

        with as_rls_role(rls_test_role, facility_id=""):
            opened = _open_link(client, url)
            response = _submit_new_password(client, opened.redirect_chain[-1][0])

        assert response.status_code == 302
        assert response["Location"] == reverse("password_reset_complete")
        target.refresh_from_db()
        assert target.check_password(NEW_PASSWORD)
        assert "_auth_user_id" not in client.session
        for action in (AuditLog.Action.LOGIN_UNLOCK, AuditLog.Action.CREDENTIALS_REVOKED):
            rows = AuditLog.objects.filter(user=target, action=action)
            assert list(rows.values_list("facility_id", flat=True)) == [target_facility.pk]

    @link_routes
    def test_unlock_audit_carries_target_facility_from_other_facility_session(
        self,
        client,
        rls_test_role,  # noqa: F811
        staff_user,
        second_facility,
        route,
        token_generator,
        trigger,
    ):
        target = _target_user(second_facility, trigger)
        client.force_login(staff_user)
        url = _link(route, token_generator, target)

        with as_rls_role(rls_test_role, facility_id=""):
            opened = _open_link(client, url)
            _submit_new_password(client, opened.redirect_chain[-1][0])

        unlock = AuditLog.objects.get(user=target, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlock.facility_id == second_facility.pk
        assert unlock.detail["trigger"] == trigger
        assert unlock.ip_address == REMOTE_ADDR
        revoked = AuditLog.objects.get(user=target, action=AuditLog.Action.CREDENTIALS_REVOKED)
        assert revoked.facility_id == second_facility.pk
        assert revoked.detail["reason"] == trigger
        logout_row = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.LOGOUT)
        assert logout_row.facility_id == staff_user.facility_id

    @link_routes
    def test_opening_the_link_keeps_the_signed_in_session(
        self,
        client,
        rls_test_role,  # noqa: F811
        staff_user,
        second_facility,
        route,
        token_generator,
        trigger,
    ):
        target = _target_user(second_facility, trigger)
        client.force_login(staff_user)
        url = _link(route, token_generator, target)

        with as_rls_role(rls_test_role, facility_id=""):
            _open_link(client, url)

        assert client.session["_auth_user_id"] == str(staff_user.pk)
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGOUT).exists()
