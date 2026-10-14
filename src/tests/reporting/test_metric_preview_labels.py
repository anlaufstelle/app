"""Metric preview labels leave the established export schema intact."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType
from core.reporting import registry

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("backend_key", "metric_key", "label"),
    [("ban-overview", "aktiv_aktuell", "Aktuell aktive Hausverbote"), ("case-metrics", "neue_faelle", "Neue Fälle")],
)
def test_preview_labels_and_stable_export(client, lead_user, facility, backend_key, metric_key, label):
    DocumentType.objects.create(facility=facility, name="Ban", system_type=DocumentType.SystemType.BAN)
    client.force_login(lead_user)
    response = client.get(reverse("core:statistics_report_panel"), {"backend": backend_key})
    assert response.status_code == 200
    html = response.content.decode()
    assert label in html
    assert metric_key in html
    assert "Bezeichnung" in html
    backend = registry.get_backend(backend_key)
    assert [spec.key for spec in backend.get_field_specs()] == ["kennzahl", "wert"]
    day = timezone.localdate()
    result = backend.prepare_export(facility, backend.build_dataset(facility, day, day))
    assert all(set(record) == {"kennzahl", "wert"} for record in result.records)
    assert metric_key in result.summary
