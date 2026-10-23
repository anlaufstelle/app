"""Scoped raw field cohorts and projections of their shared count decisions."""

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from fractions import Fraction

from core.reporting.base import DatasetResult, FieldSpec
from core.reporting.values import as_number, as_options, json_number, read_ban_expiry, read_quantity
from core.services.dashboard.shared_disclosure import CountKey, RawCount, TemporalPopulation, population_units
from core.services.file_vault import safe_decrypt


def option_identity(field, option):
    return f"service-option:{json.dumps([field, option])}"


@dataclass(frozen=True)
class FieldSources:
    populations: tuple[TemporalPopulation, ...]
    rows: tuple[dict, ...]
    types: dict
    groups: tuple
    quantity_types: frozenset


@dataclass(frozen=True)
class FieldDataset:
    """Bind every count and numeric dependency by stable row identity."""

    raw: DatasetResult
    local: DatasetResult
    labels: tuple[str, ...]
    row_counts: dict[tuple, dict[str, str]]
    summary_counts: dict[str, str]
    row_gates: dict[tuple, dict[str, tuple[str, ...]]]
    summary_gates: dict[str, tuple[str, ...]]
    populations: dict[str, frozenset[str]]
    start: date
    end: date

    def key(self, identity):
        return CountKey("event_counts", identity, self.start, self.end)

    def label(self, row):
        return tuple(row[field] for field in self.labels)

    def group(self, label):
        return label[0] if self.labels == ("feld", "option") else "records"

    def observations(self):
        published = {self.label(row): row for row in self.local.records}
        for row in self.raw.records:
            label = self.label(row)
            for field, identity in self.row_counts.get(label, {}).items():
                yield RawCount(
                    self.key(identity),
                    row[field],
                    hidden=published.get(label, {}).get(field) is None,
                    lower=1 if label in published and self.labels not in (("kennzahl",), ("document_type",), ()) else 0,
                )
        for field, identity in self.summary_counts.items():
            yield RawCount(self.key(identity), self.raw.summary[field], hidden=self.local.summary[field] is None)
        # A visible numeric sum can expose its contributor count in a known
        # unit-value world. Audit that stronger observation before release.
        for row in self.local.records:
            for field, identities in self.row_gates.get(self.label(row), {}).items():
                if row[field] is not None:
                    for identity in identities:
                        yield RawCount(self.key(identity), len(self.populations[identity]))
        for field, identities in self.summary_gates.items():
            if self.local.summary[field] is not None:
                for identity in identities:
                    yield RawCount(self.key(identity), len(self.populations[identity]))

    def dropped(self):
        labels = {self.label(row) for row in self.local.records}
        return tuple(
            self.key(identity)
            for label, fields in self.row_counts.items()
            if label not in labels
            for identity in fields.values()
        )

    def project(self, resolve, mask):
        def key(identity):
            return resolve("event_counts", identity)

        def visible(identity):
            resolved = key(identity)
            return resolved is not None and resolved not in mask.dropped and mask.published[resolved] is not None

        dropped_groups = {
            self.group(label)
            for label, fields in self.row_counts.items()
            if any(key(identity) is None or key(identity) in mask.dropped for identity in fields.values())
        }
        rows = []
        for original in self.local.records:
            label = self.label(original)
            if self.group(label) in dropped_groups:
                continue
            row = dict(original)
            for field, identity in self.row_counts.get(label, {}).items():
                row[field] = mask.published[key(identity)] if row[field] is not None else None
            for field, identities in self.row_gates.get(label, {}).items():
                if not all(visible(identity) for identity in identities):
                    row[field] = None
            rows.append(row)
        summary = dict(self.local.summary)
        for field, identity in self.summary_counts.items():
            summary[field] = mask.published[key(identity)] if visible(identity) and summary[field] is not None else None
        for field, identities in self.summary_gates.items():
            if not all(visible(identity) for identity in identities):
                summary[field] = None
        return replace(self.local, records=rows, summary=summary)


