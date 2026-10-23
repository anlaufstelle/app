"""Reviewed artifact-pair classes and independent recipient equations."""

import json
from datetime import date, datetime
from itertools import product

import pytest
from django.utils import timezone

from core.models import Client, DocumentTypeField, Event, FieldTemplate
from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from tests._factories import make_document_type, make_event
from tests.reporting import _disclosure_inventory as inventory
from tests.reporting.test_reporting_sample_pdf_integration import pdf_text as pdf_text


def test_every_format_pair_has_an_explicit_reviewed_attack_class():
    assert set(inventory.exported_formats()) == set(inventory.FORMATS), (
        "Supported export formats are not all part of the reviewed format pairs"
    )
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
    "monthly-trend": frozenset("cns"),
    "quantity-sum": frozenset("cnsb"),
    "sample-report-1": frozenset("cns"),
    "counseling-hours": frozenset("cs"),
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


def _metric_name(dimension, slug):
    return "unique_clients" if dimension == "person_counts" and slug == "general-key-figures" else METRICS[slug]


def _public_metric(facility, dimension, slug, end):
    payload = json.loads(export_dataset(get_backend(slug), facility, date(2026, 1, 1), end, format="json").content)
    return payload["summary"][_metric_name(dimension, slug)]


def _raw_metric(facility, dimension, slug, end):
    """Unmasked value the backend counts before any suppression."""
    if slug == "general-key-figures":
        # Its dataset only exposes already protected values, so count the stored events directly.
        events = Event.objects.for_facility(facility).filter(
            is_deleted=False, occurred_at__date__gte=date(2026, 1, 1), occurred_at__date__lte=end
        )
        return (
            events.exclude(client=None).values("client").distinct().count()
            if dimension == "person_counts"
            else events.count()
        )
    result = get_backend(slug).build_dataset(facility, date(2026, 1, 1), end)
    return result.summary[_metric_name(dimension, slug)]


def _remainder_candidates(left_groups, right_groups, whole, prefix, *, snapshot_sides=()):
    """Remainders consistent with the two observed values.

    January group sizes are nuisance variables. A side listed in
    ``snapshot_sides`` is a historical snapshot counter that is independent of
    the current January cohort.
    """
    candidates = set()
    for january in product(range(8, 13), repeat=4):
        counts = dict(zip("cnsb", january, strict=True))
        for snapshot in range(8, 13) if snapshot_sides else (None,):
            left_base = snapshot if "left" in snapshot_sides else sum(counts[group] for group in left_groups)
            right_base = snapshot if "right" in snapshot_sides else sum(counts[group] for group in right_groups)
            for remainder in range(1, 5):
                if (whole is None or left_base + remainder == whole) and (prefix is None or right_base == prefix):
                    candidates.add(remainder)
    return candidates


def _groups(dimension, slug):
    return EVENT_GROUPS[slug] if dimension == "event_counts" else frozenset("cnsb")


