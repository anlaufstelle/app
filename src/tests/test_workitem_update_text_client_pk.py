"""``update_workitem`` treats a person's primary key given as text like the key itself.

Offline replay and JSON callers may pass ``client`` as a raw primary key in
text form. For a work item already linked to that person, the value is the
unchanged link — a no-op for the link, while the other fields are saved. It
must not end in a ``TypeError`` from comparing a UUID with a string.
"""

import pytest

from core.models import AuditLog, Client, WorkItem
from core.services.case import create_workitem, update_workitem

pytestmark = pytest.mark.django_db


def test_text_pk_of_the_linked_person_is_the_unchanged_link(facility, client_identified, staff_user):
    workitem = create_workitem(facility, staff_user, client=client_identified, title="Before")
    audit_before = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count()

    update_workitem(workitem, staff_user, client=str(client_identified.pk), title="After")

    stored = WorkItem.objects.get(pk=workitem.pk)
    assert (stored.title, stored.client_id) == ("After", client_identified.pk)
    assert AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count() == audit_before + 1


def test_text_pk_of_another_person_is_still_refused(facility, client_identified, staff_user):
    other = Client.objects.create(facility=facility, pseudonym="Other person", created_by=staff_user)
    workitem = create_workitem(facility, staff_user, client=client_identified, title="Before")

    with pytest.raises(ValueError):
        update_workitem(workitem, staff_user, client=str(other.pk), title="After")

    stored = WorkItem.objects.get(pk=workitem.pk)
    assert (stored.title, stored.client_id) == ("Before", client_identified.pk)
