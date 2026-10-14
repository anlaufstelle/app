"""The goal partial binds the RAM draft preservation component."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_goal_section_binds_draft_preservation(client, staff_user, case_open):
    client.force_login(staff_user)
    response = client.post(
        reverse("core:goal_create", kwargs={"case_pk": case_open.pk}),
        {"title": "Goal"},
        HTTP_HX_REQUEST="true",
    )
    from html.parser import HTMLParser

    class RootParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if dict(attrs).get("id") == "goals-section":
                self.root = dict(attrs)

    parser = RootParser()
    parser.feed(response.content.decode())
    root = parser.root
    assert root["x-data"] == "goalsSection"
    assert root["@htmx:before-swap.camel"] == "preserveDrafts"
    assert root["tabindex"] == "-1"
