"""Rendered privacy packages explain actual retention anchors and approvals."""

import re

import pytest

from core.services.client import render_document

pytestmark = pytest.mark.django_db


def test_information_duties_scope_independent_contact_deadlines(facility, settings_obj):
    content, _ = render_document("informationspflichten", facility)
    flat = " ".join(content.split())
    assert "Jeder dokumentierte Kontakt hat seine eigene Frist" not in flat
    assert "Bei anonymen Kontakten und „Nur Pseudonym“" in flat
    assert "Ein neuer Kontakt im offenen Fall kann deshalb auch die Frist älterer Kontakte verschieben" in flat


@pytest.mark.parametrize("slug", ["informationspflichten", "dsfa", "toms", "verarbeitungsverzeichnis", "loeschkonzept"])
def test_rendered_packages_explain_review_and_explicit_auto_approval_exception(facility, settings_obj, slug):
    content, _ = render_document(slug, facility)
    flat = " ".join(content.split())
    assert "Löschbestätigung" in flat
    assert "Auto-Freigabe" in flat and "Opt-in, standardmäßig aus" in flat
    assert "maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage" in flat
    assert "ohne zweite Person" in flat
    assert "der automatische Aufbewahrungslauf löscht ohne Freigabe" not in flat
    assert "Der automatische Aufbewahrungslauf fällt nicht unter dieses Prinzip" not in flat


def test_rendered_package_hash_changes_with_template_content(facility, settings_obj, monkeypatch):
    from pathlib import Path

    from core.services.client import dsgvo_package

    before, _ = render_document("informationspflichten", facility)
    original_read = Path.read_bytes

    def changed_template(path):
        content = original_read(path)
        if path == dsgvo_package.TEMPLATE_DIR / "informationspflichten.md":
            return content + b"\nUpdated retention statement\n"
        return content

    monkeypatch.setattr(Path, "read_bytes", changed_template)
    after, _ = render_document("informationspflichten", facility)
    pattern = r"Settings-Hash: ([a-f0-9]{8})"
    assert re.search(pattern, before).group(1) != re.search(pattern, after).group(1)
