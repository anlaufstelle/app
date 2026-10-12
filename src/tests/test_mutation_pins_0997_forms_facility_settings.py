"""Pins for default settings warnings without a supplied model instance.

Source: mutation delta of the release-0.99.7 checkpoint, 4 mutants.
Group: _current_value fallback is observed through public setting_warnings.
No private form helper is invoked or replaced.
"""

import pytest

from core.forms.facility_settings import FacilitySettingsForm
from core.models import Settings

pytestmark = pytest.mark.django_db


def test_new_settings_form_warnings_use_model_defaults(facility):
    """A form without an instance can render the same warnings as model defaults.

    Kills xǁFacilitySettingsFormǁ_current_value__mutmut_14,
    xǁFacilitySettingsFormǁ_current_value__mutmut_15,
    xǁFacilitySettingsFormǁ_current_value__mutmut_16 and
    xǁFacilitySettingsFormǁ_current_value__mutmut_17 through setting_warnings.
    """
    form = FacilitySettingsForm(facility=facility)
    reference = FacilitySettingsForm(facility=facility, instance=Settings())
    assert form.initial == {}
    warnings = form.setting_warnings
    assert isinstance(warnings, list)
    assert warnings == reference.setting_warnings
