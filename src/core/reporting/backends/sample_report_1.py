"""Beispiel-Sachbericht als ``DatasetBackend`` (M5 P2, Refs #1061).

Migration der bisher hart kodierten Statistik aus
``core/services/system/export.py`` in das Plugin-System. Die Aggregation ist
**unveraendert** uebernommen (Verhaltensparitaet): ``summary`` traegt exakt das
bisherige Dict-Format — inklusive ``by_category`` als Liste von Tupeln, ueber
die ``core/export/sample_report_1_pdf.html`` iteriert und die
``_merge_sample_report_1_stats`` nach dem JSON-Roundtrip (Snapshots) wieder
normalisiert.

Neu ist allein :meth:`SampleReport1Backend.validate_completeness`: die Checks
brauchen die Rohdaten, die nicht im Datensatz landen (nicht zugeordnete
Dokumenttypen, Ereignisse ohne Personenbezug).

:meth:`SampleReport1Backend.prepare_export` legt die K-Anon-Suppression des
PDF-Pfads (:func:`~core.services.dashboard.external_report.suppress_sample_report_1_stats`,
Refs #1278) auf Export und Panel-Vorschau — ``build_dataset`` selbst bleibt
roh, damit die Snapshot-Payload unveraendert bleibt.
"""

import dataclasses
from collections import OrderedDict
from typing import Literal

from django.db.models import Count, Q
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend

# Modul-Ebenen-Import ist zyklusfrei: ``core.services.dashboard`` verweist
# nirgends zur Importzeit auf ``core.reporting`` — der einzige Rueckverweis
# (``core.services.system.export``) ist ein lokaler Import in der Funktion.
from core.services.dashboard.external_report import _get_threshold, suppress_sample_report_1_stats

# Mapping: DocumentType.system_type -> Berichtskategorie (fehlend = ausgeschlossen).
# ``ban``/``note``/NULL fehlen bewusst — sie gehoeren nicht in den Bericht.
# Zweitschlüssel ``DocumentType.slug`` (seit M2 vorhanden) folgt als eigenes
# Folge-Issue — Integrationspunkt.
#
# Die Kategoriennamen bleiben bewusst **deutscher Klartext** und werden NICHT
# ``gettext_lazy`` (Refs #1744): sie landen als Schluessel in
# ``summary["by_category"]`` und damit im persistierten Snapshot-Payload
# (``StatisticsSnapshot.backend_data``) sowie im monatsuebergreifenden Merge
# (``_merge_sample_report_1_stats``). Ein locale-abhaengiger Wert machte den
# gespeicherten Bestand von der Sprache des schreibenden Prozesses abhaengig —
# zwei Monate, zwei Kategoriennamen, kein Merge.
CATEGORY_MAP = {
    "contact": "Kontakte",
    "crisis": "Beratung",
    "medical": "Versorgung",
    "needle_exchange": "Versorgung",
    "accompaniment": "Vermittlung",
    "counseling": "Beratung",
    "referral": "Vermittlung",
}

# Anzeigelabels der Altersgruppen — lazy wie bisher, damit der Snapshot-Payload
# byte-gleich zum Bestand bleibt.
_AGE_LABELS = {
    "u18": _("Unter 18"),
    "18_26": _("18–26"),
    "27_plus": _("27+"),
    "unknown": _("Unbekannt"),
}


# Ereignisse, deren Dokumenttyp keiner Berichtskategorie zugeordnet ist —
# ``system_type`` NULL/leer eingeschlossen. Das ``isnull``-ODER ist bewusst
# redundant: Django rendert ``~Q(...__in=[...])`` auf einer NULL-baren Spalte
# als ``NOT (x IN (...) AND x IS NOT NULL)`` und faengt NULL-Zeilen damit
# bereits mit ein. Blankes SQL-``NOT IN`` tut das nicht — der explizite Zweig
# haelt die Absicht unabhaengig von diesem ORM-Detail fest.
_UNMAPPED_Q = Q(document_type__system_type__isnull=True) | ~Q(document_type__system_type__in=tuple(CATEGORY_MAP))

