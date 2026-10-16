"""Pins for the client creation form's inactive-type boundary.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: unsaved assigned inactive type (1), redundant creation lookup (1, not pinned).
"""

import pytest

from core.forms.clients import ClientForm
from core.models import Client, DocumentType

pytestmark = pytest.mark.django_db


def test_unsaved_person_cannot_select_a_preassigned_inactive_type(facility, doc_type_client_profile):
    """Creation rejects an inactive preassignment: xǁClientFormǁ__init____mutmut_30."""
    DocumentType.objects.filter(pk=doc_type_client_profile.pk).update(is_active=False)
    doc_type_client_profile.refresh_from_db()
    person = Client(facility=facility, document_type=doc_type_client_profile, pseudonym="New person")
    form = ClientForm(
        data={
            "pseudonym": person.pseudonym,
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "document_type": str(doc_type_client_profile.pk),
        },
        instance=person,
        facility=facility,
    )

    assert person.pk is not None and person._state.adding
    assert not form.fields["document_type"].queryset.filter(pk=doc_type_client_profile.pk).exists()
    assert not form.is_valid()
    assert [error.code for error in form.errors.as_data()["document_type"]] == ["invalid_choice"]
    assert person._state.adding
