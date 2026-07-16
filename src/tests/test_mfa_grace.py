"""Tests für die MFA-Grace-Frist + Rollen-Enforcement-Erweiterung (Refs #689).

Kernanforderung: der harte MFA-Default wird NICHT sofort umgeschaltet, sondern
über eine 30-Tage-Setup-Frist (``mfa_setup_deadline``) eingeführt, damit
Bestandsnutzer nicht ausgesperrt werden. Bewusst gesetzte Einzel-/
Einrichtungs-Zwänge (``mfa_required``, facility-weit) greifen dagegen weiterhin
SOFORT.
"""

from datetime import timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.middleware.mfa import MFAEnforcementMiddleware


@pytest.fixture
def rf():
    return RequestFactory()


def _middleware():
    return MFAEnforcementMiddleware(lambda r: r)


@pytest.mark.django_db
class TestRoleEnforcementExpanded:
    """#689: staff/assistant werden bei aktivem Setting rollen-erzwungen."""

    def test_staff_is_enforced_by_role(self, settings, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert staff_user.is_mfa_enforced is True

    def test_assistant_is_enforced_by_role(self, settings, assistant_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert assistant_user.is_mfa_enforced is True

    def test_lead_not_enforced_without_include_flag(self, settings, lead_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        settings.MFA_ENFORCE_INCLUDE_LEAD = False
        assert lead_user.is_mfa_enforced is False

    def test_lead_enforced_when_include_flag_on(self, settings, lead_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        settings.MFA_ENFORCE_INCLUDE_LEAD = True
        assert lead_user.is_mfa_enforced is True

    def test_role_enforcement_off_means_no_role_enforcement(self, settings, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = False
        assert staff_user.is_mfa_enforced is False


@pytest.mark.django_db
class TestDeliberateVsRoleEnforcement:
    """Bewusster Zwang (mfa_required) = sofort; Rollen-Zwang = mit Grace."""

    def test_mfa_required_is_deliberate(self, staff_user):
        staff_user.mfa_required = True
        assert staff_user.mfa_enforced_deliberately is True

    def test_role_enforced_is_not_deliberate(self, settings, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert staff_user.mfa_enforced_deliberately is False
        assert staff_user.mfa_enforced_by_role is True


@pytest.mark.django_db
class TestGraceMiddleware:
    def test_deliberate_without_device_redirects_immediately(self, rf, staff_user):
        """mfa_required = kein Grace: sofort auf /mfa/setup/."""
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = _middleware()(request)
        assert response.status_code == 302
        assert "/mfa/setup/" in response.url

    def test_role_enforced_without_deadline_gets_grace_and_passes(self, settings, rf, staff_user):
        """Erstkontakt eines rollen-erzwungenen Nutzers ohne Frist: Frist wird
        gestempelt (nicht ausgesperrt) und der Request durchgelassen."""
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert staff_user.mfa_setup_deadline is None
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = _middleware()(request)
        assert response == request  # durchgelassen
        staff_user.refresh_from_db()
        assert staff_user.mfa_setup_deadline is not None
        assert staff_user.mfa_setup_deadline > timezone.now()

    def test_role_enforced_within_grace_passes(self, settings, rf, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        staff_user.mfa_setup_deadline = timezone.now() + timedelta(days=5)
        staff_user.save(update_fields=["mfa_setup_deadline"])
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = _middleware()(request)
        assert response == request

    def test_role_enforced_after_grace_redirects_to_setup(self, settings, rf, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        staff_user.mfa_setup_deadline = timezone.now() - timedelta(minutes=1)
        staff_user.save(update_fields=["mfa_setup_deadline"])
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = _middleware()(request)
        assert response.status_code == 302
        assert "/mfa/setup/" in response.url

    def test_device_present_but_unverified_still_redirects_to_verify(self, settings, rf, staff_user):
        """Grace betrifft nur den Setup-Fall — mit Gerät gilt weiter Case 2."""
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        TOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = _middleware()(request)
        assert response.status_code == 302
        assert "/mfa/verify/" in response.url


@pytest.mark.django_db
class TestLoginStampsDeadline:
    """Das Stamping haengt an ``CustomLoginView.form_valid`` — es muss ein
    echter Login-POST sein (``client.login()``/``force_login`` umgehen den View)."""

    def test_first_login_sets_deadline(self, client, staff_user):
        assert staff_user.mfa_setup_deadline is None
        client.post("/login/", {"username": "teststaff", "password": "testpass123"})
        staff_user.refresh_from_db()
        assert staff_user.mfa_setup_deadline is not None

    def test_second_login_does_not_reset_deadline(self, client, staff_user):
        fixed = timezone.now() + timedelta(days=3)
        staff_user.mfa_setup_deadline = fixed
        staff_user.save(update_fields=["mfa_setup_deadline"])
        client.post("/login/", {"username": "teststaff", "password": "testpass123"})
        staff_user.refresh_from_db()
        assert abs((staff_user.mfa_setup_deadline - fixed).total_seconds()) < 1


@pytest.mark.django_db
class TestReminderBannerContext:
    def _get(self, client, user):
        client.force_login(user)
        session = client.session
        session["mfa_verified"] = True  # sonst gatet MFASettingsView/Verify
        session.save()
        return client.get("/")

    def test_banner_shown_within_reminder_window(self, settings, client, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        staff_user.mfa_setup_deadline = timezone.now() + timedelta(days=3)
        staff_user.save(update_fields=["mfa_setup_deadline"])
        response = self._get(client, staff_user)
        assert response.context["mfa_setup_show_banner"] is True
        assert response.context["mfa_setup_days_remaining"] >= 1

    def test_no_banner_far_from_deadline(self, settings, client, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        staff_user.mfa_setup_deadline = timezone.now() + timedelta(days=25)
        staff_user.save(update_fields=["mfa_setup_deadline"])
        response = self._get(client, staff_user)
        assert response.context.get("mfa_setup_show_banner") is not True

    def test_no_banner_when_not_enforced(self, settings, client, staff_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = False
        staff_user.mfa_setup_deadline = timezone.now() + timedelta(days=1)
        staff_user.save(update_fields=["mfa_setup_deadline"])
        response = self._get(client, staff_user)
        assert response.context.get("mfa_setup_show_banner") is not True


class TestReminderBannerTranslationsEN:
    """EN-msgstr-Lock für den Reminder-Banner (Refs #689), analog #1219/#1144."""

    def test_setup_link_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("Jetzt einrichten") == "Set up now"

    def test_countdown_singular_en(self):
        from django.utils.translation import ngettext, override

        with override("en"):
            text = ngettext(
                "Zwei-Faktor-Authentifizierung wird in %(days)s Tag verpflichtend.",
                "Zwei-Faktor-Authentifizierung wird in %(days)s Tagen verpflichtend.",
                1,
            ) % {"days": 1}
        assert text == "Two-factor authentication becomes mandatory in 1 day."

    def test_countdown_plural_en(self):
        from django.utils.translation import ngettext, override

        with override("en"):
            text = ngettext(
                "Zwei-Faktor-Authentifizierung wird in %(days)s Tag verpflichtend.",
                "Zwei-Faktor-Authentifizierung wird in %(days)s Tagen verpflichtend.",
                5,
            ) % {"days": 5}
        assert text == "Two-factor authentication becomes mandatory in 5 days."


@pytest.mark.django_db
class TestEnforceMfaAdminActionScoping:
    def test_facility_admin_only_enforces_own_facility(self, rf, admin_user, staff_user, organization):
        from django.contrib.messages.storage.fallback import FallbackStorage

        from core.admin.users import enforce_mfa_for_selected_users
        from core.models import Facility, User

        other_facility = Facility.objects.create(organization=organization, name="Fremdstelle")
        foreign = User.objects.create_user(username="foreignstaff", role=User.Role.STAFF, facility=other_facility)
        request = rf.post("/")
        request.user = admin_user
        request.current_facility = admin_user.facility
        request.session = {}
        request._messages = FallbackStorage(request)

        enforce_mfa_for_selected_users(None, request, User.objects.all())

        staff_user.refresh_from_db()
        foreign.refresh_from_db()
        assert staff_user.mfa_required is True
        assert foreign.mfa_required is False  # Fremd-Facility unberührt

    def test_super_admin_enforces_across_facilities(self, rf, super_admin_user, staff_user):
        from django.contrib.messages.storage.fallback import FallbackStorage

        from core.admin.users import enforce_mfa_for_selected_users
        from core.models import User

        request = rf.post("/")
        request.user = super_admin_user
        request.current_facility = None
        request.session = {}
        request._messages = FallbackStorage(request)

        enforce_mfa_for_selected_users(None, request, User.objects.all())

        staff_user.refresh_from_db()
        assert staff_user.mfa_required is True
