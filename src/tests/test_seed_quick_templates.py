"""Tests für QuickTemplate-Seeds.

Refs #1003, #1004 (Paket S — Seed-Aufwertung).
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from core.models import DocumentType, Facility, QuickTemplate
from core.seed.doc_types import seed_document_types
from core.seed.organization import seed_facility, seed_organization
from core.seed.quick_templates import seed_quick_templates


@pytest.mark.django_db
class TestSeedQuickTemplates:
    def _facility(self):
        org = seed_organization()
        facility = seed_facility(org, "Anlaufstelle Mitte")
        seed_document_types(facility)
        return facility

    def test_creates_active_facility_scoped_templates(self):
        facility = self._facility()
        seed_quick_templates(facility)

        templates = QuickTemplate.objects.filter(facility=facility)
        assert templates.count() > 0
        assert all(t.facility_id == facility.id for t in templates)
        assert all(t.is_active for t in templates)
        facility_doc_type_ids = set(DocumentType.objects.filter(facility=facility).values_list("id", flat=True))
        assert all(t.document_type_id in facility_doc_type_ids for t in templates)

    def test_prefilled_data_keys_are_real_field_slugs(self):
        facility = self._facility()
        seed_quick_templates(facility)

        for tpl in QuickTemplate.objects.filter(facility=facility):
            valid_slugs = set(tpl.document_type.fields.values_list("field_template__slug", flat=True))
            assert set(tpl.prefilled_data).issubset(valid_slugs), (
                f"{tpl.name}: {set(tpl.prefilled_data) - valid_slugs} sind keine Feld-Slugs"
            )

    def test_idempotent(self):
        facility = self._facility()
        seed_quick_templates(facility)
        first = QuickTemplate.objects.filter(facility=facility).count()
        seed_quick_templates(facility)
        assert QuickTemplate.objects.filter(facility=facility).count() == first

    def test_seed_command_creates_quick_templates_per_facility(self):
        call_command("seed", "--flush", "--scale=solo")
        for facility in Facility.objects.all():
            assert QuickTemplate.objects.filter(facility=facility).exists(), f"keine QuickTemplates für {facility}"
