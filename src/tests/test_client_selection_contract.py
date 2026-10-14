"""Selection text and submitted client identity have separate contracts."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["core:event_create", "core:case_create", "core:workitem_create"])
def test_unconfirmed_text_hint_and_immediate_input_binding(client, staff_user, url_name):
    client.force_login(staff_user)
    response = client.get(reverse(url_name))
    assert response.status_code == 200
    body = response.content.decode()
    assert '@input="setQuery"' in body
    assert '@input.debounce.150ms="onInput"' in body
    assert ':value="query"' in body
    assert 'name="client" :value="selectedId"' in body
    assert 'x-show="unconfirmedQuery"' in body
    assert "Keine Person ausgewählt. Bitte einen Treffer aus der Liste wählen." in body


@pytest.mark.django_db
def test_case_post_without_person_reports_existing_required_message(client, staff_user):
    client.force_login(staff_user)
    response = client.post(reverse("core:case_create"), {"client": "", "title": "Case without person"})
    assert response.status_code == 200
    assert "Bitte eine Person auswählen — Fälle müssen einer Person zugeordnet sein." in response.content.decode()