def _assert_raw_world_matches_model(facility, dimension, slug, target):
    """The fixture's real raw counts must equal the equation model's population."""
    groups = _groups(dimension, slug)
    january = 10 * len(groups)
    assert _raw_metric(facility, dimension, slug, date(2026, 2, 28)) == january, (
        f"{slug}: the January/February raw population differs from the modelled groups {sorted(groups)}"
    )
    expected = january + (1 if target in groups else 0)
    assert _raw_metric(facility, dimension, slug, date(2026, 3, 31)) == expected, (
        f"{slug}: the March target group {target!r} does not move the raw count as modelled"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("dimension,left,right,left_source,right_source", LIVE_CLASSES)
def test_pair_class_recipient_cannot_solve_the_small_calendar_remainder(
    facility, attack_world, dimension, left, right, left_source, right_source
):
    left_groups, right_groups = _groups(dimension, left), _groups(dimension, right)
    target_group = sorted(left_groups)[0]
    attack_world(target_group, 3, 1)
    # The target cell belongs to the examined (left) population, and the model
    # matches what the real backends count before masking.
    _assert_raw_world_matches_model(facility, dimension, left, target_group)
    _assert_raw_world_matches_model(facility, dimension, right, target_group)
    whole = _public_metric(facility, dimension, left, date(2026, 3, 31))
    prefix = _public_metric(facility, dimension, right, date(2026, 2, 28))
    candidates = _remainder_candidates(left_groups, right_groups, whole, prefix)
    assert 1 in candidates, "The true world must remain feasible"
    assert len(candidates) >= 2, f"{left}/{right} determine the March singleton: {candidates}"


@pytest.mark.django_db
@pytest.mark.parametrize("dimension,left,right,left_source,right_source", LIVE_CLASSES)
def test_pair_class_equations_pin_the_remainder_when_values_are_unmasked(
    facility, attack_world, dimension, left, right, left_source, right_source
):
    """Control: with raw values the same equations expose the target exactly for equal populations."""
    left_groups, right_groups = _groups(dimension, left), _groups(dimension, right)
    attack_world(sorted(left_groups)[0], 3, 1)
    whole = _raw_metric(facility, dimension, left, date(2026, 3, 31))
    prefix = _raw_metric(facility, dimension, right, date(2026, 2, 28))
    candidates = _remainder_candidates(left_groups, right_groups, whole, prefix)
    assert 1 in candidates
    if left_groups == right_groups:
        assert candidates == {1}, f"{left}/{right}: unmasked equal populations must pin the remainder"
    else:
        assert len(candidates) >= 2, f"{left}/{right}: unequal populations keep free nuisance variables"


HYBRID_CLASSES = [entry for entry in inventory.ATTACK_CLASSES if "snapshot_hybrid" in entry[3:]]


@pytest.mark.django_db
@pytest.mark.parametrize("dimension,left,right,left_source,right_source", HYBRID_CLASSES)
@pytest.mark.parametrize("use_snapshot", [False, True], ids=["live-fallback", "mixed-source"])
def test_hybrid_pair_class_preserves_independent_snapshot_and_live_remainders(
    facility, attack_world, pdf_text, dimension, left, right, left_source, right_source, use_snapshot
):
    import re

    from core.models import StatisticsSnapshot

    if use_snapshot:
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2026,
            month=1,
            backend_data={
                "sample-report-1": {
                    "total": 10,
                    "unique_clients": 0,
                    "by_category": [["Kontakte", 10]],
                    "by_age_cluster": [],
                }
            },
        )
    left_groups, right_groups = EVENT_GROUPS[left], EVENT_GROUPS[right]
    target_group = sorted(left_groups)[0]
    attack_world(target_group, 3, 1)
    # Live sides (and the live fallback of the hybrid PDF) must match the model.
    for slug, source in ((left, left_source), (right, right_source)):
        if source == "live" or not use_snapshot:
            _assert_raw_world_matches_model(facility, dimension, slug, target_group)
    values = []
    for slug, source, end in ((left, left_source, date(2026, 3, 31)), (right, right_source, date(2026, 2, 28))):
        if source == "live":
            values.append(_public_metric(facility, dimension, slug, end))
        else:
            response = export_dataset(get_backend(slug), facility, date(2026, 1, 1), end, format="pdf")
            assert response.content.startswith(b"%PDF-")
            match = re.search(r"Gesamtkontakte \(ohne Notizen/Hausverbote\):\s*(unterdrückt|[0-9]+)", pdf_text[-1])
            assert match
            values.append(None if match[1] == "unterdrückt" else int(match[1]))
    whole, prefix = values
    snapshot_sides = tuple(
        side for side, source in (("left", left_source), ("right", right_source)) if use_snapshot and source != "live"
    )
    candidates = _remainder_candidates(left_groups, right_groups, whole, prefix, snapshot_sides=snapshot_sides)
    assert 1 in candidates and len(candidates) >= 2, f"The PDF/{left}/{right} equations reveal {candidates}"
