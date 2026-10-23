"""Period quantity sums, bound only to numeric event fields (Refs #2515)."""

import dataclasses
from fractions import Fraction

from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event, FieldTemplate
from core.reporting.base import (
    Anforderung,
    BindungsEintrag,
    CompletenessIssue,
    DatasetBackend,
    DatasetResult,
    FeldBindung,
    FieldSpec,
    doppelbindungs_issues,
)
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.registry import register_backend
from core.reporting.values import json_number as _json_number
from core.reporting.values import read_quantity as _read_quantity
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _get_threshold,
)

QUANTITY_FIELD = FeldBindung(
    (BindungsEintrag("quantity_units", _("Stückzahl")),),
    bindet="report_key",
    field_type=FieldTemplate.FieldType.NUMBER,
)
MISSING_VALUES_CODE = "missing_field_values"
MISSING_VALUES_MESSAGE = _("Nicht verwertbare Stückzahlen oder Summen bleiben in diesem Bericht unberücksichtigt")


@register_backend("quantity-sum")
class QuantitySumBackend(DatasetBackend):
    """One quantity sum per documentation type, without monthly snapshots."""

    name = _("Summe von Zahlenfeldern")
    disclosure_dimensions = frozenset({"event_counts"})
    description = _("Summiert dokumentierte Stückzahlen je Dokumentationstyp im Zeitraum.")
    population = TYPED_EVENTS
    snapshot_enabled = False
    feld_bindungen = (QUANTITY_FIELD,)
    verfuegbarkeit = (Anforderung(feld_bindungen=(QUANTITY_FIELD,), entity_type=DocumentType.EntityType.EVENT),)

    _EXPORT_BREAKDOWN = _Breakdown("records", "contributors_total", ORDER_INPUT, count_key="contributors")

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("document_type", _("Dokumentationstyp")),
            FieldSpec("contributors", _("Beitragende Ereignisse")),
            FieldSpec("quantity", _("Stückzahl im Zeitraum")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Read each event once, resolving its field slug through the binding."""
        binding = QUANTITY_FIELD.leseschluessel(facility, entity_type=DocumentType.EntityType.EVENT)
        type_ids = set(binding.je_typ) | set(binding.doppelt)
        types = DocumentType.objects.for_facility(facility).filter(pk__in=type_ids).order_by("name", "pk")
        rows = {
            item.pk: {"document_type": item.name, "events": 0, "contributors": 0, "quantity": Fraction(0)}
            for item in types
        }
        events = Event.objects.for_facility(facility).filter(
            document_type_id__in=type_ids,
            is_deleted=False,
            occurred_at__date__gte=date_from,
            occurred_at__date__lte=date_to,
        )
        missing = 0
        for type_id, data in events.values_list("document_type_id", "data_json"):
            row = rows[type_id]
            row["events"] += 1
            value = _read_quantity(data, binding.je_typ.get(type_id))
            if value is None:
                missing += 1
                continue
            row["quantity"] += value
            row["contributors"] += 1

        # Keep the raw dataset free of report calls; preparation imports
        # the complete type and contributor contracts before projection.
        published_counts = {
            row["document_type__name"]: row["count"]
            for row in Event.objects.for_facility(facility)
            .filter(is_deleted=False, occurred_at__date__gte=date_from, occurred_at__date__lte=date_to)
            .values("document_type__name")
            .annotate(count=Count("pk"))
        }
        total = sum((row["quantity"] for row in rows.values()), Fraction(0))
        records = []
        for row in rows.values():
            if not row["events"]:
                continue
            row["quantity"] = _json_number(row["quantity"]) if row["contributors"] else None
            records.append(row)
        return DatasetResult(
            disclosure_context=(date_from, date_to, "live"),
            mode="rows",
            records=records,
            summary={
                "document_type_counts": published_counts,
                "events_total": sum(row["events"] for row in records),
                "contributors_total": sum(row["contributors"] for row in records),
                "quantity_total": _json_number(total) if all(row["quantity"] is not None for row in records) else None,
            },
            completeness=(
                [CompletenessIssue(MISSING_VALUES_CODE, MISSING_VALUES_MESSAGE, "warning", count=missing or None)]
                if missing or any(row["quantity"] is None for row in records)
                else []
            )
            + doppelbindungs_issues(binding),
        )

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """Publish the local contract through the shared population mask."""
        from core.services.dashboard.external_report import _protect_quantity_dataset
        from core.services.dashboard.shared_disclosure import project_shared_dataset

        local = _protect_quantity_dataset(
            result, _get_threshold(facility), field_specs=self.get_field_specs(), breakdown=self._EXPORT_BREAKDOWN
        )
        local = dataclasses.replace(
            local,
            completeness=[CompletenessIssue(MISSING_VALUES_CODE, MISSING_VALUES_MESSAGE, "warning", count=None)]
            + doppelbindungs_issues(QUANTITY_FIELD.leseschluessel(facility, entity_type=DocumentType.EntityType.EVENT)),
        )
        return project_shared_dataset(facility, local, "quantity-sum")
