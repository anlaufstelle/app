"""Pins for the missing-origin error message (``core.services.security.origin``).

Source: mutation delta of checkpoint release-0.99.5, 1 mutant in
``get_external_origin``: the ``ImproperlyConfigured`` raised for a missing
``EXTERNAL_ORIGIN`` loses its message. The operator then sees the exception
type without the name of the setting that has to be configured. The pins check
the message for an empty and for an absent value, directly and through
``build_external_url``.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.services.security.origin import build_external_url, get_external_origin


class TestGetExternalOrigin:
    """``get_external_origin``: a missing origin fails loudly and names the setting."""

    @pytest.mark.parametrize("value", ["", None], ids=["empty", "none"])
    def test_missing_origin_names_setting(self, settings, value):
        """x_get_external_origin__mutmut_14: ``ImproperlyConfigured(None)`` drops the message."""
        settings.EXTERNAL_ORIGIN = value

        with pytest.raises(ImproperlyConfigured, match="EXTERNAL_ORIGIN"):
            get_external_origin()

    def test_link_builder_reports_missing_setting(self, settings):
        """x_get_external_origin__mutmut_14 through the public link builder."""
        del settings.EXTERNAL_ORIGIN

        with pytest.raises(ImproperlyConfigured, match="EXTERNAL_ORIGIN"):
            build_external_url("/password-reset/")
