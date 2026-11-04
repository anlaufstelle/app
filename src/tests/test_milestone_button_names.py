"""Milestone toggle buttons carry their visible title in the accessible name."""

from html.parser import HTMLParser

import pytest
from django.conf import settings
from django.urls import reverse

from core.models import Milestone

pytestmark = pytest.mark.django_db


class _Dom(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.tags = {}
        self.texts = {}  # id -> visible text (heading text)
        self.buttons = []  # (attrs, accessible text)
        self._open_id = None
        self._button = None
        self._svg_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
            self.tags[attrs["id"]] = (tag, attrs)
            self.texts.setdefault(attrs["id"], "")
            self._open_id = attrs["id"]
        if tag == "button" and attrs.get("type") == "submit":
            self._button = [attrs, ""]
            self.buttons.append(self._button)
        if tag == "svg":
            self._svg_depth += 1

    def handle_endtag(self, tag):
        if tag in ("h2", "h3"):
            self._open_id = None
        if tag == "button":
            self._button = None
        if tag == "svg":
            self._svg_depth -= 1

    def handle_data(self, data):
        if self._open_id:
            self.texts[self._open_id] += data
        if self._button is not None and not self._svg_depth:
            self._button[1] += data


def _dom(html):
    dom = _Dom()
    dom.feed(html)
    return dom


def _milestone_buttons(dom, titles):
    found = {}
    for attrs, text in dom.buttons:
        name = " ".join(attrs.get("aria-label", text).split())
        for title in titles:
            if title in name:
                found[title] = (attrs, name)
    return found


@pytest.fixture
def two_milestones(outcome_goal):
    open_ms = Milestone.objects.create(goal=outcome_goal, title="Wohnung finden")
    done_ms = Milestone.objects.create(goal=outcome_goal, title="Antrag stellen", is_completed=True)
    return open_ms, done_ms


@pytest.mark.parametrize("language", ["de", "en"])
def test_milestone_buttons_are_distinguishable_by_name(client, staff_user, case_open, two_milestones, language):
    client.force_login(staff_user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
    response = client.get(reverse("core:case_detail", args=[case_open.pk]))
    dom = _dom(response.content.decode())

    found = _milestone_buttons(dom, ["Wohnung finden", "Antrag stellen"])
    assert set(found) == {"Wohnung finden", "Antrag stellen"}  # open and completed entry
    names = [name for _, name in found.values()]
    assert len(set(names)) == 2
    for attrs, name in found.values():
        # A fixed label would override the visible title (label-in-name).
        assert "aria-label" not in attrs
        # The action text stays: translated, behind the visible title.
        assert name.startswith(("Wohnung finden", "Antrag stellen"))
        assert ("toggle milestone" if language == "en" else "umschalten") in name.lower()
