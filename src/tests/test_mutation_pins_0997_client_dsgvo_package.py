"""Pins for configured periods in rendered privacy documents.

Source: mutation delta of the release-0.99.7 checkpoint, 2 mutants.
Groups: proposal retry period (1) and open case inactivity period (1).
"""

import pytest

from core.models import Settings
from core.services.client.dsgvo_package import render_document

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("slug", ["dsfa", "loeschkonzept", "verarbeitungsverzeichnis"])
def test_rejection_retry_sentence_uses_configured_period(facility, slug):
    """Retry sentences contain the configured period: x_render_document__mutmut_71."""
    Settings.objects.create(facility=facility, retention_rejected_retry_days=137)

    content, _filename = render_document(slug, facility)

    assert "frühestens nach 137 Tagen" in " ".join(content.split())


def test_open_case_inactivity_sentence_uses_configured_period(facility):
    """The inactivity sentence contains its own period: x_render_document__mutmut_76."""
    Settings.objects.create(facility=facility, retention_open_case_inactive_days=283)

    content, _filename = render_document("loeschkonzept", facility)

    assert "Das Dashboard meldet offene Fälle nach 283 Tagen Ruhe." in " ".join(content.split())
