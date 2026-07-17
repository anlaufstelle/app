"""Tests für die MFA-Enforcement-Regeln (Issue #521).

Fokus: Die Properties ``User.is_mfa_enforced`` und ``User.has_confirmed_totp_device``
und der Zusammenhang mit dem Facility-Flag ``mfa_enforced_facility_wide``.
"""

import pytest
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import Settings


@pytest.mark.django_db
class TestMFAEnforcementProperty:
    def test_user_without_flag_and_without_facility_setting_is_not_enforced(self, staff_user):
        # Keine Settings für die Facility → is_mfa_enforced ist False.
        assert staff_user.is_mfa_enforced is False

    def test_user_flag_makes_enforced(self, staff_user):
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        assert staff_user.is_mfa_enforced is True

    def test_facility_flag_enforces_all_users(self, staff_user, facility):
        Settings.objects.create(facility=facility, mfa_enforced_facility_wide=True)
        staff_user.refresh_from_db()
        assert staff_user.is_mfa_enforced is True

    def test_facility_flag_off_does_not_enforce(self, staff_user, facility):
        Settings.objects.create(facility=facility, mfa_enforced_facility_wide=False)
        staff_user.refresh_from_db()
        assert staff_user.is_mfa_enforced is False

    def test_user_without_facility_returns_false(self, db):
        from core.models import User

        user = User.objects.create_user(username="nofacility", role=User.Role.STAFF)
        assert user.is_mfa_enforced is False

    def test_has_confirmed_device_false_without_device(self, staff_user):
        assert staff_user.has_confirmed_totp_device is False

    def test_has_confirmed_device_false_with_unconfirmed_device(self, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="pending", confirmed=False)
        assert staff_user.has_confirmed_totp_device is False

    def test_has_confirmed_device_true_with_confirmed_device(self, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="active", confirmed=True)
        assert staff_user.has_confirmed_totp_device is True


@pytest.mark.django_db
class TestMFARoleEnforcementWhenEnabled:
    """A3.1 (Refs #1019) + #689/#1639: Bei aktivem ``MFA_ENFORCE_PRIVILEGED_ROLES``
    erzwingen super_admin/facility_admin (sofort) sowie lead/staff/assistant (mit
    Grace-Frist) MFA allein ueber ihre Rolle. Welche Rollen zu welcher Ebene
    gehoeren, ist per ``MFA_ENFORCED_ROLES_IMMEDIATE`` /
    ``MFA_ENFORCED_ROLES_GRACE`` konfigurierbar (Default: LEAD im Grace-Set).

    Das Setting ist nur in Produktion True (``base.py``); dev/test/e2e bleiben
    bewusst MFA-frei (s. ``TestMFARoleEnforcementDisabledByDefault``). Diese
    Tests schalten es per ``settings``-Fixture gezielt an.
    """

    def test_super_admin_is_enforced_by_role(self, settings, super_admin_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert super_admin_user.is_mfa_enforced is True

    def test_facility_admin_is_enforced_by_role(self, settings, admin_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert admin_user.is_mfa_enforced is True

    def test_lead_is_enforced_by_role(self, settings, lead_user):
        # Refs #1639: LEAD gehoert jetzt per Default in die Rollen-Pflicht
        # (Grace-Set) — wer Fachkraft/Assistenz erzwingt, erst recht die
        # hierarchisch hoehere Leitung. Konfigurierbar per Env
        # ``MFA_ENFORCED_ROLES_GRACE`` (s. test_mfa_role_config / test_mfa_grace).
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert lead_user.is_mfa_enforced is True

    def test_staff_is_enforced_by_role(self, settings, staff_user):
        # Anforderungsaenderung (Refs #689): staff (und assistant) werden mit
        # aktivem Setting rollen-erzwungen. Vorher (nur #1019) war staff bewusst
        # ausgenommen; #689 zieht den MFA-Default fuer Niedrigrechte-Rollen nach
        # (Grace-Frist, s. test_mfa_grace).
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert staff_user.is_mfa_enforced is True

    def test_enforced_facility_admin_without_device_redirects_immediately(self, settings, client, admin_user):
        """#1019-Sofort-Erzwingung wiederhergestellt (Refs #689): ein rollen-
        erzwungener facility_admin OHNE zweiten Faktor wird SOFORT auf
        ``/mfa/setup/`` geleitet — KEINE Grace-Frist, auch ohne gestempeltes
        ``mfa_setup_deadline``. (Vor dem Fix lief die privilegierte Rolle
        faelschlich durch die 30-Tage-Frist.)"""
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert admin_user.mfa_setup_deadline is None
        client.force_login(admin_user)
        response = client.get("/")
        assert response.status_code == 302
        assert response.url == "/mfa/setup/"

    def test_enforced_super_admin_without_device_redirects_immediately(self, settings, client, super_admin_user):
        """Sofort-Erzwingung auch fuer super_admin (Refs #689/#1019)."""
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert super_admin_user.mfa_setup_deadline is None
        client.force_login(super_admin_user)
        response = client.get("/")
        assert response.status_code == 302
        assert response.url == "/mfa/setup/"

    def test_enforced_facility_admin_ignores_future_deadline(self, settings, client, admin_user):
        """Die (harmlos beim Login gestempelte) Grace-Frist entfaltet fuer
        privilegierte Rollen KEINE Wirkung: selbst mit zukuenftigem
        ``mfa_setup_deadline`` wird der facility_admin ohne Geraet sofort
        umgeleitet (Refs #689)."""
        import datetime

        from django.utils import timezone

        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        admin_user.mfa_setup_deadline = timezone.now() + datetime.timedelta(days=20)
        admin_user.save(update_fields=["mfa_setup_deadline"])
        client.force_login(admin_user)
        response = client.get("/")
        assert response.status_code == 302
        assert response.url == "/mfa/setup/"


@pytest.mark.django_db
class TestMFARoleEnforcementDisabledByDefault:
    """DEV bewusst MFA-frei: ohne aktives ``MFA_ENFORCE_PRIVILEGED_ROLES``
    (Default in dev/test/e2e, gesetzt in ``dev.py``) loesen privilegierte Rollen
    KEINEN Rollen-Zwang aus.
    """

    def test_super_admin_not_enforced_by_default(self, super_admin_user):
        assert super_admin_user.is_mfa_enforced is False

    def test_facility_admin_not_enforced_by_default(self, admin_user):
        assert admin_user.is_mfa_enforced is False
