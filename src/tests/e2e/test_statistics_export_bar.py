"""E2E: Eine Exportleiste für den ganzen Statistik-Bereich (Refs #2505).

Beobachtetes Verhalten (manuell, E2E-Server): Die Übersicht bietet nur die
interne Ereignisliste im Warnkasten, die Datensatz-Berichte bieten unter den
Karten CSV, JSON, XML und PDF mit Empfänger/Zweck davor und der Quelle je
Format darunter. Jeder Download erscheint unter „Freigaben“; die alte externe
Adresse leitet auf die Karte „Allgemeine Kennzahlen“ um.

Wait-Strategie: domcontentloaded + Anchor-First (locator.wait_for), nie
networkidle.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

CARD_URL = "/statistics/?ansicht=datensaetze&bericht=general-key-figures"
FORMATS = (
    ("csv", "CSV-Export", ".csv"),
    ("json", "JSON-Export", ".json"),
    ("xml", "XML-Export", ".xml"),
    ("pdf", "PDF-Export", ".pdf"),
)


def _open_card(page, base_url, path=CARD_URL):
    page.goto(f"{base_url}{path}")
    page.wait_for_load_state("domcontentloaded")
    bar = page.get_by_test_id("export-bar")
    bar.wait_for(timeout=30_000)
    return bar


def test_overview_shows_only_the_internal_event_list(authenticated_page, base_url):
    page = authenticated_page
    page.goto(f"{base_url}/statistics/")
    form = page.get_by_test_id("statistics-export-form")
    form.wait_for(timeout=30_000)

    expect(form.locator("button")).to_have_count(1)
    expect(form.locator("button")).to_contain_text("Ereignisliste (CSV, intern, mit Pseudonymen)")
    expect(form).to_contain_text("Enthält Einzelereignisse mit Pseudonym")
    assert page.get_by_test_id("export-bar").count() == 0


def test_report_card_shows_the_bar_with_four_formats_and_sources(authenticated_page, base_url):
    page = authenticated_page
    bar = _open_card(page, base_url)

    for _fmt, label, _suffix in FORMATS:
        expect(bar.locator(f"button:has-text('{label}')")).to_be_visible()
    expect(bar.locator("#report-recipient")).to_be_visible()
    expect(bar.locator("#report-purpose")).to_be_visible()
    expect(bar).to_contain_text("Jeder Download wird protokolliert")
    expect(page.get_by_test_id("export-bar-sources")).to_contain_text("Quelle je Format")
    # Position: unter den Karten, über der Vorschau.
    cards = page.get_by_test_id("report-card-list").bounding_box()
    bar_box = bar.bounding_box()
    preview = page.locator("#report-panel").bounding_box()
    assert cards["y"] < bar_box["y"] < preview["y"]


def test_enter_in_the_recipient_field_does_not_download(authenticated_page, base_url):
    """Enter darf keine Herausgabe auslösen (Refs #1776)."""
    page = authenticated_page
    _open_card(page, base_url)
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    url_before = page.url

    page.fill("#report-recipient", "Stadt Enter-Test")
    page.press("#report-recipient", "Enter")
    page.wait_for_timeout(1_000)

    assert downloads == []
    assert page.url == url_before
    assert "/statistics/reports/export/" not in page.url


@pytest.mark.parametrize(("fmt", "label", "suffix"), FORMATS)
def test_each_format_download_is_released(authenticated_page, base_url, fmt, label, suffix):
    page = authenticated_page
    bar = _open_card(page, base_url)
    recipient = f"Stadt E2E-Leiste {fmt}"

    page.fill("#report-recipient", recipient)
    page.fill("#report-purpose", "Verwendungsnachweis E2E")
    with page.expect_download() as download_info:
        bar.locator(f"button:has-text('{label}')").click()
    download = download_info.value

    assert download.suggested_filename.startswith("general-key-figures_")
    assert download.suggested_filename.endswith(suffix)
    if fmt == "pdf":
        assert download.path().read_bytes()[:4] == b"%PDF"

    page.goto(f"{base_url}/statistics/reports/releases/")
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_text(recipient).first).to_be_visible(timeout=30_000)


def test_old_external_address_redirects_to_the_card(authenticated_page, base_url):
    page = authenticated_page

    page.goto(f"{base_url}/statistics/external/?format=json&zeitraum=letzte90")
    page.wait_for_load_state("domcontentloaded")
    page.get_by_test_id("export-bar").wait_for(timeout=30_000)

    assert "bericht=general-key-figures" in page.url, page.url
    assert "zeitraum=letzte90" in page.url, page.url
    expect(page.locator("#report-panel h3")).to_have_text("Allgemeine Kennzahlen")