# Export-Fassung der Vollstaendigkeits-Meldungen (K6, Refs #1769). Sie ist die
# EINZIGE Fassung, die ein externer Empfaenger je zu sehen bekommt: der Block
# wird **datenunabhaengig** gebaut — beide Meldungen immer, nie mit Zahl.
#
# Die frueheren bezifferten Export-Vorlagen sind ersatzlos entfallen. Tragend war
# nicht, wie gross eine genannte Zahl ist, sondern dass sie ueberhaupt genannt
# wird: eine bezifferte Meldung publiziert implizit „mein Komplement >= k", also
# eine untere Schranke — selbst wenn jede Alterszelle unterdrueckt ist. Die
# Entscheidung darueber lief ueber eine Disjunktion auf Rohdaten und war damit
# selbst informativ.
#
# Die Formulierung sagt bewusst **nicht**, dass es solche Ereignisse gibt — der
# gleiche Text steht auch dann, wenn es gar keine Luecke gibt. Waeren beide
# Faelle unterscheidbar, verriete der Wortlaut selbst, ob die Luecke > 0 ist,
# und der Kanal waere durch die Hintertuer zurueck.
#
# **Uebersetzungshinweis (Refs #1744):** Die Ziffernfreiheit gilt je ``code`` und
# muss in **jeder** Sprache halten; eine Uebersetzung darf weder eine Zahl noch
# eine Bezugsgroesse ergaenzen.
_SUPPRESSED_MESSAGES = {
    "unmapped_system_types": _("Angabe zu Ereignissen ohne Berichtszuordnung im Zeitraum unterdrückt (k-Anonymität)"),
    "events_without_client": _(
        "Angabe zu Ereignissen ohne Personenbezug im Zeitraum unterdrückt (k-Anonymität) "
        "— nicht in Altersgruppen/Personenzahl enthalten"
    ),
}

# Severity der beiden Meldungen — im Export gibt es kein Original mehr, aus dem
# sie uebernommen werden koennte.
_ISSUE_SEVERITIES: dict[str, Literal["info", "warning"]] = {
    "unmapped_system_types": "warning",
    "events_without_client": "info",
}

# Kanonische Reihenfolge des Export-Blocks (unmapped zuerst) — Single Source of
# Truth fuer den datenunabhaengigen Aufbau in ``prepare_export``.
_EXPORT_ISSUE_CODES = ("unmapped_system_types", "events_without_client")


def _base_queryset(facility, date_from, date_to):
    """Sichtbare Ereignisse der Einrichtung im Zeitraum (Basis aller Auswertungen)."""
    return Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )


