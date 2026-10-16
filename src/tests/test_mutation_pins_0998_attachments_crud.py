"""Pins for attachment purge scoping and the public lock return contract.

Source: mutation delta of the release-0.99.8 checkpoint, 4 mutants.
Groups: facility-bound owner reload (1), scalar ordered lock results (3).
"""

from copy import deepcopy
from uuid import UUID

import pytest
from django.db import transaction

from core.models import Attachment, Event
from core.services.attachments import purge_attachment_chain
from core.services.attachments.crud import lock_attachment_rows_in_facility_order

pytestmark = pytest.mark.django_db


def test_purge_rejects_stale_owner_facility(sample_event, authz_attachment, second_facility):
    """A stale facility cannot reload an anonymous owner from a different facility.

    x_purge_attachment_chain__mutmut_35.
    """
    marker = {"__files__": True, "entries": [{"id": str(authz_attachment.pk), "sort": 0}]}
    data = {authz_attachment.field_template.slug: marker}
    Event.objects.filter(pk=sample_event.pk).update(client=None, is_anonymous=True, data_json=data)
    sample_event.refresh_from_db()
    stale = deepcopy(sample_event)
    stale.facility = second_facility
    with pytest.raises(Event.DoesNotExist):
        purge_attachment_chain(stale, authz_attachment.entry_id)
    sample_event.refresh_from_db()
    assert sample_event.data_json == data
    assert Attachment.objects.filter(pk=authz_attachment.pk).exists()


def test_lock_returns_ordered_scalar_primary_keys(sample_event, authz_attachment):
    """The non-deleting lock API returns sorted scalar keys for selected chains.

    x_lock_attachment_rows_in_facility_order__mutmut_21,
    x_lock_attachment_rows_in_facility_order__mutmut_23,
    x_lock_attachment_rows_in_facility_order__mutmut_29.
    """
    smaller = deepcopy(authz_attachment)
    smaller.pk = UUID(int=1)
    smaller.entry_id = UUID(int=2)
    smaller.save(force_insert=True)
    with transaction.atomic():
        result = lock_attachment_rows_in_facility_order(
            sample_event, [authz_attachment.entry_id, smaller.entry_id], deletes_rows=False
        )
    assert result == sorted([authz_attachment.pk, smaller.pk])
