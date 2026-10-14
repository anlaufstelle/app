"""An event whose creator no longer exists (``created_by`` is ``SET_NULL``) still renders."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event


@pytest.fixture
def orphaned_event(facility, client_identified, doc_type_contact):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 5},
        created_by=None,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("view_name", ["core:event_list", "core:zeitstrom"])
def test_lists_render_event_without_creator(client, staff_user, orphaned_event, view_name):
    client.force_login(staff_user)
    response = client.get(reverse(view_name))
    assert response.status_code == 200


@pytest.mark.django_db
def test_detail_renders_event_without_creator(client, staff_user, orphaned_event):
    client.force_login(staff_user)
    response = client.get(reverse("core:event_detail", args=[orphaned_event.pk]))
    assert response.status_code == 200
    assert "Erstellt von" in response.content.decode()
