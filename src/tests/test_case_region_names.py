"""Case detail: the event and goal live regions are named by their headings."""

from collections import Counter
from html.parser import HTMLParser

import pytest
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


@pytest.fixture
def two_milestones(outcome_goal):
    open_ms = Milestone.objects.create(goal=outcome_goal, title="Wohnung finden")
    done_ms = Milestone.objects.create(goal=outcome_goal, title="Antrag stellen", is_completed=True)
    return open_ms, done_ms


def test_case_regions_are_named_by_their_headings(client, staff_user, case_open):
    client.force_login(staff_user)
    dom = _dom(client.get(reverse("core:case_detail", args=[case_open.pk])).content.decode())

    for region_id, heading_id, heading_text in (
        ("case-event-list", "case-events-heading", "Kontakte"),
        ("goals-section", "goals-section-heading", "Wirkungsziele"),
    ):
        tag, attrs = dom.tags[region_id]
        assert attrs["role"] == "region" and attrs["aria-live"] == "polite"
        assert attrs["aria-labelledby"] == heading_id
        assert dom.tags[heading_id][0] == "h2"
        assert dom.texts[heading_id].strip() == heading_text
    assert not [i for i, n in Counter(dom.ids).items() if n > 1]


def test_goals_region_keeps_its_name_after_a_htmx_swap(client, staff_user, case_open, two_milestones):
    client.force_login(staff_user)
    open_ms, _ = two_milestones
    response = client.post(
        reverse("core:milestone_toggle", kwargs={"case_pk": case_open.pk, "pk": open_ms.pk}),
        HTTP_HX_REQUEST="true",
    )
    dom = _dom(response.content.decode())

    tag, attrs = dom.tags["goals-section"]
    assert attrs["aria-labelledby"] == "goals-section-heading"
    assert attrs["aria-live"] == "polite" and attrs["tabindex"] == "-1"
    assert dom.texts["goals-section-heading"].strip() == "Wirkungsziele"
    assert not [i for i, n in Counter(dom.ids).items() if n > 1]
