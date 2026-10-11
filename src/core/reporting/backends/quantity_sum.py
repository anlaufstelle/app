"""Period quantity sums, bound only to numeric event fields (Refs #2515)."""

import dataclasses
import math
from fractions import Fraction

from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event, FieldTemplate
from core.reporting.backends.needle_exchange_balance import as_number
from core.reporting.base import (
    Anforderung,
    BindungsEintrag,
    CompletenessIssue,
    DatasetBackend,
    DatasetResult,
    FeldBindung,
    FieldSpec,
    doppelbindungs_issues,
    project_records,
    stamp_threshold,
)
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _get_threshold,
    _suppress_small,
    apply_residual_guard,
    build_external_report,
)
from core.services.file_vault import safe_decrypt

QUANTITY_FIELD = FeldBindung(
    (BindungsEintrag("quantity_units", _("Stückzahl")),),
    bindet="report_key",
    field_type=FieldTemplate.FieldType.NUMBER,
)
MISSING_VALUES_CODE = "missing_field_values"
MISSING_VALUES_MESSAGE = _("Nicht verwertbare Stückzahlen oder Summen bleiben in diesem Bericht unberücksichtigt")


def _read_quantity(data, slug):
    """Reject malformed payloads and non-finite numbers before aggregation."""
    if slug is None or not isinstance(data, dict):
        return None
    number = as_number(safe_decrypt(data.get(slug), default=None))
    if number is None or (isinstance(number, float) and not math.isfinite(number)):
        return None
    return Fraction(str(number))


def _json_number(value):
    """Keep integral sums as integers, including totals beyond float range."""
    if value.denominator == 1:
        return value.numerator
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


@register_backend("quantity-sum")
class QuantitySumBackend(DatasetBackend):
    """One quantity sum per documentation type, without monthly snapshots."""

    name = _("Summe von Zahlenfeldern")
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

        # Reuse the complete general-figures decision, including secondary
        # suppression and any whole-table removal by the residual guard.
        disclosure = build_external_report(facility, date_from, date_to)
        published_counts = {row["name"]: row["count"] for row in disclosure.get("by_document_type", [])}
        total = sum((row["quantity"] for row in rows.values()), Fraction(0))
        records = []
        for row in rows.values():
            if not row["events"]:
                continue
            row["quantity"] = _json_number(row["quantity"]) if row["contributors"] else None
            records.append(row)
        return DatasetResult(
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
        """Share the type decision; never publish a bound-event subtotal."""
        threshold = _get_threshold(facility)
        published_counts = result.summary["document_type_counts"]
        rows = [dict(row) for row in result.records]
        # Contributors are event counts, too. Withhold their margin whenever
        # any row is hidden; a bound subtotal could unlock another report.
        contributions = _suppress_small(rows, threshold, count_key="contributors", margin=None, order=ORDER_INPUT)
        complete = bool(rows)
        for row, contribution in zip(rows, contributions, strict=True):
            missing = row["events"] - row["contributors"]
            # The general report still exposes event counts. Publishing a
            # contributor count must not reveal a small missing-value gap.
            if (
                published_counts.get(row["document_type"]) is None
                or contribution.get("suppressed")
                or 0 < missing < threshold
            ):
                row["contributors"] = None
                row["quantity"] = None
                complete = False
        # The contributor margin is published only for a fully visible table.
        # Still pass it through the central guard so that its partition contract
        # remains protected if subsequent suppression rules become stricter.
        view = {"records": rows, "contributors_total": result.summary["contributors_total"] if complete else None}
        apply_residual_guard(view, threshold, (self._EXPORT_BREAKDOWN,))
        rows = view.get("records", [])
        for row in rows:
            if row.get("suppressed"):
                row["quantity"] = None
                complete = False
        summary = stamp_threshold(
            {
                "contributors_total": view["contributors_total"],
                "quantity_total": result.summary["quantity_total"]
                if complete and view["contributors_total"] is not None
                else None,
            },
            threshold,
        )
        return dataclasses.replace(
            result,
            records=project_records(rows, self.get_field_specs()),
            summary=summary,
            # Data-independent, unnumbered completeness avoids a small-gap signal.
            completeness=[CompletenessIssue(MISSING_VALUES_CODE, MISSING_VALUES_MESSAGE, "warning", count=None)]
            + doppelbindungs_issues(QUANTITY_FIELD.leseschluessel(facility, entity_type=DocumentType.EntityType.EVENT)),
        )
