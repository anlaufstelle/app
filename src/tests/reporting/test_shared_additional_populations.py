"""Recipient attacks against temporal unions of additional live populations."""

import json
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Case, Client
from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from tests._factories import make_document_type, make_event

pytestmark = pytest.mark.django_db


def _at(month):
    return timezone.make_aware(datetime(2026, month, 15, 12))


def _payload(facility, slug, end):
    return json.loads(export_dataset(get_backend(slug), facility, date(2026, 1, 1), end, format="json").content)


@pytest.mark.parametrize(
    "slug,metric",
    [
        ("monthly-trend", "total"),
        ("client-metrics", "aktive_personen"),
        ("client-metrics", "neuaufnahmen"),
        ("case-metrics", "neue_faelle"),
        ("case-metrics", "geschlossene_faelle"),
        ("case-metrics", "betroffene_personen"),
    ],
)
def test_public_prefix_difference_cannot_recover_an_additional_population_singleton(facility, admin_user, slug, metric):
    document_type = make_document_type(facility=facility, name="Contacts", report_key="contact")
    for month, amount in ((1, 10), (3, 1)):
        for index in range(amount):
            person = Client.objects.create(facility=facility, pseudonym=f"P-{month}-{index}", age_cluster="u18")
            Client.objects.for_facility(facility).filter(pk=person.pk).update(created_at=_at(month))
            make_event(
                facility=facility,
                document_type=document_type,
                client=person,
                created_by=admin_user,
                occurred_at=_at(month),
                data_json={},
            )
            case = Case.objects.create(facility=facility, client=person, title="Support")
            Case.objects.for_facility(facility).filter(pk=case.pk).update(
                created_at=_at(month), closed_at=_at(month), status=Case.Status.CLOSED
            )
    values = [_payload(facility, slug, end)["summary"][metric] for end in (date(2026, 3, 31), date(2026, 2, 28))]
    whole, prefix = values
    candidates = {whole - prefix} if whole is not None and prefix is not None else set(range(1, 5))
    assert 1 in candidates
    assert len(candidates) >= 2, f"The two {slug} files must not determine the March singleton via {metric}"
