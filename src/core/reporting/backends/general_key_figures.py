"""Live general figures using the existing external disclosure pipeline."""

from django.utils.translation import gettext_lazy as _

from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.data_source import POPULATION_NOTES
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import build_external_report


@register_backend("general-key-figures")
class GeneralKeyFiguresBackend(DatasetBackend):
    name = _("Allgemeine Kennzahlen")
    description = _("Zeigt Kontakte und Personen nach Kontaktstufe, Dokumentationstyp und Altersgruppe.")

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("table", _("Tabelle")),
            FieldSpec("label", _("Kennzahl")),
            FieldSpec("count", _("Anzahl")),
            FieldSpec("population", _("Grundgesamtheit")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Project only counts already protected by build_external_report."""
        report = build_external_report(facility, date_from, date_to)
        populations = report["metadata"]["populations"]
        records = []

        def append(table, label, count):
            records.append(
                {
                    "table": table,
                    "label": label,
                    "count": count,
                    "population": str(POPULATION_NOTES[populations[table]]),
                }
            )

        append("total_contacts", _("Gesamtkontakte"), report["total_contacts"])
        append("unique_clients", _("Unterschiedliche Personen"), report["unique_clients"])
        for stage, count in report.get("by_contact_stage", {}).items():
            from core.models import Client

            append("by_contact_stage", Client.ContactStage(stage).label, count)
        for row in report.get("by_document_type", []):
            append("by_document_type", row["name"], row["count"])
        for row in report.get("by_age_cluster", []):
            append("by_age_cluster", row["label"], row["count"])
        return DatasetResult(
            "aggregate", records, {**report, "k_anonymity_threshold": report["metadata"]["k_anonymity_threshold"]}, []
        )

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """The external report is already protected; never suppress it twice."""
        return result
