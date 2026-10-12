"""Public pending-history count pins.

Source: mutation delta of the release-0.99.7 checkpoint, 6 candidate mutants.
Groups:

* ``pending_history_count`` scope (2): only the person's own facility events.
* ``pending_history_count`` payloads (4): only anonymized people and versions
  with retained values, including values present solely before the change.

The facility-only candidate is equivalent under the composite event/client
facility foreign key and is not pinned. These tests exercise the public service without ``rls_test_role``.
"""

import pytest
from django.utils import timezone

from core.models import EventHistory
from core.services.client.history_status import pending_history_count
from core.services.events.history_diff import build_anonymization_marker
from tests._factories import make_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def history_event(facility, client_identified, doc_type_contact, staff_user):
    """An event without automatically inserted history, for explicit version payloads."""
    return make_event(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


def _version(event, before=None, after=None):
    """Append a history version through the normal model API."""
    return EventHistory.objects.create(
        event=event, action=EventHistory.Action.UPDATE, data_before=before, data_after=after
    )


def _anonymized(person):
    """Persist anonymization state without invoking the separate retention workflow."""
    person.anonymized_at = timezone.now()
    person.save(update_fields=["anonymized_at"])
    return person


def test_count_excludes_another_person(history_event, client_identified, client_qualified):
    """A person's count excludes retained history belonging to another person nearby.

    Kills x_pending_history_count__mutmut_11.
    """
    _version(history_event, after={"note": "Other person's retained value"})
    assert pending_history_count(_anonymized(client_qualified)) == 0
    assert pending_history_count(_anonymized(client_identified)) == 1


@pytest.mark.parametrize("before,after", [(None, None), ({}, {}), (None, {}), ("marker", "marker")])
def test_empty_and_redacted_versions_are_complete(history_event, client_identified, before, after):
    """Empty payloads and nonempty redaction markers never count as pending versions.

    Kills x_pending_history_count__mutmut_18 and x_pending_history_count__mutmut_20.
    """
    if before == "marker":
        before = build_anonymization_marker()
        after = build_anonymization_marker()
    _version(history_event, before=before, after=after)
    assert pending_history_count(_anonymized(client_identified)) == 0


def test_non_anonymized_person_has_no_pending_count(history_event, client_identified):
    """A live person has zero pending anonymization versions despite retained history.

    Kills x_pending_history_count__mutmut_2.
    """
    _version(history_event, before={"note": "Retained before"}, after={"note": "Retained after"})
    assert pending_history_count(client_identified) == 0


@pytest.mark.parametrize("after", [None, {}, "marker"])
def test_before_only_values_count_as_one_pending_version(history_event, client_identified, after):
    """Retained before-values count even when after-values are empty or redacted.

    Kills x_pending_history_count__mutmut_6.
    """
    if after == "marker":
        after = build_anonymization_marker()
    _version(history_event, before={"note": "Retained before"}, after=after)
    assert pending_history_count(_anonymized(client_identified)) == 1
