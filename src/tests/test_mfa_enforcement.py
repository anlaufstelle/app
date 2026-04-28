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
