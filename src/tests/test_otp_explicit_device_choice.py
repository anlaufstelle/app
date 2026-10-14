"""The upstream OTP form requires an explicit device choice (Refs #2593)."""

from html.parser import HTMLParser

import pytest
from django_otp.forms import OTPTokenForm
from django_otp.plugins.otp_totp.models import TOTPDevice


class OptionParser(HTMLParser):
    """Collect rendered select options through the public form interface."""

    def __init__(self):
        super().__init__()
        self.options = []

    def handle_starttag(self, tag, attrs):
        if tag == "option":
            self.options.append(dict(attrs))


@pytest.mark.django_db
def test_otp_form_does_not_silently_select_a_registered_device(staff_user):
    first = TOTPDevice.objects.create(user=staff_user, name="first", confirmed=True)
    second = TOTPDevice.objects.create(user=staff_user, name="second", confirmed=True)
    form = OTPTokenForm(staff_user)
    parser = OptionParser()
    parser.feed(str(form["otp_device"]))

    assert parser.options[0]["value"] == ""
    assert {option["value"] for option in parser.options[1:]} == {
        first.persistent_id,
        second.persistent_id,
    }
    assert not any("selected" in option and option["value"] for option in parser.options)
