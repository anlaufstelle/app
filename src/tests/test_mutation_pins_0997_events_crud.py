"""Pins for event form fingerprints and the locked deletion recheck.

Source: mutation delta of the release-0.99.7 checkpoint, 11 mutants.
Groups: recursive JSON ordering (3), facility recheck message (1).
Seven service search-index mutations are equivalent with the real pre_save
receiver: it rebuilds the index before every relevant persistence operation.
"""

from copy import deepcopy

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Event
from core.services.client import restriction
from core.services.events import crud

pytestmark = pytest.mark.django_db


def test_equal_nested_json_has_the_database_form_token(sample_event):
    """Equal JSON objects share a token regardless of recursive insertion order.

    Kills x__event_form_state_token_from_values__mutmut_3,
    x__event_form_state_token_from_values__mutmut_6 and
    x__event_form_state_token_from_values__mutmut_9 through event_form_state_token.
    """
    data = {"z": {"right": 2, "left": 1}, "a": [{"second": 4, "first": 3}]}
    Event.objects.filter(pk=sample_event.pk).update(data_json=data)
    sample_event.refresh_from_db()
    rendered = deepcopy(sample_event)
    rendered.data_json = {"z": {"right": 2, "left": 1}, "a": [{"second": 4, "first": 3}]}
    reordered = deepcopy(rendered)
    reordered.data_json = {"a": [{"first": 3, "second": 4}], "z": {"left": 1, "right": 2}}
    assert rendered.data_json == reordered.data_json == sample_event.data_json
    assert crud.event_form_state_token(rendered) == crud.event_form_state_token(reordered)
    assert crud.event_form_state_token(rendered) == crud.event_form_state_token(sample_event)


def test_locked_deletion_recheck_keeps_the_facility_message(sample_event, staff_user, second_facility, monkeypatch):
    """A facility change after the first guard keeps the explicit domain message.

    Kills x_soft_delete_event__mutmut_22 through the rows_locked public argument.
    """
    original = restriction.assert_processing_allowed
    reached = []

    def move_actor_after_restriction_check(*args, **kwargs):
        original(*args, **kwargs)
        reached.append(True)
        staff_user.facility_id = second_facility.pk

    monkeypatch.setattr(restriction, "assert_processing_allowed", move_actor_after_restriction_check)
    with transaction.atomic(), pytest.raises(ValidationError) as error:
        crud.soft_delete_event(sample_event, staff_user, rows_locked=True)
    assert reached
    assert error.value.messages == [crud._EVENT_OTHER_FACILITY_MESSAGE]
    sample_event.refresh_from_db()
    assert not sample_event.is_deleted
