"""Pins for the account forms of the access hub (``core.forms.user_access``).

Source: mutation delta of the release-0.99.6 checkpoint, 15 mutants.
Groups:

* ``deletion_approval_field`` -- the creation-time choice for
  ``can_confirm_deletion``: model label, the three choices ``role``/``yes``/
  ``no`` with their labels, preset ``role``, optional, and the four-eyes help
  text. The function runs once at class definition, so each pin calls it
  directly inside the test.
* ``_UserAccessForm.__init__`` -- the create and the update form explain the
  mandatory second factor in the help text of ``mfa_required``.
"""

import pytest
from django.utils.translation import gettext, override

from core.forms.user_access import UserAccessCreateForm, UserAccessUpdateForm, deletion_approval_field
from core.models import User

BY_ROLE_LABEL = "Nach Rolle: ja für Leitung und Anwendungsbetreuung, sonst nein"
YES_LABEL = "Ja"
NO_LABEL = "Nein"
APPROVAL_HELP_TEXT = (
    "Darf Löschanträge als zweite Person genehmigen; eigene Anträge bleiben ausgeschlossen. "
    "Jede Einrichtung braucht mindestens zwei Konten mit diesem Recht."
)
MFA_REQUIRED_HELP_TEXT = "Erzwingt die Einrichtung eines zweiten Faktors beim nächsten Zugang."


class TestDeletionApprovalField:
    def test_label_is_the_model_verbose_name(self):
        """The field is labelled with the verbose_name of ``can_confirm_deletion``.

        Pins ``x_deletion_approval_field__mutmut_1`` and ``x_deletion_approval_field__mutmut_6``.
        """
        field = deletion_approval_field()

        assert field.label == User._meta.get_field("can_confirm_deletion").verbose_name

    def test_offers_exactly_role_yes_and_no(self):
        """The choice values are ``role``, ``yes`` and ``no`` in this order.

        Pins ``x_deletion_approval_field__mutmut_2`` and ``x_deletion_approval_field__mutmut_7``.
        """
        field = deletion_approval_field()

        assert [value for value, _label in field.choices] == ["role", "yes", "no"]

    def test_choice_labels_render_their_wording(self):
        """Every choice label renders its translated wording.

        Pins ``x_deletion_approval_field__mutmut_14``, ``x_deletion_approval_field__mutmut_18``
        and ``x_deletion_approval_field__mutmut_22``.
        """
        field = deletion_approval_field()

        with override("de"):
            labels = [str(label) for _value, label in field.choices]
            assert labels == [gettext(BY_ROLE_LABEL), gettext(YES_LABEL), gettext(NO_LABEL)]

    def test_is_preset_to_the_role_default(self):
        """The field is preset to ``role``.

        Pins ``x_deletion_approval_field__mutmut_3`` and ``x_deletion_approval_field__mutmut_8``.
        """
        assert deletion_approval_field().initial == "role"

    def test_is_optional(self):
        """The field is optional, so a form without it falls back to the role.

        Pins ``x_deletion_approval_field__mutmut_9`` and ``x_deletion_approval_field__mutmut_26``.
        """
        assert deletion_approval_field().required is False

    def test_help_text_explains_the_four_eyes_rule(self):
        """The help text renders the four-eyes wording.

        Pins ``x_deletion_approval_field__mutmut_5``, ``x_deletion_approval_field__mutmut_10``
        and ``x_deletion_approval_field__mutmut_27``.
        """
        field = deletion_approval_field()

        with override("de"):
            assert str(field.help_text) == gettext(APPROVAL_HELP_TEXT)


@pytest.mark.django_db
class TestMfaRequiredHelpText:
    @pytest.mark.parametrize("form_class", [UserAccessCreateForm, UserAccessUpdateForm], ids=["create", "update"])
    def test_mfa_required_explains_the_second_factor(self, form_class, facility, admin_user):
        """Both account forms explain the mandatory second factor in the help
        text of ``mfa_required``.

        Pins ``xǁ_UserAccessFormǁ__init____mutmut_22``.
        """
        form = form_class(facility=facility, actor=admin_user)

        with override("de"):
            assert str(form.fields["mfa_required"].help_text) == gettext(MFA_REQUIRED_HELP_TEXT)
