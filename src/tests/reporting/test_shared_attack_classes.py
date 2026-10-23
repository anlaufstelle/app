"""Reviewed artifact-pair classes and independent recipient equations."""

import json
from datetime import date, datetime
from itertools import product

import pytest
from django.utils import timezone

from core.models import Client, DocumentTypeField, FieldTemplate
from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from tests._factories import make_document_type, make_event
from tests.reporting import _disclosure_inventory as inventory


def test_every_format_pair_has_an_explicit_reviewed_attack_class():
    actual = {
        (dimension, left, right, inventory.SOURCES[left, left_format], inventory.SOURCES[right, right_format])
        for dimension, left, right, left_format, right_format in inventory.FORMAT_PAIRS
    }
    assert actual == set(inventory.ATTACK_CLASSES)
    assert len(actual) == 45


# These are membership sets, not claims that similarly sized reports are aliases.
EVENT_GROUPS = {
    "general-key-figures": frozenset("cnsb"),
    "example": frozenset("cnsb"),
    "monthly-trend": frozenset("cnsb"),
    "quantity-sum": frozenset("cnsb"),
    "sample-report-1": frozenset("cns"),
    "counseling-hours": frozenset("c"),
    "needle-exchange-balance": frozenset("n"),
    "service-distribution": frozenset("s"),
    "ban-overview": frozenset("b"),
}
METRICS = {
    "general-key-figures": "total_contacts",
    "example": "total",
    "monthly-trend": "total",
    "quantity-sum": "contributors_total",
    "sample-report-1": "total",
    "counseling-hours": "events_gesamt",
    "needle-exchange-balance": "events",
    "service-distribution": "events_leistungen",
    "ban-overview": "ausgesprochen",
    "client-metrics": "aktive_personen",
}
LIVE_CLASSES = [entry for entry in inventory.ATTACK_CLASSES if entry[3:] == ("live", "live")]


@pytest.fixture
def attack_world(facility, admin_user, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 3, 31))
    fields = {}
    for key in ("dauer_minuten", "quantity_units", "ausgabe_stueck", "rueckgabe_stueck", "leistungen"):
        field = FieldTemplate.objects.create(
            facility=facility,
            name=key,
            report_key=key,
            field_type="multi_select" if key == "leistungen" else "number",
            options_json=[{"slug": "support", "label": "Support"}] if key == "leistungen" else [],
        )
        fields[key] = field
    types = {}
    for group, report_key in (("c", "counseling"), ("n", "needle_exchange"), ("s", "contact"), ("b", None)):
        document_type = make_document_type(
            facility=facility,
            name=f"Activity {group}",
            report_key=report_key,
            **({"system_type": "ban"} if group == "b" else {}),
        )
        for field in fields.values():
            DocumentTypeField.objects.create(document_type=document_type, field_template=field)
        types[group] = document_type

    def add(group, month, amount):
        for index in range(amount):
            person = Client.objects.create(facility=facility, pseudonym=f"Person-{group}-{month}-{index}")
            data = {field.slug: ["support"] if key == "leistungen" else 1 for key, field in fields.items()}
            if group == "b":
                data.update(aktiv=True, bis=f"2026-{month:02d}-20")
            make_event(
                facility=facility,
                document_type=types[group],
                client=person,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, month, 15, 12)),
                data_json=data,
            )

    for group in types:
        add(group, 1, 10)
    return add


def _public_metric(facility, dimension, slug, end):
    payload = json.loads(export_dataset(get_backend(slug), facility, date(2026, 1, 1), end, format="json").content)
    metric = "unique_clients" if dimension == "person_counts" and slug == "general-key-figures" else METRICS[slug]
    return payload["summary"][metric]


@pytest.mark.django_db
@pytest.mark.parametrize("dimension,left,right,left_source,right_source", LIVE_CLASSES)
def test_pair_class_recipient_cannot_solve_the_small_calendar_remainder(
    facility, attack_world, dimension, left, right, left_source, right_source
):
    left_groups = EVENT_GROUPS[left] if dimension == "event_counts" else frozenset("cnsb")
    right_groups = EVENT_GROUPS[right] if dimension == "event_counts" else frozenset("cnsb")
    target_group = sorted(left_groups)[0]
    attack_world(target_group, 3, 1)
    whole = _public_metric(facility, dimension, left, date(2026, 3, 31))
    prefix = _public_metric(facility, dimension, right, date(2026, 2, 28))
    # Independently enumerate recipient equations. Disjoint/partial populations
    # retain their nuisance variables; subtracting them is not an identity.
    candidates = set()
    for january in product(range(8, 13), repeat=4):
        counts = dict(zip("cnsb", january, strict=True))
        for remainder in range(1, 5):
            if whole is not None and sum(counts[group] for group in left_groups) + remainder != whole:
                continue
            if prefix is not None and sum(counts[group] for group in right_groups) != prefix:
                continue
            candidates.add(remainder)
    assert 1 in candidates, "The true world must remain feasible"
    assert len(candidates) >= 2, f"{left}/{right} determine the March singleton: {candidates}"
