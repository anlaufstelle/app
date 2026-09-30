"""Pins for the e-mail hint of the user access forms (``core.forms.user_access``).

Source: mutation delta of checkpoint release-0.99.5, 1 mutant in
``_UserAccessForm.__init__``: the help text of the e-mail field is dropped.
The field then no longer tells the admin that the personal setup link goes to
this address, and the rendered input loses its ``aria-describedby`` reference
to the hint. Both concrete forms (create and update) share the initialiser, so
both are pinned.
"""

import pytest
from django.utils import translation

from core.forms.user_access import UserAccessCreateForm, UserAccessUpdateForm

pytestmark = pytest.mark.django_db


def _form(form_class, facility, actor, staff_user):
    if form_class is UserAccessUpdateForm:
        return form_class(instance=staff_user, facility=facility, actor=actor)
    return form_class(facility=facility, actor=actor)


class TestUserAccessFormInit:
    """``_UserAccessForm.__init__``: the e-mail field explains the setup link."""

    @pytest.mark.parametrize("form_class", [UserAccessCreateForm, UserAccessUpdateForm], ids=["create", "update"])
    def test_email_field_announces_setup_link(self, form_class, facility, admin_user, staff_user):
        """xǁ_UserAccessFormǁ__init____mutmut_15: ``help_text = None`` removes the hint
        and its accessible reference from the rendered e-mail input.
        """
        with translation.override("de"):
            bound = _form(form_class, facility, admin_user, staff_user)["email"]

            assert "Setup-Link" in str(bound.help_text)
            assert 'aria-describedby="id_email_helptext"' in str(bound)
