"""Pins for visible completeness and data-source notes in the sample PDF.

Source: mutation delta of the release-0.99.7 checkpoint, 6 mutants.
Groups: completeness messages (2), source note (2), source explanation (2).
The actual template renders; only the final HTML-to-PDF conversion is replaced.
"""

from datetime import date
from html import unescape
from html.parser import HTMLParser
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _

from core.reporting.data_source import DATA_SOURCE_EXPLANATIONS, DATA_SOURCE_NOTES, SNAPSHOT_HYBRID
from core.services.system import export

pytestmark = pytest.mark.django_db


class _SourceParagraph(HTMLParser):
    """Read the source disclosure paragraph independently of repeated table notes."""

    def __init__(self):
        super().__init__()
        self.inside = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "p" and "data-source" in dict(attrs).get("class", "").split():
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "p":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.text.append(data)


def test_sample_pdf_html_contains_completeness_and_source_text(facility):
    """The rendered PDF content retains completeness and both source explanations.

    Kills x_render_sample_report_1_pdf__mutmut_19,
    x_render_sample_report_1_pdf__mutmut_20,
    x_render_sample_report_1_pdf__mutmut_21,
    x_render_sample_report_1_pdf__mutmut_22,
    x_render_sample_report_1_pdf__mutmut_23 and
    x_render_sample_report_1_pdf__mutmut_24.
    """
    messages = ("An incomplete month needs review.", "A second completeness notice.")
    with patch.object(export.weasyprint, "HTML") as converter:
        converter.return_value.write_pdf.return_value = b"sample pdf"
        result = export.render_sample_report_1_pdf(
            facility,
            {"total": 0, "unique_clients": 0, "by_category": [], "by_age_cluster": []},
            date(2026, 1, 1),
            date(2026, 6, 30),
            generated_at=timezone.now(),
            completeness_messages=messages,
        )
    assert result == b"sample pdf"
    html = unescape(converter.call_args.kwargs["string"])
    for message in messages:
        assert message in html
    source = _SourceParagraph()
    source.feed(html)
    source_label = _("Datenquelle")
    expected = f"{source_label}: {DATA_SOURCE_NOTES[SNAPSHOT_HYBRID]} — {DATA_SOURCE_EXPLANATIONS[SNAPSHOT_HYBRID]}"
    assert " ".join("".join(source.text).split()) == " ".join(expected.split())
