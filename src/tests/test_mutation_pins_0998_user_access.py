"""Pins for incomplete actor attributes at the public MFA reset boundary.

Source: mutation delta of the release-0.99.8 checkpoint, 7 mutants.
Groups: missing authentication (1), activity (2), system role (2), and facility role (2).
"""

from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied

from core.services.user_access import reset_user_mfa

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("missing", ["is_authenticated", "is_active"])
def test_reset_requires_explicit_authentication_and_activity(staff_user, missing):
    """Missing credentials deny a public reset: x__assert_can_reset_mfa__mutmut_11,
    x__assert_can_reset_mfa__mutmut_18, x__assert_can_reset_mfa__mutmut_21.
    """
    attributes = dict(pk=-1, is_authenticated=True, is_active=True, is_super_admin=True)
    del attributes[missing]

    with pytest.raises(PermissionDenied) as error:
        reset_user_mfa(staff_user, actor=SimpleNamespace(**attributes))

    assert str(error.value) == "Second-factor reset requires an active, signed-in administrator."


@pytest.mark.parametrize("missing", ["is_super_admin", "is_facility_admin"])
def test_reset_requires_an_explicit_administrator_role(staff_user, missing):
    """Missing roles never grant a reset: x__assert_can_reset_mfa__mutmut_36,
    x__assert_can_reset_mfa__mutmut_39, x__assert_can_reset_mfa__mutmut_48,
    x__assert_can_reset_mfa__mutmut_51.
    """
    attributes = dict(
        pk=-1,
        is_authenticated=True,
        is_active=True,
        is_super_admin=False,
        is_facility_admin=False,
        facility_id=staff_user.facility_id,
    )
    del attributes[missing]

    with pytest.raises(PermissionDenied) as error:
        reset_user_mfa(staff_user, actor=SimpleNamespace(**attributes))

    assert str(error.value) == "Second-factor reset is not allowed for this account."
