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
``SampleReport1Backend`` (mehrere zusammenhaengende Kennzahlen/Meldungen) — wer so
etwas aggregiert, orientiert sich an ``SampleReport1Backend.prepare_export`` statt
an dieser Vorlage.
"""

import dataclasses

from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import ORDER_INPUT, _Breakdown, suppress_single_margin


@register_backend("example")
class ExampleBackend(DatasetBackend):
    """Ereignisse je Dokumentationstyp im Zeitraum — kleinstmoegliches Referenz-Backend."""

    name = _("Beispiel: Ereignisse je Dokumentationstyp")
    disclosure_dimensions = frozenset({"event_counts"})
    description = _("Zählt die Ereignisse des Zeitraums je Dokumentationstyp.")
    snapshot_enabled = False

    def get_field_specs(self) -> list[FieldSpec]:
        # Labels als ``gettext_lazy`` (Refs #1744) — die Copy-Vorlage macht
        # vor, was der Vertrag will: sonst uebernimmt jedes abgeleitete
        # Backend deutschen Klartext in seine Spaltenkoepfe.
        return [
            FieldSpec("document_type", _("Dokumentationstyp")),
            FieldSpec("system_type", _("System-Typ")),
            FieldSpec("count", _("Anzahl")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Eine Zeile je Dokumenttyp mit Ereignissen im Zeitraum, Gesamtzahl im Summary."""
        rows = (
            Event.objects.for_facility(facility)
            .filter(
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            # document_type_id in der Gruppierung: eine Aggregation liefert
            # damit robust genau eine Zeile je DocumentType, kein N+1.
            .values("document_type_id", "document_type__name", "document_type__system_type")
            .annotate(count=Count("id"))
            .order_by("document_type__name")
        )

        records = [
            {
                "document_type": row["document_type__name"],
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

    # Deklaration fuer den zentralen Residual-Guard (K1, Refs #1770): ``total``
    # ist die Summe aller Zeilen-``count``s, ``records`` also die exakte
    # Partition dieser Randsumme. ``ORDER_INPUT``: die Query sortiert nach
    # ``document_type__name`` — die Zeilenposition ist bereits wertunabhaengig
    # und traegt keinen Rangkanal (K5).
    _EXPORT_BREAKDOWN = _Breakdown("records", "total", ORDER_INPUT)

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Lehrbeispiel (Refs #1278/#1770): so und nicht anders.

        Die einzige Rueckrechnungs-Gefahr dieses Backends ist die Identitaet
        ``total`` = Σ Zeilen-``count``: bliebe ``total`` nach einer
        Zeilen-Suppression roh stehen, waere eine unterdrueckte Zelle exakt als
        Differenz zu den sichtbaren Zeilen rueckrechenbar. Genau dafuer gibt es
        die **zentrale** Maschinerie, und dieses Backend zeigt ihre Benutzung:
        :func:`suppress_single_margin` (Refs #1835) kapselt Schwelle, R14,
        :func:`~core.services.dashboard.external_report._suppress_small`
        (``margin``/``order`` als Pflicht-Keywords, K2/K5) und
        :func:`~core.services.dashboard.external_report.apply_residual_guard`
        (K1, letzter Schritt) fuer Backends mit genau einer Randsumme.

        Die fruehere Eigenbau-Regel „faellt eine Zelle, faellt auch ``total``"
        ist damit entfallen (Refs #1770): sie war eine zweite, pauschalere
        Suppressionslogik neben der zentralen — genau das, was der
        Architektur-Guard heute verbietet. Der Spannen-Audit erreicht dieselbe
        Leakfreiheit und behaelt die Randsumme, wo sie ungefaehrlich ist.

        ``records`` wird auf die deklarierten FieldSpec-Keys zurueckprojiziert
        (wie ``SampleReport1Backend.prepare_export``) — das interne
        ``suppressed``-Flag aus ``_suppress_small`` ist keine eigene Spalte und
        wuerde sonst ungefiltert in CSV/JSON/XML landen.
        """
        records, summary = suppress_single_margin(
            facility, result, breakdown=self._EXPORT_BREAKDOWN, field_specs=self.get_field_specs()
        )
        return dataclasses.replace(result, records=records, summary=summary)
