"""Pins for public case fingerprints and missing actor facilities.

Source: mutation delta of the release-0.99.7 checkpoint, 8 mutants.
Groups: recursive JSON ordering (3), early actor guards (5).
The fingerprint is observed through the public token API, without calling
the private serialization or comparison helpers.
"""

from copy import deepcopy

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import Case
from core.services.case import cases

pytestmark = pytest.mark.django_db


def test_equal_nested_json_has_the_database_form_token(case_open):
    """Recursive JSON key ordering cannot change an unchanged case's form token.

    Kills x__case_form_state_token_from_values__mutmut_3,
    x__case_form_state_token_from_values__mutmut_6 and
    x__case_form_state_token_from_values__mutmut_9 through case_form_state_token.
    """
    data = {"z": {"right": 2, "left": 1}, "a": [{"second": 4, "first": 3}]}
    Case.objects.filter(pk=case_open.pk).update(data_json=data)
    case_open.refresh_from_db()
    rendered = deepcopy(case_open)
    rendered.data_json = {"z": {"right": 2, "left": 1}, "a": [{"second": 4, "first": 3}]}
    reordered = deepcopy(rendered)
    reordered.data_json = {"a": [{"first": 3, "second": 4}], "z": {"left": 1, "right": 2}}
    assert rendered.data_json == reordered.data_json == case_open.data_json
    assert cases.case_form_state_token(rendered) == cases.case_form_state_token(reordered)
    assert cases.case_form_state_token(rendered) == cases.case_form_state_token(case_open)


@pytest.mark.parametrize("actor", [None, AnonymousUser()], ids=["missing", "anonymous"])
@pytest.mark.parametrize("operation", ["create", "update", "close", "reopen", "unlink"])
def test_missing_actor_facility_has_the_domain_error(
    case_open, sample_event, facility, client_identified, actor, operation
):
    """Missing facility attributes produce the domain-specific facility errors.

    Kills x_create_case__mutmut_18, x_update_case__mutmut_5,
    x_close_case__mutmut_5, x_reopen_case__mutmut_5 and
    x_remove_event_from_case__mutmut_5 through their public services.
    """
    with pytest.raises(ValidationError) as error:
        if operation == "create":
            cases.create_case(facility, actor, client_identified, "New case")
        elif operation == "unlink":
            cases.remove_event_from_case(sample_event, actor)
        else:
            getattr(cases, f"{operation}_case")(case_open, actor)
    expected = (
        _("Ereignis gehört zu einer anderen Einrichtung.")
        if operation == "unlink"
        else _("Fall gehört zu einer anderen Einrichtung.")
    )
    assert error.value.messages == [expected]
