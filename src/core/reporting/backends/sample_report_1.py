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

# Export-Fassung der Vollstaendigkeits-Meldungen: **nur** die eigene Zahl der
# Meldung, ohne Bezugsgroesse und ohne Prozentanteil. Die internen Fassungen in
# ``validate_completeness`` duerfen beides nennen (Snapshot-/Rohpfad hat keinen
# externen Konsumenten) — im Export waere die Bezugsgroesse dagegen der Hebel
# fuer die komplementaere Rueckrechnung (siehe ``prepare_export``).
#
# Die Vorlagen sind ``gettext_lazy`` (Refs #1744) und werden mit ``format_lazy``
# gefuellt — nicht mit ``.format()``: auf einem Lazy-Proxy waere ``.format()``
# eine **eager** Auswertung, die die Uebersetzung schon in
# ``prepare_export`` festschriebe statt erst beim Rendern. Der Platzhalter
# ``{count}`` ist Teil der msgid und muss in jeder Uebersetzung stehen bleiben.
_EXPORT_MESSAGES = {
    "unmapped_system_types": _("{count} Ereignisse ohne Berichtszuordnung im Zeitraum"),
    "events_without_client": _(
        "{count} Ereignisse ohne Personenbezug im Zeitraum — nicht in Altersgruppen/Personenzahl enthalten"
    ),
}

# Ersatztexte fuer Vollstaendigkeits-Meldungen, deren Zahl faellt: die Luecke
# bleibt sichtbar (sonst waere der Bericht irrefuehrend vollstaendig), die Zahl
# verschwindet. Bewusst ohne jede Ziffer — auch Anteile/Bezugsgroessen lassen
# die unterdrueckte Zahl zurueckrechnen.
#
# Die Formulierung sagt bewusst **nicht**, dass es solche Ereignisse gibt: der
# gleiche Text steht auch dann, wenn die Meldung nur als Unterdrueckungs-Hinweis
# fuer einen Wert eingesetzt wird, den es gar nicht gibt (``_hide_term``).
# Waeren beide Faelle unterscheidbar, verriete der Wortlaut selbst, ob die
# Luecke > 0 ist — und damit genau das, was unterdrueckt werden soll.
#
# **Uebersetzungshinweis (Refs #1744):** Die Ununterscheidbarkeit gilt je
# ``code`` und muss in **jeder** Sprache halten. Weil Platzhalter- und
# Degradations-Fassung dieselbe Konstante benutzen, ist das strukturell
# gesichert — eine Uebersetzung kann sie nicht auseinanderziehen, solange sie
# keine Ziffer und keine Bezugsgroesse ergaenzt.
_SUPPRESSED_MESSAGES = {
    "unmapped_system_types": _("Angabe zu Ereignissen ohne Berichtszuordnung im Zeitraum unterdrückt (k-Anonymität)"),
    "events_without_client": _(
        "Angabe zu Ereignissen ohne Personenbezug im Zeitraum unterdrückt (k-Anonymität) "
        "— nicht in Altersgruppen/Personenzahl enthalten"
    ),
}

# Severity der beiden Identitaets-Terme — noetig, wenn ein Unterdrueckungs-
# Hinweis fuer eine *fehlende* Meldung erzeugt wird (dann gibt es kein Original).
_ISSUE_SEVERITIES: dict[str, Literal["info", "warning"]] = {
    "unmapped_system_types": "warning",
    "events_without_client": "info",
}

# Meldungs-Terme der Vier-Term-Identitaet, in Degradations-Reihenfolge:
# ``events_without_client`` faellt zuerst (siehe ``_close_identity_leak``).
_IDENTITY_CODES = ("events_without_client", "unmapped_system_types")

# Fail-safe fuer kuenftige Checks ohne eigenen Ersatztext: lieber unspezifisch
# als eine Kleinstfallzahl im Export.
_SUPPRESSED_FALLBACK = _("Angabe unterdrückt (k-Anonymität)")


def _prepare_issue(issue: CompletenessIssue, threshold: int, complements: dict[str, int]) -> CompletenessIssue:
    """Export-Fassung einer Vollstaendigkeits-Meldung (K-Anon).

    Die Zahl faellt, wenn sie selbst unter der Schwelle liegt, wenn ihr
    Komplement unter der Schwelle liegt (Differenz-Rueckrechnung) oder wenn es
    fuer den Code keine geprueft ziffernarme Export-Formulierung gibt
    (Fail-safe fuer kuenftige Checks). Sonst wird die Meldung ohne
    Bezugsgroesse/Prozentanteil neu formuliert. Meldungen ohne ``count``
    bleiben unangetastet — ihnen fehlt die Transformationsgrundlage.
    """
    if issue.count is None:
        return issue

    template = _EXPORT_MESSAGES.get(issue.code)
    complement = complements.get(issue.code)
    drop_number = template is None or issue.count < threshold or (complement is not None and complement < threshold)
    if drop_number:
        return dataclasses.replace(
            issue,
            message=_SUPPRESSED_MESSAGES.get(issue.code, _SUPPRESSED_FALLBACK),
            count=None,
        )
    return dataclasses.replace(issue, message=format_lazy(template, count=issue.count))


def _term_status(issues: list[CompletenessIssue], code: str) -> str:
    """``"quantified"`` | ``"hidden"`` | ``"missing"`` fuer einen Meldungs-Term.

    ``"missing"`` heisst **bekannt**: ``validate_completeness`` meldet nur Luecken
    > 0, eine fehlende Meldung ist also deterministisch die Zahl 0.
    """
    for issue in issues:
        if issue.code == code:
            return "quantified" if issue.count is not None else "hidden"
    return "missing"


