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


@pytest.mark.parametrize(
    "slug,metric",
    [
        ("counseling-hours", "events_gesamt"),
        ("needle-exchange-balance", "events"),
        ("ban-overview", "ausgesprochen"),
        ("ban-overview", "abgelaufen_im_zeitraum"),
        ("service-distribution", "events_leistungen"),
        ("quantity-sum", "contributors_total"),
    ],
)
def test_public_field_and_ban_prefix_difference_does_not_recover_a_singleton(facility, admin_user, slug, metric):
    from core.models import DocumentTypeField, FieldTemplate

    report_keys = {
        "counseling-hours": "counseling",
        "needle-exchange-balance": "needle_exchange",
        "ban-overview": None,
        "service-distribution": "contact",
        "quantity-sum": "contact",
    }
    document_type = make_document_type(
        facility=facility,
        name="Activity",
        report_key=report_keys[slug],
        **({"system_type": "ban"} if slug == "ban-overview" else {}),
    )
    field_keys = {
        "counseling-hours": ("dauer_minuten",),
        "needle-exchange-balance": ("ausgabe_stueck", "rueckgabe_stueck"),
        "ban-overview": (),
        "service-distribution": ("leistungen",),
        "quantity-sum": ("quantity_units",),
    }
    fields = []
    for key in field_keys[slug]:
        field = FieldTemplate.objects.create(
            facility=facility,
            name=key,
            report_key=key,
            field_type="multi_select" if key == "leistungen" else "number",
            options_json=[{"slug": "support", "label": "Support"}] if key == "leistungen" else [],
        )
        DocumentTypeField.objects.create(document_type=document_type, field_template=field)
        fields.append(field)
    for month, amount in ((1, 10), (3, 1)):
        for index in range(amount + (5 if slug == "quantity-sum" else 0)):
            data = {field.slug: ["support"] if field.report_key == "leistungen" else 1 for field in fields}
            if slug == "quantity-sum" and index >= amount:
                data = {}
            if slug == "ban-overview":
                data = {"aktiv": True, "bis": f"2026-{month:02d}-20"}
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=_at(month),
                data_json=data,
            )
    whole, prefix = [_payload(facility, slug, end)["summary"][metric] for end in (date(2026, 3, 31), date(2026, 2, 28))]
    candidates = {whole - prefix} if whole is not None and prefix is not None else set(range(1, 5))
    assert 1 in candidates
    assert len(candidates) >= 2, f"The {slug} files must not determine the March singleton via {metric}"
