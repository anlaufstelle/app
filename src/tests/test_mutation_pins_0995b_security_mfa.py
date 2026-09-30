"""Pins for the empty-input guard of the second-factor check (``core.services.security.mfa``).

Source: mutation delta of checkpoint release-0.99.5, 1 mutant in
``verify_totp_or_backup``: the fallback for a missing token becomes the
placeholder ``"XXXX"`` instead of the empty string. An empty or missing input
then passes the emptiness guard and reaches the device lookup; the answer stays
``False``, but the check touches the database. The contract is that such an
input is rejected before any device is looked up, so the pins run the call
under a zero-query budget.
"""

import pytest

from core.services.security.mfa import verify_totp_or_backup

pytestmark = pytest.mark.django_db


class TestVerifyTotpOrBackup:
    """``verify_totp_or_backup``: empty input is rejected without a device lookup."""

    @pytest.mark.parametrize("token", ["", None], ids=["empty", "missing"])
    def test_empty_input_rejected_without_query(self, token, staff_user, django_assert_num_queries):
        """x_verify_totp_or_backup__mutmut_7: ``token or "XXXX"`` sends the empty input
        on to the device lookup (one query instead of none).
        """
        with django_assert_num_queries(0):
            assert verify_totp_or_backup(staff_user, token) is False
