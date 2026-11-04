"""Initial focus on the 2FA setup page follows the setup flow."""

import re

import pytest
from django_otp.oath import totp

from core.models import EncryptedTOTPDevice

pytestmark = pytest.mark.django_db
SETUP_URL = "/mfa/setup/"


def _token_input_tag(html):
    match = re.search(r"<input[^>]*\bid=\"id_token\"[^>]*>", html, re.DOTALL)
    assert match, "token input missing"
    return match.group(0)


def _has_autofocus(tag):
    return re.search(r"\sautofocus(\s|=|/|>)", tag) is not None


def test_first_visit_does_not_autofocus_token_field(client, staff_user):
    client.force_login(staff_user)
    response = client.get(SETUP_URL)
    assert response.status_code == 200
    html = response.content.decode()
    tag = _token_input_tag(html)
    assert not _has_autofocus(tag)
    assert "aria-invalid" not in tag
    # The QR code and the manual secret alternative stay reachable before the field.
    assert html.index('data-testid="mfa-qrcode"') < html.index('data-testid="mfa-secret"')
    assert html.index('data-testid="mfa-secret"') < html.index('id="id_token"')
    assert "<summary" in html


def test_invalid_token_moves_focus_to_token_field_and_keeps_aria_contract(client, staff_user):
    client.force_login(staff_user)
    client.get(SETUP_URL)
    response = client.post(SETUP_URL, {"token": "000000"})
    assert response.status_code == 200
    html = response.content.decode()
    tag = _token_input_tag(html)
    assert _has_autofocus(tag)
    assert 'aria-invalid="true"' in tag
    assert 'aria-describedby="mfa-setup-message"' in tag
    assert 'id="mfa-setup-message"' in html


def test_valid_token_still_completes_setup_step(client, staff_user):
    client.force_login(staff_user)
    client.get(SETUP_URL)
    device = EncryptedTOTPDevice.objects.get(user=staff_user, confirmed=False)
    token = f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits):0{device.digits}d}"
    response = client.post(SETUP_URL, {"token": token})
    assert response.status_code == 302
    assert response.url == "/mfa/backup-codes/"


def test_device_change_explains_replacement_before_token_field(client, staff_user, settings):
    settings.SUDO_MODE_ENABLED = False
    EncryptedTOTPDevice.objects.create(user=staff_user, name="old", confirmed=True)
    client.force_login(staff_user)
    session = client.session
    session["mfa_verified"] = True
    session.save()
    response = client.get("/mfa/change-device/")
    assert response.status_code == 200
    html = response.content.decode()
    assert not _has_autofocus(_token_input_tag(html))
    note = "Die bisherigen Authenticator-Geräte werden erst ersetzt"
    assert html.index(note) < html.index('id="id_token"')
    failed = client.post("/mfa/change-device/", {"token": "000000"})
    assert _has_autofocus(_token_input_tag(failed.content.decode()))
