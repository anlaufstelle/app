"""Backup-code device eligibility with current-format issued credentials.

Source: mutation delta of the release-0.99.7 checkpoint, 2 mutants.
Groups: backup device name (1), confirmed device (1). Public generation
creates the stored digest; public verification must preserve rejected tokens.
"""

import pytest
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.services.security.mfa import BACKUP_DEVICE_NAME, generate_backup_codes, verify_backup_code

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("ineligible", ["other-name", "unconfirmed"])
def test_ineligible_backup_device_cannot_consume_an_issued_code(staff_user, ineligible):
    """A modern code is rejected without consumption until its device is eligible.

    x_verify_backup_code__mutmut_9, x_verify_backup_code__mutmut_10.
    """
    code = generate_backup_codes(staff_user)[0]
    device = StaticDevice.objects.get(user=staff_user, name=BACKUP_DEVICE_NAME)
    tokens_before = list(device.token_set.order_by("pk").values_list("pk", "token"))
    device.name = "other" if ineligible == "other-name" else BACKUP_DEVICE_NAME
    device.confirmed = ineligible != "unconfirmed"
    device.save(update_fields=["name", "confirmed"])

    assert verify_backup_code(staff_user, code) is False
    assert list(device.token_set.order_by("pk").values_list("pk", "token")) == tokens_before
    device.refresh_from_db()
    assert device.last_used_at is None
    assert device.throttling_failure_count == 0

    device.name = BACKUP_DEVICE_NAME
    device.confirmed = True
    device.save(update_fields=["name", "confirmed"])
    assert verify_backup_code(staff_user, code) is True
    assert StaticToken.objects.filter(device=device).count() == len(tokens_before) - 1
