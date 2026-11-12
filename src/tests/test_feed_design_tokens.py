"""Decorative borders must not weaken control boundaries or focus contrast."""

import re
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from tests.test_a11y_banner_contrast import TOKENS, contrast

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.architecture
def test_decorative_token_is_separate_from_controls_and_focus(monkeypatch):
    css = (ROOT / "src/static/css/input.css").read_text()
    config = (ROOT / "tailwind.config.js").read_text()
    for token in ("border", "border-soft", "bg", "accent", "text-muted"):
        match = re.search(rf"--{token}: oklch\(([^)]+)\)", css)
        assert match, token
        monkeypatch.setitem(TOKENS, token, tuple(map(float, match[1].split())))
    assert "'var(--border-soft)'" in config
    assert "subtle-soft" in config
    assert "border-color: var(--border);" in css
    card = re.search(r"\.card\s*\{([^}]+)\}", css)[1]
    assert "border-subtle-soft" in card
    for component in ("btn-secondary", "tally-tile"):
        rule = re.search(rf"\.{component}\s*\{{([^}}]+)\}}", css)[1]
        assert "border-subtle " in rule
        assert "subtle-soft" not in rule
    for background in ("surface", "bg"):
        assert contrast("border", background) >= 3
        assert contrast("accent", background) >= 3
        assert contrast("text-muted", background) >= 4.5
        assert contrast("border-soft", background) < 3
    for path in (ROOT / "src/templates").rglob("*.html"):
        for tag in re.findall(r"<(?:input|select|textarea|button|summary)\b[^>]*>", path.read_text()):
            assert "border-subtle-soft" not in tag, path
    assert "ring-2 ring-accent ring-offset-2" in css


def test_tally_section_label_is_outside_the_decorative_card():
    html = render_to_string(
        "core/zeitstrom/partials/tally_panel.html",
        {"tally_summary": {"rows": [{"document_type": {"pk": "x", "name": "Contact"}, "count": 0}]}},
    )
    section = re.search(r"<section\b[^>]*>", html)[0]
    assert "border-subtle-soft" in section
    assert "text-[12px]" in re.search(r"<h2\b[^>]*>", html)[0]
    assert html.index('id="tally-heading"') < html.index("<section")
