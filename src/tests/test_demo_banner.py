"""Demo notice in the page header: one line on small screens, expandable (Refs #2663).

The notice discloses synthetic data and the reset time on a public demo, so it
may fold its details away but never disappear. The browser behaviour lives in
``tests/js/demo-banner.test.mjs``; here the server-rendered markup is pinned,
including what a visitor without JavaScript gets (the complete notice).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from django.test import override_settings
from django.urls import reverse


class _Banner(HTMLParser):
    """Collect element attributes and text inside ``#demo-banner``."""

    _VOID = {"meta", "link", "input", "img", "br", "hr", "source", "path"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = None
        self.stack = []
        self.elements = []  # (tag, attrs, path-of-ids)
        self.text = ""
        self.html_ids = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.html_ids.add(attributes["id"])
        if self.depth is None and attributes.get("id") == "demo-banner":
            self.depth = len(self.stack)
        if self.depth is not None and self.depth >= 0:
            self.elements.append((tag, attributes))
        if tag not in self._VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        while self.stack:
            if self.stack.pop() == tag:
                break
        if self.depth is not None and len(self.stack) == self.depth:
            self.depth = -1  # banner closed; ignore the rest

    def handle_data(self, data):
        if self.depth is not None and self.depth >= 0:
            self.text += data


def _classes(attributes):
    return set((attributes.get("class") or "").split())


@pytest.fixture
def demo_page(client, staff_user, settings_obj):
    with override_settings(DEMO_MODE=True):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
    assert response.status_code == 200
    return response.content.decode()


def _parse(html):
    parser = _Banner()
    parser.feed(html)
    assert parser.elements, "demo banner missing"
    return parser


@pytest.mark.django_db
class TestDemoBanner:
    def test_banner_keeps_its_test_id_and_shows_the_one_line_summary(self, demo_page):
        parsed = _parse(demo_page)
        banner = parsed.elements[0][1]
        assert banner["data-testid"] == "demo-banner"
        assert re.search(r"Demo · synthetische Daten · Reset \d{2}:\d{2}", demo_page)
        summary = [a for t, a in parsed.elements if "data-demo-banner-summary" in a]
        assert len(summary) == 1
        assert "hidden" not in _classes(summary[0]), "the summary must stay visible on every width"
        assert "hidden" not in summary[0]

    def test_toggle_is_a_button_wired_to_the_details(self, demo_page):
        parsed = _parse(demo_page)
        toggles = [(t, a) for t, a in parsed.elements if "data-demo-banner-toggle" in a]
        assert len(toggles) == 1
        tag, toggle = toggles[0]
        assert tag == "button" and toggle["type"] == "button"
        assert toggle["aria-expanded"] == "true", "expanded unless the script folds it"
        assert toggle["aria-controls"] == "demo-banner-details"
        assert "demo-banner-details" in parsed.html_ids
        assert "Details" in parsed.text
        assert "md:hidden" in _classes(toggle)
        assert "hidden" in toggle, "the control stays out of the page until the script runs"

    def test_the_banner_can_fold_but_never_be_dismissed(self, demo_page):
        parsed = _parse(demo_page)
        buttons = [a for t, a in parsed.elements if t == "button"]
        assert len(buttons) == 1, "only the details toggle, no close button"
        assert buttons[0]["aria-controls"] == "demo-banner-details"

    def test_without_javascript_the_complete_notice_is_visible(self, demo_page):
        parsed = _parse(demo_page)
        details = next(a for t, a in parsed.elements if a.get("id") == "demo-banner-details")
        assert "hidden" not in details
        assert "group-data-[collapsed]:max-md:hidden" in _classes(details)
        assert "group" in _classes(parsed.elements[0][1])
        assert "E-Mails & Admin-Funktionen deaktiviert." in parsed.text
        link = next(a for t, a in parsed.elements if a.get("data-testid") == "demo-scenario-switch-link")
        assert link["href"] == reverse("demo_scenario_select")

    def test_desktop_keeps_the_full_sentence_on_one_line(self, demo_page):
        parsed = _parse(demo_page)
        assert re.search(
            r"Demo — synthetische Daten, Reset jede volle Stunde \(nächster: \d{2}:\d{2} Uhr\)\.",
            parsed.text,
        )
        long_variant = [a for t, a in parsed.elements if {"hidden", "md:inline"} <= _classes(a)]
        assert long_variant, "the full sentence is shown from md upwards"

    def test_banner_leaves_room_for_the_quick_exit_button(self, demo_page):
        classes = _classes(_parse(demo_page).elements[0][1])
        assert {"pr-32", "md:pr-48", "py-1"} <= classes
        assert "py-1.5" not in classes

    def test_no_inline_style_or_handler_in_the_banner(self, demo_page):
        for _, attributes in _parse(demo_page).elements:
            assert "style" not in attributes
            assert not any(name.startswith("on") for name in attributes)

    def test_script_is_loaded_only_in_demo_mode(self, demo_page, client, staff_user, settings_obj):
        assert re.search(r'<script src="[^"]*js/demo-banner\.js[^"]*"', demo_page)
        client.force_login(staff_user)
        plain = client.get(reverse("core:zeitstrom")).content.decode()
        assert "demo-banner" not in plain
