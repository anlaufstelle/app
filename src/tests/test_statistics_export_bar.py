"""One export bar for the whole statistics area (Refs #2505).

The overview offers only the internal event list, the report cards offer CSV,
JSON, XML and PDF through the generic report export. Both come from one partial
and are the only place in the area that carries a download target.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from core.models import AuditLog, DocumentType

CARDS = ("general-key-figures", "sample-report-1")
HIDDEN_RE = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)">')
FORMACTION_RE = re.compile(r'formaction="([^"]+)"')


@pytest.fixture
def statistics_document_type(facility):
    return DocumentType.objects.create(
        facility=facility, name="Kontakt (Export-Leiste)", entity_type=DocumentType.EntityType.EVENT
    )


def _report_page(client, user, slug, **extra):
    client.force_login(user)
    response = client.get(reverse("core:statistics"), {"ansicht": "datensaetze", "bericht": slug, **extra})
    assert response.status_code == 200
    return response.content.decode()


def _bar(html: str) -> str:
    start = html.index('data-testid="export-bar"')
    return html[html.rindex("<form", 0, start) : html.index("</form>", start)]


@pytest.mark.django_db
class TestReportCardsBar:
    @pytest.mark.parametrize("slug", CARDS)
    def test_every_card_offers_four_formats_through_the_generic_export(self, client, lead_user, slug):
        html = _report_page(client, lead_user, slug)
        bar = _bar(html)

        export_url = reverse("core:statistics_report_export")
        assert FORMACTION_RE.findall(bar) == [export_url] * 4
        for fmt in ("csv", "json", "xml", "pdf"):
            assert f'name="format" value="{fmt}"' in bar

    @pytest.mark.parametrize("slug", CARDS)
    def test_no_other_download_target_on_the_card_page(self, client, lead_user, slug):
        html = _report_page(client, lead_user, slug)

        assert len(FORMACTION_RE.findall(html)) == 4
        assert reverse("core:statistics_csv_export") not in html

    def test_bar_sits_between_the_cards_and_the_preview(self, client, lead_user):
        html = _report_page(client, lead_user, "general-key-figures")

        assert html.index('data-testid="report-card-list"') < html.index('data-testid="export-bar"')
        assert html.index('data-testid="export-bar"') < html.index('id="report-panel"')

    def test_bar_carries_recipient_purpose_and_the_release_sentence(self, client, lead_user):
        bar = _bar(_report_page(client, lead_user, "general-key-figures"))

        assert 'name="recipient"' in bar
        assert 'name="purpose"' in bar
        assert "Jeder Download wird protokolliert und erscheint unter" in bar
        assert "nicht eine Person" in bar

    def test_bar_names_the_source_per_format(self, client, lead_user):
        sample = _bar(_report_page(client, lead_user, "sample-report-1"))
        general = _bar(_report_page(client, lead_user, "general-key-figures"))

        assert "Quelle je Format" in sample
        assert "PDF: Monats-Aggregate" in sample
        assert "CSV: Live-Abfrage" in sample
        assert "Monats-Aggregate" not in general
        assert "PDF: Live-Abfrage" in general

    def test_bar_carries_the_period_as_free_range(self, client, lead_user):
        bar = _bar(_report_page(client, lead_user, "general-key-figures", zeitraum="jahr", year="2025"))
        hidden = dict(HIDDEN_RE.findall(bar))

        assert hidden["zeitraum"] == "frei"
        assert hidden["date_from"] == "2025-01-01"
        assert hidden["date_to"] == "2025-12-31"
        assert hidden["backend"] == "general-key-figures"

    def test_no_bar_without_a_selected_card(self, client, lead_user):
        client.force_login(lead_user)
        html = client.get(reverse("core:statistics"), {"ansicht": "datensaetze"}).content.decode()

        assert 'data-testid="export-bar"' not in html
        assert "formaction=" not in html

    def test_releases_tab_has_no_bar(self, client, lead_user):
        client.force_login(lead_user)
        html = client.get(reverse("core:statistics_report_releases")).content.decode()

        assert "formaction=" not in html
        assert 'data-testid="export-bar"' not in html

    @pytest.mark.parametrize("slug", CARDS)
    @pytest.mark.parametrize("fmt", ["csv", "json", "xml", "pdf"])
    def test_submitting_the_bar_releases_the_download(self, client, lead_user, facility, slug, fmt):
        """End to end: exactly what the browser serialises on a button click."""
        bar = _bar(_report_page(client, lead_user, slug, zeitraum="frei", date_from="2026-01-01", date_to="2026-01-31"))
        fields = dict(HIDDEN_RE.findall(bar))
        before = AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count()

        response = client.get(
            FORMACTION_RE.findall(bar)[0],
            {**fields, "recipient": "Stadt Musterstadt", "purpose": "Nachweis", "format": fmt},
        )

        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]
        assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == before + 1
        entry = AuditLog.objects.filter(facility=facility, action=AuditLog.Action.EXPORT).latest("timestamp")
        assert entry.detail["backend"] == slug
        assert entry.detail["format"] == fmt
        assert entry.detail["recipient"] == "Stadt Musterstadt"
        assert entry.detail["artifact_released"] is True

    def test_card_preview_is_not_logged_as_a_release(self, client, lead_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count()

        html = _report_page(client, lead_user, "general-key-figures")

        assert "Allgemeine Kennzahlen" in html
        assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == before


@pytest.mark.django_db
class TestOverviewIsInternal:
    def test_overview_offers_only_the_internal_event_list(self, client, lead_user):
        client.force_login(lead_user)
        html = client.get(reverse("core:statistics")).content.decode()

        assert FORMACTION_RE.findall(html) == [reverse("core:statistics_csv_export")]
        assert 'data-variant="internal"' in html
        assert "Ereignisliste (CSV, intern, mit Pseudonymen)" in html
        assert "Enthält Einzelereignisse mit Pseudonym" in html
        assert "PDF-Export" not in html
        assert "JSON-Export" not in html
        assert "XML-Export" not in html

    def test_internal_variant_is_visually_a_warning_and_has_no_format_buttons(self, client, lead_user):
        client.force_login(lead_user)
        html = client.get(reverse("core:statistics")).content.decode()
        form = html[html.rindex("<form", 0, html.index('data-variant="internal"')) :]
        form = form[: form.index("</form>")]

        assert "amber" in form
        assert 'name="format"' not in form

    def test_document_type_filter_still_reaches_the_event_list(self, client, lead_user, statistics_document_type):
        client.force_login(lead_user)
        html = client.get(reverse("core:statistics"), {"doktyp": str(statistics_document_type.pk)}).content.decode()

        assert f'name="doktyp" value="{statistics_document_type.pk}"' in html

    def test_event_list_download_is_a_release(self, client, lead_user, facility):
        client.force_login(lead_user)
        response = client.get(
            reverse("core:statistics_csv_export"),
            {"date_from": "2026-01-01", "date_to": "2026-01-31", "recipient": "Stadt"},
        )

        assert response.status_code == 200
        entry = AuditLog.objects.filter(facility=facility, action=AuditLog.Action.EXPORT).latest("timestamp")
        assert entry.detail["artifact_released"] is True
        assert entry.detail["recipient"] == "Stadt"


class TestOnlyOnePartialCarriesDownloadTargets:
    """Guard: no ``formaction`` or export link outside the export bar partial."""

    TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "core" / "statistics"
    BAR = "_export_bar.html"
    EXPORT_URL_NAMES = ("statistics_csv_export", "statistics_report_export")

    def _templates(self):
        return sorted(self.TEMPLATES.rglob("*.html"))

    def test_bar_partial_exists(self):
        assert (self.TEMPLATES / "partials" / self.BAR).is_file()

    def test_no_formaction_outside_the_bar(self):
        offenders = [p.name for p in self._templates() if p.name != self.BAR and "formaction" in p.read_text()]
        assert offenders == []

    def test_no_export_route_outside_the_bar(self):
        offenders = []
        for path in self._templates():
            if path.name == self.BAR:
                continue
            text = path.read_text()
            offenders += [f"{path.name}:{name}" for name in self.EXPORT_URL_NAMES if name in text]
        assert offenders == []

    def test_no_download_link_outside_the_bar(self):
        offenders = [
            p.name for p in self._templates() if p.name != self.BAR and re.search(r"\bdownload\b", p.read_text())
        ]
        assert offenders == []

    def test_bar_is_included_by_overview_and_report_section(self):
        contents = {p.name: p.read_text() for p in self._templates()}
        users = sorted(name for name, text in contents.items() if "_export_bar.html" in text)
        assert users == ["dashboard_content.html", "full_content.html"]
