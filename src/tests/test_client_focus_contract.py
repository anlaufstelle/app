"""Rendered bindings for the shared combobox and pending case submission."""

from html.parser import HTMLParser

import pytest
from django.urls import reverse


class Tags(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.tags = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["core:event_create", "core:case_create", "core:workitem_create"])
def test_combobox_activation_and_active_option_bindings(client, staff_user, url_name):
    client.force_login(staff_user)
    response = client.get(reverse(url_name))
    assert response.status_code == 200
    tags = Tags(response.content.decode()).tags
    field = next(attrs for _, attrs in tags if attrs.get("aria-label") == "Person suchen")
    option = next(attrs for _, attrs in tags if attrs.get("@mouseenter") == "highlightByEvent")
    listbox = next(attrs for _, attrs in tags if attrs.get("id") == "client-autocomplete-list")
    assert field.get("x-ref") == "clientInput"
    assert field.get(":aria-activedescendant") == "activeDescendant"
    assert field.get(":aria-controls") == "listboxId"
    assert listbox["id"] == "client-autocomplete-list"
    assert listbox.get(":id") == "listboxId"
    assert sum(attrs.get("id") == "client-autocomplete-list" for _, attrs in tags) == 1
    assert option.get("tabindex") == "-1"
    assert option.get("@click") == "selectByEvent"
    assert option.get("@mousedown.prevent") == ""
    assert option.get(":id") == "c.optionId"
    assert option["type"] == "button"
    assert option["@mouseenter"] == "highlightByEvent"
    assert option[":class"] == "c.highlightClass"


@pytest.mark.django_db
def test_pending_case_submission_has_all_three_rendered_bindings(client, staff_user):
    client.force_login(staff_user)
    tags = Tags(client.get(reverse("core:event_create")).content.decode()).tags
    form = next(attrs for tag, attrs in tags if tag == "form" and attrs.get("id") == "event-create-form")
    assert form["@submit"] == "guardCaseContext"
    for button_id in ("event-submit-btn", "event-submit-next"):
        button = next(attrs for _, attrs in tags if attrs.get("id") == button_id)
        assert button[":disabled"] == "caseContextPending"
        assert button["type"] == "submit"
