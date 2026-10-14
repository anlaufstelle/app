"""Chart labels and errors are supplied by the active gettext language."""

from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.utils.translation import gettext, override


@pytest.mark.parametrize("language", ["de", "en"])
def test_chart_template_supplies_translated_labels(language):
    with override(language):
        html = render_to_string("core/statistics/partials/dashboard_content.html", {})
        for attr, msgid in [
            ("total", "Gesamt"),
            ("anonymous", "Anonym"),
            ("pseudonym", "Nur Pseudonym"),
            ("qualified", "Mit Zusatzangaben"),
            ("contacts", "Kontakte"),
            ("source", "Quelle: "),
            ("snapshot", "Snapshot"),
            ("live", "Live-Daten"),
            ("error", "Fehler: "),
            ("http", "HTTP-Fehler"),
        ]:
            label = gettext(msgid)
            assert f'data-i18n-{attr}="{label}"' in html
    assert 'x-text="errorText"' in html


def test_chart_javascript_has_no_german_ui_literals():
    source = (Path(__file__).resolve().parents[1] / "static/js/statistics-charts.js").read_text()
    for literal in [
        '"Gesamt"',
        '"Anonym"',
        '"Nur Pseudonym"',
        '"Mit Zusatzangaben"',
        '"Quelle: "',
        '"Live-Daten"',
        '"Kontakte"',
    ]:
        assert literal not in source
