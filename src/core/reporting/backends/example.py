"""Beispiel-Backend als Copy-Vorlage fuer neue Reporting-Plugins (M5 P6, Refs #1061).

Zeigt am minimalen Fall, was ein ``DatasetBackend`` braucht, um automatisch im
Berichte-Dropdown zu erscheinen: ``@register_backend``, die Pflichtmethoden aus
:mod:`core.reporting.base`, und ein ``prepare_export``-Lehrbeispiel der
K-Anon-Invariante (Refs #1278). Keine View-/Template-/Migrations-Aenderung
noetig — die Lazy-Discovery in :mod:`core.reporting.registry` findet dieses
Modul von selbst.

``prepare_export`` loest hier die einzige Rueckrechnungs-Gefahr dieses
Backends: ``summary["total"]`` ist die Summe aller Zeilen-``count``s
(``total = Σ counts``) — bliebe ``total`` nach einer Zeilen-Suppression roh
stehen, waere eine unterdrueckte Zelle exakt als Differenz zu den sichtbaren
Zeilen rueckrechenbar (A6.1, Refs #1024/#1016). Ein ``rows``-Backend mit einer
einzigen Kennzahl je Zeile hat dagegen KEINE Vier-Term-Identitaet wie
``JugendamtBackend`` (mehrere zusammenhaengende Kennzahlen/Meldungen) — wer so
etwas aggregiert, orientiert sich an ``JugendamtBackend.prepare_export`` statt
an dieser Vorlage.
"""

import dataclasses

from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import _get_threshold, _suppress_small


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
        rows = (
            Event.objects.filter(
                facility=facility,
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            # document_type_id in der Gruppierung: eine Aggregation liefert
            # damit robust genau eine Zeile je DocumentType, kein N+1.
            .values("document_type_id", "document_type__name", "document_type__category", "document_type__system_type")
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
        """K-Anon-Lehrbeispiel (Refs #1278): Zeilen-Suppression inkl. A6.1-Schutz.

        Nutzt ``_suppress_small`` — dieselbe Primaer-/Sekundaer-Suppression wie
        ``JugendamtBackend``/``build_external_report`` (A6.1, Refs #1024/#1016):
        eine Zelle < Schwelle wird ``count=None``; bleibt danach genau EINE
        Zelle unterdrueckt, faellt zusaetzlich die naechstkleinere sichtbare
        Zelle (``_apply_secondary_suppression``) — sonst waere sie aus den
        sichtbaren Zellen und ``total`` rueckrechenbar.

        ``total`` (= Σ aller Zeilen-``count``) faellt, wenn es selbst unter der
        Schwelle liegt (R14) ODER wenn ueberhaupt eine Zeile unterdrueckt ist:
        die Sekundaer-Suppression schuetzt nur GENAU EINE unterdrueckte Zelle
        gegen Rueckrechnung ueber die sichtbaren Zellen — bleiben mehrere
        unterdrueckt, waere ``total`` weiterhin ein Rueckrechnungs-Hebel.
        Konservativ fuers Lehrbeispiel: faellt eine Zelle, faellt auch ``total``.

        ``records`` wird auf die deklarierten FieldSpec-Keys zurueckprojiziert
        (wie ``JugendamtBackend.prepare_export``) — das interne
        ``suppressed``-Flag aus ``_suppress_small`` ist keine eigene Spalte und
        wuerde sonst ungefiltert in CSV/JSON/XML landen.
        """
        threshold = _get_threshold(facility)
        suppressed_rows = _suppress_small(result.records, threshold, count_key="count")
        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [{key: row[key] for key in field_keys} for row in suppressed_rows]

        total = result.summary["total"]
        any_row_suppressed = any(row["suppressed"] for row in suppressed_rows)
        total_faellt = total < threshold or any_row_suppressed

        summary = dict(result.summary)
        summary["total"] = None if total_faellt else total
        summary["k_anonymity_threshold"] = threshold
        return dataclasses.replace(result, records=records, summary=summary)
