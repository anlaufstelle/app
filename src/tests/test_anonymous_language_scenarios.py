"""Translation extraction and rendered scenario forms (Refs #2609)."""

import runpy
from html.parser import HTMLParser
from unittest.mock import patch

import pytest

from core.seed import scenarios

pytestmark = pytest.mark.django_db


def test_scenario_display_texts_are_marked_for_extraction():
    with patch("django.utils.translation.gettext_noop", side_effect=lambda text: text) as marker:
        catalog = runpy.run_path(scenarios.__file__)["SCENARIOS"]
    texts = [
        getattr(scenario, field) for scenario in catalog for field in ("display_name", "description", "intro_hint")
    ]
    assert marker.call_count == len(texts)
    assert [call.args[0] for call in marker.call_args_list] == texts


def test_demo_translates_all_scenario_display_texts(client, settings):
    settings.DEMO_MODE = True
    texts = [
        getattr(scenario, field)
        for scenario in scenarios.SCENARIOS
        for field in ("display_name", "description", "intro_hint")
    ]
    translations = {text: f"Translated scenario text {index}" for index, text in enumerate(texts)}
    with patch("django.utils.translation._trans.gettext", side_effect=lambda text: translations.get(text, text)):
        response = client.get("/demo/?lang=en")
    assert response.status_code == 200
    for translated in translations.values():
        assert translated in response.content.decode()


class ScenarioFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and "data-demo-scenario-form" in attrs:
            self.current = {}
        if tag == "input" and self.current is not None and attrs.get("type") == "hidden":
            self.current[attrs.get("name")] = attrs.get("value")

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None


def test_each_demo_start_form_has_hidden_area(client, settings, staff_user):
    settings.DEMO_MODE = True
    with patch("core.views.demo_scenarios.facility_for_scenario", return_value=(staff_user.facility, True)):
        response = client.get("/demo/")
    parser = ScenarioFormParser()
    parser.feed(response.content.decode())
    assert len(parser.forms) == 2 * len(scenarios.SCENARIOS)
    for form in parser.forms:
        assert form["bereich"] == form["scenario"]
