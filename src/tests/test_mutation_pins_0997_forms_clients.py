"""Pins for contact stage form validation.

Source: mutation delta of the release-0.99.7 checkpoint, 5 mutants.
Groups: unchanged qualified people (2), creation lookup (1, not pinned),
and the four-eyes approval message (2).
"""

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _

from core.forms.clients import ClientForm
from core.models import Client, Event

pytestmark = pytest.mark.django_db


def _form(person, stage):
    return ClientForm(
        data={
            "pseudonym": person.pseudonym,
            "contact_stage": stage,
            "age_cluster": person.age_cluster,
            "notes": person.notes,
        },
        instance=person,
        facility=person.facility,
    )


@pytest.mark.parametrize("has_event", [True, False], ids=["with-event", "without-event"])
def test_unchanged_qualified_stage_is_valid(client_qualified, doc_type_contact, has_event):
    """Keeping a qualified stage succeeds with and without events:
    xǁClientFormǁclean_contact_stage__mutmut_10,
    xǁClientFormǁclean_contact_stage__mutmut_11.
    """
    if has_event:
        Event.objects.create(
            facility=client_qualified.facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
        )
    form = _form(client_qualified, Client.ContactStage.QUALIFIED)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["contact_stage"] == Client.ContactStage.QUALIFIED


def test_downgrade_field_error_explains_four_eyes(client_qualified, doc_type_contact):
    """A forbidden downgrade names the approval route:
    xǁClientFormǁclean_contact_stage__mutmut_16,
    xǁClientFormǁclean_contact_stage__mutmut_17.
    """
    Event.objects.create(
        facility=client_qualified.facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
    )
    form = _form(client_qualified, Client.ContactStage.IDENTIFIED)

    assert not form.is_valid()
    assert list(form.errors["contact_stage"]) == [
        _("Bei vorhandenen Ereignissen ist ein Herabstufungsantrag mit Vier-Augen-Freigabe erforderlich.")
    ]
