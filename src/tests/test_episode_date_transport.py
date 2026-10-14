"""Editing an episode preserves the rendered ISO date range (Refs #2536)."""

from datetime import date
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from core.services.case.episodes import episode_basis_token

pytestmark = pytest.mark.django_db


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.values[attrs["name"]] = attrs.get("value", "")


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("closed", [False, True])
def test_title_edit_preserves_rendered_date_range(client, staff_user, case_open, episode, language, closed):
    staff_user.preferred_language = language
    staff_user.save(update_fields=["preferred_language"])
    episode.started_at = date(2026, 10, 1)
    episode.ended_at = date(2026, 10, 7) if closed else None
    episode.save(update_fields=["started_at", "ended_at"])
    client.force_login(staff_user)
    url = reverse("core:episode_update", kwargs={"case_pk": case_open.pk, "pk": episode.pk})
    response = client.get(url, HTTP_ACCEPT_LANGUAGE="en-US" if language == "de" else "de")
    assert response.status_code == 200
    inputs = Inputs(response.content.decode()).values
    assert inputs["started_at"] == "2026-10-01"
    assert inputs["ended_at"] == ("2026-10-07" if closed else "")
    response = client.post(
        url,
        {
            "title": "Changed title",
            "description": episode.description,
            "started_at": inputs["started_at"],
            "ended_at": inputs["ended_at"],
            "basis_token": episode_basis_token(episode),
        },
    )
    assert response.status_code == 302
    episode.refresh_from_db()
    assert episode.title == "Changed title"
    assert episode.started_at == date(2026, 10, 1)
    assert episode.ended_at == (date(2026, 10, 7) if closed else None)
