"""Behavioral guard: declared consumers must use the solver's decisions."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Case, Client, DocumentTypeField, Event, FieldTemplate, StatisticsSnapshot
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec, stamp_threshold
from core.reporting.export import enforce_suppression_contract
from core.reporting.registry import get_backend
from core.services.dashboard import shared_disclosure
from tests._factories import make_document_type, make_event
from tests.reporting._disclosure_inventory import SOURCES

pytestmark = [pytest.mark.django_db, pytest.mark.architecture]
START, END = date(2026, 1, 1), date(2026, 1, 31)


@pytest.fixture
def pipeline_world(facility, admin_user):
    """Every declared live consumer has nonzero, safely publishable counts."""
    when = timezone.make_aware(datetime(2026, 1, 15, 12))
    fields = {}
    for key, field_type in (
        ("dauer_minuten", "number"),
        ("quantity_units", "number"),
        ("ausgabe_stueck", "number"),
        ("rueckgabe_stueck", "number"),
        ("leistungen", "multi_select"),
        ("art_der_krise", "select"),
    ):
        fields[key] = FieldTemplate.objects.create(
            facility=facility,
            name=key,
            report_key=key,
            field_type=field_type,
            options_json=[{"slug": "support", "label": "Support"}] if field_type != "number" else [],
        )
    types = []
    for key in ("contact", "counseling", "crisis", "needle_exchange", None):
        document_type = make_document_type(
            facility=facility,
            name=key or "Ban",
            report_key=key,
            **({"system_type": "ban"} if key is None else {}),
        )
        keys = ["quantity_units"]
        if key in ("contact", "counseling", "crisis"):
            keys.append("dauer_minuten")
        if key == "needle_exchange":
            keys.extend(("ausgabe_stueck", "rueckgabe_stueck"))
        if key == "contact":
            keys.append("leistungen")
        if key == "crisis":
            keys.append("art_der_krise")
        for field_key in keys:
            DocumentTypeField.objects.create(document_type=document_type, field_template=fields[field_key])
        types.append((document_type, keys))
    for index in range(6):
        person = Client.objects.create(facility=facility, pseudonym=f"P{index}", age_cluster="u18")
        Client.objects.for_facility(facility).filter(pk=person.pk).update(created_at=when)
        case = Case.objects.create(facility=facility, client=person, title="Support")
        Case.objects.for_facility(facility).filter(pk=case.pk).update(created_at=when)
        for document_type, keys in types:
            data = {
                fields[key].slug: ["support"] if key == "leistungen" else "support" if key == "art_der_krise" else 1
                for key in keys
            }
            if document_type.system_type == "ban":
                data.update(aktiv=True, bis="2026-01-20")
            make_event(
                facility=facility,
                document_type=document_type,
                client=person,
                created_by=admin_user,
                occurred_at=when,
                data_json=data,
            )
    return facility


def _numbers(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key != "k_anonymity_threshold":
                yield from _numbers(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _numbers(item)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield value


def _row_identities(value, path=()):
    """Yield (path, identity) for every table row and keyed entry in a prepared result.

    A row's identity is its text content (labels, names, categories); counts,
    markers and other numbers are not part of it.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, Mapping | list | tuple):
                yield from _row_identities(item, (*path, key))
            elif len(path) > 1:
                # A scalar directly below the result is a fixed key figure, not a data-dependent row.
                yield path, ("key", key)
    else:
        for item in value:
            if isinstance(item, Mapping):
                yield path, ("row", frozenset((key, text) for key, text in item.items() if isinstance(text, str)))
                for key, nested in item.items():
                    if isinstance(nested, Mapping | list | tuple):
                        yield from _row_identities(nested, (*path, key))
            elif isinstance(item, str):
                yield path, ("text", item)


def _identities(result):
    return set(_row_identities({"records": result.records, "summary": result.summary}))


def _prepared(backend, facility, format_name):
    raw = (
        backend.build_dataset(facility, START, END)
        if format_name == "preview"
        else backend.build_format_dataset(facility, START, END, format=format_name)
    )
    return enforce_suppression_contract(backend, backend.prepare_export(facility, raw))


