"""Pins for the lock order of the foreign key pre-lock of an attachment batch (``core.services.attachments.crud``).

Source: mutation delta of the checkpoint release-0.99.5, 1 mutant.
Group:

* ``_new_row_fk_targets`` — the union of the targets of all new rows is
  sorted by table only; within one table (several field templates of one
  batch) the ``FOR KEY SHARE`` locks follow the iteration order of a set
  instead of the primary key.

Two batches with overlapping field templates must take the shared rows in the
same order (table, then primary key), otherwise they can block each other
crosswise. ``lock_fk_targets`` locks in the order it is handed and does not
sort itself, so the order of the target list is the lock order. The pin hands
the field templates in descending key order and observes the executed
``FOR KEY SHARE`` statements on the field template table
(``tests._row_lock_helpers``: PostgreSQL plans the captured statement; what
counts is the locked table, the lock strength and the filter value, not the
literal form). With ten templates, a set iteration order that happens to be
sorted is practically excluded.
"""

import pytest
from django.db import transaction

from core.models import FieldTemplate
from core.services.attachments.crud import lock_batch_attachment_rows
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

FIELD_TEMPLATE_TABLE = "core_fieldtemplate"
TEMPLATE_COUNT = 10


@pytest.fixture
def file_templates(facility):
    return [
        FieldTemplate.objects.create(
            facility=facility,
            name=f"Attachment field {index}",
            field_type=FieldTemplate.FieldType.FILE,
        )
        for index in range(TEMPLATE_COUNT)
    ]


def _first_lock_order(locks, templates):
    """The templates in the order of their first observed lock."""
    order = []
    for lock in locks:
        matches = [template for template in templates if lock.traegt(id=[template.pk])]
        assert len(matches) == 1, lock
        if matches[0] not in order:
            order.append(matches[0])
    return order


class TestNewRowFkTargets:
    """``_new_row_fk_targets`` as ``lock_batch_attachment_rows`` uses it for new rows."""

    def test_field_templates_are_locked_in_key_order(self, sample_event, staff_user, file_templates):
        """x__new_row_fk_targets__mutmut_20: the sort key loses the primary
        key, so the field template rows were locked in set iteration order
        instead of ascending key order."""
        handed = sorted(file_templates, key=lambda template: str(template.pk), reverse=True)
        with transaction.atomic(), mitschnitt() as queries:
            lock_batch_attachment_rows(sample_event, [], creates_rows=True, user=staff_user, field_templates=handed)
        locks = queries.sperren(FIELD_TEMPLATE_TABLE)
        assert all(lock.sperrart == "KEY SHARE" for lock in locks), locks
        order = _first_lock_order(locks, file_templates)
        assert set(order) == set(file_templates)
        assert [str(t.pk) for t in order] == sorted(str(t.pk) for t in file_templates)
