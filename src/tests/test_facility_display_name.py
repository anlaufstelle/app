"""Facility display name resolution for exports (COD-04).

The PDF builders in ``core.services.system.export`` resolved the facility name
via ``getattr(facility.settings, "facility_full_name", facility.name)`` — the
``getattr`` default only fires when the *attribute is missing*, never for a
present-but-empty ``CharField(blank=True)`` value. A configured facility whose
``facility_full_name`` was left blank rendered a blank name in the report /
Sachbericht-PDF. The JSON/DSGVO builders already did it right with
``... or facility.name``.

All builders now share ``resolve_facility_display_name(facility)``.
Refs #1478 (COD-04).
"""

from __future__ import annotations

from unittest import mock

import pytest

from core.models import Settings
from core.services.facility import resolve_facility_display_name


@pytest.mark.django_db
class TestResolveFacilityDisplayName:
    def test_uses_full_name_when_set(self, facility):
        Settings.objects.create(facility=facility, facility_full_name="Anlaufstelle Musterstadt e.V.")
        assert resolve_facility_display_name(facility) == "Anlaufstelle Musterstadt e.V."

    def test_falls_back_to_facility_name_when_full_name_empty(self, facility):
        Settings.objects.create(facility=facility, facility_full_name="")
        assert resolve_facility_display_name(facility) == facility.name

    def test_falls_back_to_facility_name_when_no_settings_row(self, facility):
        assert not Settings.objects.filter(facility=facility).exists()
        assert resolve_facility_display_name(facility) == facility.name


@pytest.mark.django_db
class TestExportBuildersUseFacilityName:
    """The two PDF builders must not render a blank name for empty full_name."""

    def _captured_context(self, mock_render):
        # render_to_string("...", {context}) — context is the 2nd positional arg.
        return mock_render.call_args.args[1]

    def test_generic_dataset_pdf_uses_facility_name_when_full_name_empty(self, facility):
        from datetime import date, datetime

        from core.reporting.base import DatasetResult
        from core.reporting.pdf import render_dataset_pdf
        from core.reporting.registry import get_backend

        Settings.objects.create(facility=facility, facility_full_name="")
        backend = get_backend("general-key-figures")
        result = DatasetResult("aggregate", [], {"k_anonymity_threshold": 5}, [])

        with (
            mock.patch("core.reporting.pdf.render_to_string", return_value="<html></html>") as m,
            mock.patch("weasyprint.HTML"),
        ):
            render_dataset_pdf(backend, facility, date(2026, 1, 1), date(2026, 6, 30), result, datetime(2026, 7, 1))
        assert self._captured_context(m)["facility_name"] == facility.name

    def test_sample_report_1_pdf_uses_facility_name_when_full_name_empty(self, facility):
        Settings.objects.create(facility=facility, facility_full_name="")
        from core.services.system import export

        with (
            mock.patch.object(export, "render_to_string", return_value="<html></html>") as m,
            mock.patch.object(export, "weasyprint"),
        ):
            export.render_sample_report_1_pdf(
                facility,
                {"total": 0, "by_category": [], "by_age_cluster": [], "unique_clients": 0},
                "2026-01-01",
                "2026-06-30",
                generated_at=None,
            )
        assert self._captured_context(m)["facility_name"] == facility.name