def _assert_consumes_solver_output(backend, facility, format_name, monkeypatch):
    baseline = _prepared(backend, facility, format_name)
    assert any(number > 0 for number in _numbers((baseline.records, baseline.summary))), (
        "The probe needs a nonempty consumer"
    )
    original = shared_disclosure.protect_temporal_counts
    reached = []

    def withhold_everything(*args, **kwargs):
        mask = original(*args, **kwargs)
        reached.append(mask.facility_id)
        return replace(mask, published=dict.fromkeys(mask.published), dropped=frozenset(mask.published))

    with monkeypatch.context() as patch:
        patch.setattr(shared_disclosure, "protect_temporal_counts", withhold_everything)
        protected = _prepared(backend, facility, format_name)
    assert reached == [facility.pk], "The declared source must reach the common solver once"
    assert not any(number > 0 for number in _numbers((protected.records, protected.summary))), (
        "A backend reached the common solver but discarded its suppression/table decisions"
    )
    # Fixed rows (scalar key figures, fixed stage lists) also exist without any data;
    # whatever appears only with data is a data-dependent row or table.
    Case.objects.for_facility(facility).delete()
    Event.objects.for_facility(facility).delete()
    StatisticsSnapshot.objects.filter(facility=facility).delete()
    data_dependent = _identities(baseline) - _identities(_prepared(backend, facility, format_name))
    restored = data_dependent & _identities(protected)
    assert not restored, f"A backend restored rows or tables the common solver dropped: {sorted(map(repr, restored))}"


@pytest.mark.parametrize("slug,format_name", SOURCES)
def test_registered_dimensions_consume_the_actual_solver_mask(pipeline_world, slug, format_name, monkeypatch):
    if SOURCES[slug, format_name] == "snapshot_hybrid":
        from core.models import StatisticsSnapshot

        StatisticsSnapshot.objects.create(
            facility=pipeline_world,
            year=2026,
            month=1,
            backend_data={
                "sample-report-1": {
                    "total": 29,
                    "unique_clients": 0,
                    "by_category": [["Kontakte", 29]],
                    "by_age_cluster": [],
                }
            },
        )
    _assert_consumes_solver_output(get_backend(slug), pipeline_world, format_name, monkeypatch)


@pytest.mark.parametrize("alias", [False, True])
def test_inherited_and_aliased_consumers_pass_the_behavioral_guard(pipeline_world, monkeypatch, alias):
    parent = type(get_backend("example"))
    attributes = {"prepare_export": parent.prepare_export} if alias else {}
    backend = type("DerivedConsumer", (parent,), attributes)()
    _assert_consumes_solver_output(backend, pipeline_world, "json", monkeypatch)


class BypassBackend(DatasetBackend):
    """A declaration, interval and solver call do not establish data flow."""

    slug = "bypass"
    disclosure_dimensions = frozenset({"event_counts"})

    def get_field_specs(self):
        return [FieldSpec("count", "Count")]

    def build_dataset(self, facility, date_from, date_to):
        return DatasetResult("rows", [{"count": 6}], {"total": 6}, [], (date_from, date_to, "live"))

    def prepare_export(self, facility, result):
        shared_disclosure.build_live_release(facility, START, END)
        return replace(result, summary=stamp_threshold(result.summary, 5))


def test_behavioral_guard_rejects_a_solver_call_whose_result_is_ignored(pipeline_world, monkeypatch):
    with pytest.raises(AssertionError, match="discarded its suppression/table decisions"):
        _assert_consumes_solver_output(BypassBackend(), pipeline_world, "json", monkeypatch)


def _consumer_restoring(slug, restore):
    """A consumer of the real backend that masks every number but brings dropped rows back."""
    parent = type(get_backend(slug))

    class RowRestoringConsumer(parent):
        def prepare_export(self, facility, result):
            protected = super().prepare_export(facility, result)
            return restore(result, protected)

    return RowRestoringConsumer()


def _restore_records(result, protected):
    if result.records and not protected.records:
        return replace(protected, records=[{**row, "count": None} for row in result.records])
    return protected


def _restore_summary_table(result, protected):
    if result.summary.get("by_category") and not protected.summary.get("by_category"):
        names = [row[0] if isinstance(row, tuple | list) else row["name"] for row in result.summary["by_category"]]
        rows = [{"name": name, "count": None} for name in names]
        return replace(protected, summary={**protected.summary, "by_category": rows})
    return protected


@pytest.mark.parametrize(
    "slug,restore",
    [("example", _restore_records), ("sample-report-1", _restore_summary_table)],
    ids=["record-rows", "summary-table"],
)
def test_behavioral_guard_rejects_rows_restored_after_the_solver_dropped_them(
    pipeline_world, monkeypatch, slug, restore
):
    with pytest.raises(AssertionError, match="restored rows or tables"):
        _assert_consumes_solver_output(_consumer_restoring(slug, restore), pipeline_world, "json", monkeypatch)