def _known_terms(summary: dict, issues: list[CompletenessIssue]) -> int:
    """Wie viele der vier Identitaets-Terme ein Empfaenger bestimmen kann."""
    known = 0
    if summary.get("total") is not None:
        known += 1
    # Σ der Altersgruppen ist nur ohne unterdrueckte Zelle bestimmbar; die leere
    # Liste ist bestimmbar (= 0). Fehlt die Tabelle ganz (K0-Fail-safe,
    # Refs #1768), ist Σ **unbestimmt** — nicht 0: sonst zaehlte der Fail-safe
    # als bekannter Term und liesse ohne Not eine weitere Meldung fallen.
    age_rows = summary.get("by_age_cluster")
    if age_rows is not None and not any(row.get("suppressed") for row in age_rows):
        known += 1
    known += sum(1 for code in _IDENTITY_CODES if _term_status(issues, code) != "hidden")
    return known


def _hide_term(issues: list[CompletenessIssue], code: str) -> list[CompletenessIssue]:
    """Nimmt einem Meldungs-Term seine Aussagekraft fuer die Identitaet.

    Bei bezifferter Meldung faellt die Zahl. Fehlt die Meldung, wird ein
    Unterdrueckungs-Hinweis **eingefuegt** — sonst bliebe die Abwesenheit selbst
    die Information „dieser Term ist 0".
    """
    replacement_text = _SUPPRESSED_MESSAGES[code]
    for index, issue in enumerate(issues):
        if issue.code == code:
            replaced = dataclasses.replace(issue, message=replacement_text, count=None)
            return [*issues[:index], replaced, *issues[index + 1 :]]

    placeholder = CompletenessIssue(code, replacement_text, _ISSUE_SEVERITIES[code], count=None)
    # Kanonische Reihenfolge der Meldungen beibehalten (unmapped zuerst).
    position = 0 if code == "unmapped_system_types" else len(issues)
    return [*issues[:position], placeholder, *issues[position:]]


def _close_identity_leak(summary: dict, issues: list[CompletenessIssue]) -> list[CompletenessIssue]:
    """Schliesst die Rueckrechnung ueber die Vier-Term-Identitaet.

    Konstruktionsbedingt gilt ``total + unmapped_events = Σ by_age_cluster +
    without_client`` (beide Seiten = alle sichtbaren Ereignisse im Zeitraum).
    Sind drei Terme bekannt und der vierte verborgen, ist der vierte exakt
    bestimmt — die Suppression liefe leer. Dann faellt ein weiterer Term, und
    zwar **immer** ueber die Meldungen: ``total`` und die Altersgruppen-Zellen
    gehoeren dem Report-Koerper (``suppress_sample_report_1_stats``), an dem hier
    nicht nachgebessert wird.

    Bevorzugt faellt eine bezifferte Meldung (sichtbarer Informationsverlust,
    aber kein neuer Hinweis); erst wenn keine beziffert ist, wird eine fehlende
    Meldung durch einen Unterdrueckungs-Hinweis ersetzt. Jede Runde verwandelt
    einen bekannten Term in einen verborgenen, die Schleife terminiert also
    spaetestens nach zwei Runden (mehr Meldungs-Terme gibt es nicht).
    """
    updated = list(issues)
    # Bewusst durch die Zahl der Meldungs-Terme begrenzt statt ``while``: jede
    # Runde verbirgt einen Term, mehr als zwei kann es nicht geben.
    for _round in _IDENTITY_CODES:
        if _known_terms(summary, updated) != 3:
            break
        candidate = next(
            (code for code in _IDENTITY_CODES if _term_status(updated, code) == "quantified"), None
        ) or next((code for code in _IDENTITY_CODES if _term_status(updated, code) == "missing"), None)
        if candidate is None:  # pragma: no cover - bei drei bekannten Termen unerreichbar
            break
        updated = _hide_term(updated, candidate)
    return updated


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

        Vollstaendigkeits-Meldungen werden fuer den Export **immer** neu
        formuliert (``_prepare_issue``): ohne Bezugsgroesse und Prozentanteil,
        und ganz ohne Zahl, sobald die Zahl selbst oder ihr Komplement unter
        der Schwelle liegt. Anschliessend prueft
        ``_close_identity_leak`` die Vier-Term-Identitaet als Ganzes.
        Der Hinweis auf die Luecke bleibt in jedem Fall stehen.
        ``build_dataset`` bleibt roh (Snapshot-Payload).

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

        # Konstruktionsbedingte Komplementbeziehungen zwischen Meldungszahl und
        # Aggregat (``events_in_period`` = alle sichtbaren Ereignisse im Zeitraum):
        #
        #   Σ by_age_cluster = events_in_period − without_client
        #   summary["total"] = events_in_period − unmapped_events
        #
        # Bleibt eine Meldungszahl stehen, waehrend ihre Gegenmenge unterdrueckt
        # ist, laesst sich die Gegenmenge als Differenz exakt zurueckrechnen —
        # deshalb faellt die Zahl auch dann, wenn *das Komplement* klein ist.
        # Die Komplemente **vor** der Suppression aus dem rohen Aggregat ziehen:
        # in ``summary`` sind die Zellen an dieser Stelle ggf. schon ``None``.
        complements = {
            "events_without_client": sum(row.get("count") or 0 for row in result.summary.get("by_age_cluster", [])),
            "unmapped_system_types": result.summary.get("total") or 0,
        }
        completeness = [_prepare_issue(issue, threshold, complements) for issue in result.completeness]
        # Zweiter Durchgang gegen die Rueckrechnung ueber die Identitaet als
        # Ganzes — die paarweise Komplementregel oben deckt nur eine Richtung ab.
        completeness = _close_identity_leak(summary, completeness)

        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
