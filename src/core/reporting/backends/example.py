"""Beispiel-Backend als Copy-Vorlage fuer neue Reporting-Plugins (M5 P6, Refs #1061).

Zeigt am minimalen Fall, was ein ``DatasetBackend`` braucht, um automatisch im
Berichte-Dropdown zu erscheinen: ``@register_backend``, die Pflichtmethoden aus
:mod:`core.reporting.base`, und ein ``prepare_export``-Lehrbeispiel der
K-Anon-Invariante (Refs #1278). Keine View-/Template-/Migrations-Aenderung
noetig — die Lazy-Discovery in :mod:`core.reporting.registry` findet dieses
Modul von selbst.

``prepare_export`` zeigt hier nur die Minimalfassung (Zellen-/Randsummen-
Schwelle): ein ``rows``-Backend mit einer einzigen Kennzahl je Zeile hat keine
Vier-Term-Identitaet wie ``JugendamtBackend`` — wer mehrere zusammenhaengende
Kennzahlen aggregiert, orientiert sich an ``JugendamtBackend.prepare_export``
statt an dieser Vorlage.
"""

import dataclasses

from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import _get_threshold


@register_backend("example")
class ExampleBackend(DatasetBackend):
    """Ereignisse je Dokumenttyp im Zeitraum — kleinstmoegliches Referenz-Backend."""

    name = _("Beispiel: Ereignisse je Dokumenttyp")
    snapshot_enabled = False

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("document_type", "Dokumenttyp"),
            FieldSpec("category", "Kategorie"),
            FieldSpec("system_type", "System-Typ"),
            FieldSpec("count", "Anzahl"),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Eine Zeile je Dokumenttyp mit Ereignissen im Zeitraum, Gesamtzahl im Summary."""
        # (facility, name, category) ist DB-eindeutig
        # (unique_facility_documenttype_name_category) -> eine Aggregation
        # ergibt garantiert eine Zeile je DocumentType, kein N+1.
        rows = (
            Event.objects.filter(
                facility=facility,
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            .values("document_type__name", "document_type__category", "document_type__system_type")
            .annotate(count=Count("id"))
            .order_by("document_type__name")
        )
        records = [
            {
                "document_type": row["document_type__name"],
                "category": str(DocumentType.Category(row["document_type__category"]).label),
                "system_type": row["document_type__system_type"] or "",
                "count": row["count"],
            }
            for row in rows
        ]
        return DatasetResult(
            mode="rows",
            records=records,
            summary={"total": sum(row["count"] for row in records)},
            completeness=[],  # bewusst minimal — Lehrbeispiel ohne eigene Datenqualitaets-Checks
        )

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Lehrbeispiel (Refs #1278): Zellen/Randsumme < Schwelle -> ``None``.

        Siehe Modul-Docstring zur Grenze dieser Minimalfassung gegenueber
        ``JugendamtBackend.prepare_export``.
        """
        threshold = _get_threshold(facility)
        records = [{**row, "count": None} if row["count"] < threshold else row for row in result.records]
        total = result.summary["total"]
        summary = {**result.summary, "total": None if total < threshold else total, "k_anonymity_threshold": threshold}
        return dataclasses.replace(result, records=records, summary=summary)