def collect_field_sources(facility):
    """Resolve field bindings once and read event payloads once per release."""
    from django.db.models.functions import TruncDate
    from django.utils import timezone

    from core.models import DocumentType, Event
    from core.reporting.registry import get_backend

    counseling = get_backend("counseling-hours")
    needle = get_backend("needle-exchange-balance")
    quantity = get_backend("quantity-sum")
    service = get_backend("service-distribution")
    duration = counseling.feld_bindungen[0].leseschluessel(facility, counseling.typ_bindung)
    outgoing, incoming = (binding.leseschluessel(facility, needle.typ_bindung) for binding in needle.feld_bindungen)
    quantity_binding = quantity.feld_bindungen[0].leseschluessel(facility, entity_type=DocumentType.EntityType.EVENT)
    quantity_types = frozenset(set(quantity_binding.je_typ) | set(quantity_binding.doppelt))
    groups = tuple(group for group in service.count_groups if group.anforderung.erfuellt(facility))
    group_bindings = {group.feld_slug: group.leseschluessel(facility) for group in groups}
    types = {
        row["pk"]: row
        for row in DocumentType.objects.for_facility(facility)
        .order_by("name", "pk")
        .values("pk", "name", "report_key", "system_type")
    }
    members = defaultdict(set)
    for identity in (
        "counseling-events",
        "needle-events",
        "needle-contributors",
        "needle-missing",
        "issued-bans",
        "expired-bans",
        "current-active-bans",
        "quantity-contributors",
    ):
        members[identity] = set()
    for group in groups:
        members[f"service-values:{group.feld_slug}"] = set()
    rows = []
    today = timezone.localdate()
    events = (
        Event.objects.for_facility(facility)
        .filter(is_deleted=False)
        .annotate(day=TruncDate("occurred_at"))
        .values("pk", "day", "document_type_id", "data_json")
        .order_by("occurred_at", "pk")
    )
    for row in events.iterator(chunk_size=1000):
        pk, day, type_id, data = str(row["pk"]), row["day"], row["document_type_id"], row["data_json"] or {}
        unit = pk, day
        report_key = types[type_id]["report_key"]
        parsed = {"unit": pk, "day": day, "type_id": type_id}
        if counseling.typ_bindung is not None:
            category = counseling.typ_bindung.resolve(report_key)
            if category is not None:
                slug = duration.je_typ.get(type_id)
                value = as_number(safe_decrypt(data.get(slug), default=None)) if slug else None
                parsed.update(counseling=category, duration=value)
                members["counseling-events"].add(unit)
                members[f"counseling-category:{category}"].add(unit)
                if value is not None:
                    members[f"counseling-contributors:{category}"].add(unit)
        if needle.typ_bindung is not None and needle.typ_bindung.resolve(report_key) is not None:
            values = [
                as_number(safe_decrypt(data.get(slug), default=None)) if slug else None
                for slug in (outgoing.je_typ.get(type_id), incoming.je_typ.get(type_id))
            ]
            parsed["needle"] = values
            members["needle-events"].add(unit)
            members["needle-contributors" if any(value is not None for value in values) else "needle-missing"].add(unit)
        if types[type_id]["system_type"] == "ban":
            expiry = read_ban_expiry(data)
            members["issued-bans"].add(unit)
            if expiry is not None:
                members["expired-bans"].add((pk, expiry))
            if safe_decrypt(data.get("aktiv", False), default=False) and (expiry is None or expiry >= today):
                members["current-active-bans"].add((pk, date.min))
        if type_id in quantity_types:
            value = read_quantity(data, quantity_binding.je_typ.get(type_id))
            parsed["quantity"] = value
            members.setdefault(f"quantity-contributors:{type_id}", set())
            if value is not None:
                members[f"quantity-contributors:{type_id}"].add(unit)
                members["quantity-contributors"].add(unit)
        parsed["options"] = {}
        for group in groups:
            if group.typ_bindung.resolve(report_key) is None:
                continue
            slug = group_bindings[group.feld_slug].je_typ.get(type_id)
            options = as_options(safe_decrypt(data.get(slug), default=None)) if slug else []
            parsed["options"][group.feld_slug] = options
            if options:
                members[f"service-values:{group.feld_slug}"].add(unit)
                for option in options:
                    members[option_identity(group.feld_slug, option)].add(unit)
        rows.append(parsed)
    # Empty configured contributor cohorts still have stable identities.
    for pk in quantity_types:
        members.setdefault(f"quantity-contributors:{pk}", set())
    if counseling.typ_bindung is not None:
        for category in counseling.typ_bindung.ziele():
            members.setdefault(f"counseling-contributors:{category}", set())
    return FieldSources(
        tuple(
            TemporalPopulation(
                "event_counts", identity, tuple(sorted(units)), point_in_time=identity == "current-active-bans"
            )
            for identity, units in sorted(members.items())
        ),
        tuple(rows),
        types,
        groups,
        quantity_types,
    )