@register_backend("sample-report-1")
class SampleReport1Backend(DatasetBackend):
    """Kontakte/Beratung/Versorgung/Vermittlung je Zeitraum, plus Altersgruppen."""

    # Refs #1279 (sichtbares Label) und #1739 (interne Bezeichner): Modul, Slug
    # und Anzeigename sind bewusst traegerneutral — der Bericht ist eine frei
    # anpassbare Beispielvorlage, kein Formular einer bestimmten Behoerde.
    name = _("Beispiel-Sachbericht")
    snapshot_enabled = True

    def get_field_specs(self) -> list[FieldSpec]:
        """Spalten der tabellarischen Projektion (eine Zeile je Kategorie)."""
        return [
            FieldSpec("category", _("Kategorie")),
            FieldSpec("count", _("Anzahl")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Aggregiert Ereignisse zu Berichtskategorien und Altersgruppen."""
        base_qs = _base_queryset(facility, date_from, date_to)

        # Aggregation ueber DocumentType.system_type
        by_dt = (
            base_qs.values("document_type__system_type")
            .annotate(count=Count("id"))
            .order_by("document_type__system_type")
        )

        # Konsolidierung auf die Berichtskategorien
        category_counts: OrderedDict[str, int] = OrderedDict()
        total = 0
        for row in by_dt:
            system_type = row["document_type__system_type"]
            cat = CATEGORY_MAP.get(system_type)
            if cat is None:
                # Nicht zugeordnete Typen still ueberspringen — auch ``total``
                # zaehlt sie nicht mit (Bestandsverhalten). Sichtbar werden sie
                # ueber ``validate_completeness``.
                continue
            category_counts[cat] = category_counts.get(cat, 0) + row["count"]
            total += row["count"]

        by_category = [(name, count) for name, count in category_counts.items()]

        # Altersgruppen (nur Ereignisse mit Personenbezug)
        by_age_cluster = [
            {
                "cluster": row["client__age_cluster"],
                "label": _AGE_LABELS.get(row["client__age_cluster"], ""),
                "count": row["count"],
            }
            for row in base_qs.exclude(client__isnull=True)
            .values("client__age_cluster")
            .annotate(count=Count("id"))
            .order_by("-count")
        ]

        unique_clients = base_qs.exclude(client__isnull=True).values("client").distinct().count()

        return DatasetResult(
            mode="aggregate",
            records=[{"category": name, "count": count} for name, count in by_category],
            summary={
                "total": total,
                "by_category": by_category,
                "by_age_cluster": by_age_cluster,
                "unique_clients": unique_clients,
            },
            completeness=self.validate_completeness(facility, date_from, date_to),
        )

    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]:
        """Beziffert, was der Bericht **nicht** zeigt.

        Beide Checks laufen gegen die Rohdaten, nicht gegen den Datensatz:
        nicht zugeordnete Dokumenttypen fehlen dort per Definition, und
        Ereignisse ohne Personenbezug sind in den Kategorien enthalten, in
        Altersgruppen/Personenzahl aber nicht.
        """
        # Alle vier Kennzahlen in *einer* Aggregation: ``build_dataset`` ruft
        # diese Methode immer mit, und der Hybrid-/Snapshot-Pfad ruft
        # ``build_dataset`` je Zeitsegment einmal auf — Einzel-``count()``s
        # wuerden sich dort vervielfachen.
        #
        # ``events_in_period`` statt ``total``: der gleichnamige Summary-Key
        # zaehlt nur die *gemappten* Ereignisse — hier ist die Bezugsgroesse
        # aber alles Sichtbare im Zeitraum.
        counts = _base_queryset(facility, date_from, date_to).aggregate(
            events_in_period=Count("id"),
            without_client=Count("id", filter=Q(client__isnull=True)),
            unmapped_events=Count("id", filter=_UNMAPPED_Q),
            unmapped_types=Count("document_type_id", filter=_UNMAPPED_Q, distinct=True),
        )
        issues: list[CompletenessIssue] = []

        # (a) Dokumenttypen ohne Berichtszuordnung (inkl. system_type NULL/leer).
        #
        # ``format_lazy`` statt f-String (Refs #1744): der Text erscheint in
        # Panel und JSON-/XML-Export und muss der Sprache des Empfaengers
        # folgen. Die Platzhalter sind benannt — eine Uebersetzung darf sie
        # umstellen, aber nicht weglassen.
        if counts["unmapped_events"]:
            issues.append(
                CompletenessIssue(
                    "unmapped_system_types",
                    format_lazy(
                        _("{events} Ereignisse in {types} Dokumenttypen ohne Berichtszuordnung im Zeitraum"),
                        events=counts["unmapped_events"],
                        types=counts["unmapped_types"],
                    ),
                    "warning",
                    count=counts["unmapped_events"],
                )
            )

        # (b) Ereignisse ohne Personenbezug — die Luecke in by_age_cluster
        # und unique_clients.
        if counts["without_client"]:
            share = round(counts["without_client"] * 100 / counts["events_in_period"])
            issues.append(
                CompletenessIssue(
                    "events_without_client",
                    format_lazy(
                        _(
                            "{without_client} von {events_in_period} Ereignissen im Zeitraum ohne "
                            "Personenbezug ({share} %) — nicht in Altersgruppen/Personenzahl enthalten"
                        ),
                        without_client=counts["without_client"],
                        events_in_period=counts["events_in_period"],
                        share=share,
                    ),
                    "info",
                    count=counts["without_client"],
                )
            )

        return issues

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel-Vorschau (Refs #1278, T1).

        Dieselbe Maschinerie wie fuer das PDF (``suppress_sample_report_1_stats``):
        Zellen < Schwelle werden ``count=None``/``suppressed=True`` (inkl.
        sekundaerer Suppression gegen Randsummen-Rueckrechnung),
        ``unique_clients`` und die Randsumme ``total`` < Schwelle werden
        ``None`` (Security R14). ``records`` wird aus den bereits
        suppressierten Kategorie-Zeilen neu projiziert — sonst haette der
        CSV-Export die Rohzahlen weiterhin gezeigt.

        Der Vollstaendigkeits-Block ist **datenunabhaengig** (K6, Refs #1769):
        beide Meldungen immer, nie mit Zahl, in fester Reihenfolge — er wird
        aus ``_EXPORT_ISSUE_CODES`` gebaut und nicht aus ``result.completeness``
        abgeleitet. Damit sagt weder seine Gestalt noch sein Inhalt etwas ueber
        die Rohdaten aus. Der Hinweis auf die Luecke bleibt in jedem Fall
        stehen; ``build_dataset`` bleibt roh (Snapshot-Payload) und behaelt die
        bezifferten Fassungen fuer die interne Pruefung.

        Erwartet das **rohe** ``build_dataset``-Ergebnis: ``by_category``
        kommt als ``(name, count)``-Tupel herein und geht als Dict-Zeilen
        heraus — ein zweiter Durchlauf ueber dasselbe Ergebnis ist damit
        kein sinnvoller Aufruf.
        """
        threshold = _get_threshold(facility)

        summary = suppress_sample_report_1_stats(facility, result.summary)
        # Transparenz analog ``build_external_report``-Metadaten: der Empfaenger
        # soll erkennen, ab welcher Fallzahl unterdrueckt wurde.
        summary["k_anonymity_threshold"] = threshold

        records = [{"category": row["name"], "count": row["count"]} for row in summary["by_category"]]

        # K6 (Refs #1769): fest verdrahtet statt aus ``result.completeness``
        # abgeleitet — jede Ableitung waere wieder eine datenabhaengige
        # Verzweigung. Codes, die ``validate_completeness`` kuenftig ergaenzt,
        # erreichen den Export damit nicht ungeprueft (fail-safe: lieber eine
        # Meldung zu wenig als eine Kleinstfallzahl zu viel).
        completeness = [
            CompletenessIssue(code, _SUPPRESSED_MESSAGES[code], _ISSUE_SEVERITIES[code], count=None)
            for code in _EXPORT_ISSUE_CODES
        ]

        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
