"""A stored threshold below the minimum is logged once per report computation."""

import logging
from datetime import date

import pytest

from core.models import Settings
from core.reporting.export import export_dataset
from core.reporting.registry import get_backends

pytestmark = pytest.mark.django_db

LOGGER = "core.services.dashboard.external_report"
START, END = date(2026, 1, 1), date(2026, 1, 31)
SLUGS = sorted(backend.slug for backend in get_backends())


@pytest.mark.parametrize("fmt", ["json", "pdf"])
@pytest.mark.parametrize("slug", SLUGS)
def test_every_export_logs_a_low_stored_threshold_once(facility, settings_obj, caplog, slug, fmt):
    backend = next(backend for backend in get_backends() if backend.slug == slug)
    if fmt.upper() not in backend.get_format_sources():
        pytest.skip("The backend does not offer this format")
    Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=1)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        export_dataset(backend, facility, START, END, format=fmt)

    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert len(messages) == 1, messages