def prepare_field_interval(sources, populations, start, end, threshold, general):
    """Rebuild local field contracts from the single raw snapshot."""
    from core.services.dashboard.external_report import (
        ORDER_INPUT,
        _Breakdown,
        _protect_counseling_dataset,
        _protect_metric_dataset,
        _protect_needle_dataset,
        _protect_quantity_dataset,
        _protect_service_dataset,
    )

    members = {p.identity: population_units(p, start, end) for p in populations if p.dimension == "event_counts"}
    rows = [row for row in sources.rows if start <= row["day"] <= end]

    def size(identity):
        return len(members.get(identity, ()))

    def specs(*fields):
        return [FieldSpec(field, field) for field in fields]

    def bind(raw, local, labels, row_counts, summary_counts, row_gates=None, summary_gates=None):
        return FieldDataset(
            raw, local, labels, row_counts, summary_counts, row_gates or {}, summary_gates or {}, members, start, end
        )

    categories = list(dict.fromkeys(row["counseling"] for row in rows if "counseling" in row))
    # Report keys have a declared order independent of counts or event order.
    from core.reporting.registry import get_backend

    binding = get_backend("counseling-hours").typ_bindung
    categories = [category for category in binding.ziele() if category in categories] if binding else []
    counseling_rows = []
    for category in categories:
        matching = [row for row in rows if row.get("counseling") == category]
        values = [row["duration"] for row in matching if row["duration"] is not None]
        counseling_rows.append(
            {"kategorie": category, "events": len(matching), "minuten": sum(values), "beitragende": len(values)}
        )
    raw = DatasetResult(
        "rows",
        counseling_rows,
        {"events_gesamt": size("counseling-events"), "minuten_gesamt": sum(row["minuten"] for row in counseling_rows)},
        [],
    )
    local = _protect_counseling_dataset(
        raw,
        threshold,
        field_specs=specs("kategorie", "events", "minuten"),
        breakdown=_Breakdown("records", "events_gesamt", ORDER_INPUT, count_key="events"),
    )
    counseling_contract = bind(
        raw,
        local,
        ("kategorie",),
        {(category,): {"events": f"counseling-category:{category}"} for category in categories},
        {"events_gesamt": "counseling-events"},
        {
            (category,): {"minuten": (f"counseling-category:{category}", f"counseling-contributors:{category}")}
            for category in categories
        },
        {"minuten_gesamt": ("counseling-events", *(f"counseling-contributors:{category}" for category in categories))},
    )
    outgoing = sum((row["needle"][0] or 0) for row in rows if "needle" in row)
    incoming = sum((row["needle"][1] or 0) for row in rows if "needle" in row)
    values = {
        "events": size("needle-events"),
        "summe_ausgabe": outgoing,
        "summe_rueckgabe": incoming,
        "saldo": outgoing - incoming,
        "events_ohne_werte": size("needle-missing"),
    }
    needle_specs = specs("events", "summe_ausgabe", "summe_rueckgabe", "saldo")
    raw = DatasetResult("aggregate", [{spec.key: values[spec.key] for spec in needle_specs}], values, [])
    local = _protect_needle_dataset(raw, threshold, field_specs=needle_specs)
    gates = dict.fromkeys(("summe_ausgabe", "summe_rueckgabe", "saldo"), ("needle-events", "needle-contributors"))
    needle_contract = bind(
        raw,
        local,
        (),
        {(): {"events": "needle-events"}},
        {"events": "needle-events", "events_ohne_werte": "needle-missing"},
        {(): gates},
        gates,
    )
    values = {
        "ausgesprochen": size("issued-bans"),
        "aktiv_aktuell": size("current-active-bans"),
        "abgelaufen_im_zeitraum": size("expired-bans"),
    }
    raw = DatasetResult(
        "aggregate", [{"kennzahl": field, "wert": value} for field, value in values.items()], values, []
    )
    local = _protect_metric_dataset(raw, threshold, count_key="wert")
    identities = dict(zip(values, ("issued-bans", "current-active-bans", "expired-bans"), strict=True))
    ban_contract = bind(
        raw, local, ("kennzahl",), {(field,): {"wert": identity} for field, identity in identities.items()}, identities
    )
    service_rows, values = [], {}
    for group in sources.groups:
        values[group.summary_key] = size(f"service-values:{group.feld_slug}")
        options = sorted({option for row in rows for option in row["options"].get(group.feld_slug, ())})
        service_rows.extend(
            {"feld": group.feld_slug, "option": option, "count": size(option_identity(group.feld_slug, option))}
            for option in options
        )
    raw = DatasetResult("rows", service_rows, values, [])
    local = _protect_service_dataset(
        raw, threshold, field_specs=specs("feld", "option", "count"), groups=sources.groups
    )
    service_contract = bind(
        raw,
        local,
        ("feld", "option"),
        {(row["feld"], row["option"]): {"count": option_identity(row["feld"], row["option"])} for row in service_rows},
        {group.summary_key: f"service-values:{group.feld_slug}" for group in sources.groups},
    )
    quantity_rows = []
    for pk, metadata in sources.types.items():
        if pk not in sources.quantity_types:
            continue
        matching = [row for row in rows if row["type_id"] == pk]
        if not matching:
            continue
        contributions = [row["quantity"] for row in matching if row["quantity"] is not None]
        quantity_rows.append(
            {
                "document_type": metadata["name"],
                "events": len(matching),
                "contributors": len(contributions),
                "quantity": json_number(sum(contributions, Fraction(0))) if contributions else None,
            }
        )
    total = sum((row["quantity"] for row in rows if "quantity" in row and row["quantity"] is not None), Fraction(0))
    values = {
        "document_type_counts": {row["name"]: row["count"] for row in general.get("by_document_type", [])},
        "contributors_total": size("quantity-contributors"),
        "quantity_total": json_number(total) if all(row["quantity"] is not None for row in quantity_rows) else None,
    }
    raw = DatasetResult("rows", quantity_rows, values, [])
    local = _protect_quantity_dataset(
        raw,
        threshold,
        field_specs=specs("document_type", "contributors", "quantity"),
        breakdown=_Breakdown("records", "contributors_total", ORDER_INPUT, count_key="contributors"),
    )
    types_by_name = {metadata["name"]: pk for pk, metadata in sources.types.items()}
    quantity_contract = bind(
        raw,
        local,
        ("document_type",),
        {
            (row["document_type"],): {"contributors": f"quantity-contributors:{types_by_name[row['document_type']]}"}
            for row in quantity_rows
        },
        {"contributors_total": "quantity-contributors"},
        {
            (row["document_type"],): {
                "quantity": (
                    f"document-type:{types_by_name[row['document_type']]}",
                    f"quantity-contributors:{types_by_name[row['document_type']]}",
                )
            }
            for row in quantity_rows
        },
        {
            "quantity_total": (
                "quantity-contributors",
                *(f"document-type:{types_by_name[row['document_type']]}" for row in quantity_rows),
            )
        },
    )
    return {
        "counseling-hours": counseling_contract,
        "needle-exchange-balance": needle_contract,
        "ban-overview": ban_contract,
        "service-distribution": service_contract,
        "quantity-sum": quantity_contract,
    }
