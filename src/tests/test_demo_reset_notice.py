"""The demo entry states the configured reset interval."""

from django.template.loader import render_to_string


def test_demo_entry_discloses_hourly_reset():
    html = render_to_string("auth/demo_scenario_select.html", {})
    assert "Die Demo wird jede volle Stunde zurückgesetzt." in html
