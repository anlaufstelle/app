"""Jugendamt-Sachbericht als ``DatasetBackend`` (M5 P2, Refs #1061).

Migration der bisher hart kodierten Statistik aus
``core/services/system/export.py`` in das Plugin-System. Die Aggregation ist
**unveraendert** uebernommen (Verhaltensparitaet): ``summary`` traegt exakt das
bisherige Dict-Format — inklusive ``by_category`` als Liste von Tupeln, ueber
die ``core/export/jugendamt_pdf.html`` iteriert und die
``_merge_jugendamt_stats`` nach dem JSON-Roundtrip (Snapshots) wieder
normalisiert.

Neu ist allein :meth:`JugendamtBackend.validate_completeness`: die Checks
brauchen die Rohdaten, die nicht im Datensatz landen (nicht zugeordnete
Dokumenttypen, Ereignisse ohne Personenbezug).

:meth:`JugendamtBackend.prepare_export` legt die K-Anon-Suppression des
PDF-Pfads (:func:`~core.services.dashboard.external_report.suppress_jugendamt_stats`,
Refs #1278) auf Export und Panel-Vorschau — ``build_dataset`` selbst bleibt
roh, damit die Snapshot-Payload unveraendert bleibt.
"""

import dataclasses
from collections import OrderedDict
from typing import Literal

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend

# Modul-Ebenen-Import ist zyklusfrei: ``core.services.dashboard`` verweist
# nirgends zur Importzeit auf ``core.reporting`` — der einzige Rueckverweis
# (``core.services.system.export``) ist ein lokaler Import in der Funktion.
from core.services.dashboard.external_report import _get_threshold, suppress_jugendamt_stats

# Mapping: DocumentType.system_type -> Jugendamt-Kategorie (fehlend = ausgeschlossen).
# ``ban``/``note``/NULL fehlen bewusst — sie gehoeren nicht in den Bericht.
# Zweitschlüssel ``DocumentType.slug`` (seit M2 vorhanden) folgt als eigenes
# Folge-Issue — Integrationspunkt.
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


# Ereignisse, deren Dokumenttyp keiner Jugendamt-Kategorie zugeordnet ist —
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
_EXPORT_MESSAGES = {
    "unmapped_system_types": "{count} Ereignisse ohne Jugendamt-Zuordnung im Zeitraum",
    "events_without_client": (
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
# fuer einen Wert eingesetzt wird, den es gar nicht gibt (``_verberge_term``).
# Waeren beide Faelle unterscheidbar, verriete der Wortlaut selbst, ob die
# Luecke > 0 ist — und damit genau das, was unterdrueckt werden soll.
_SUPPRESSED_MESSAGES = {
    "unmapped_system_types": "Angabe zu Ereignissen ohne Jugendamt-Zuordnung im Zeitraum unterdrückt (k-Anonymität)",
    "events_without_client": (
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
# ``events_without_client`` faellt zuerst (siehe ``_schliesse_identitaets_leak``).
_IDENTITY_CODES = ("events_without_client", "unmapped_system_types")

# Fail-safe fuer kuenftige Checks ohne eigenen Ersatztext: lieber unspezifisch
# als eine Kleinstfallzahl im Export.
_SUPPRESSED_FALLBACK = "Angabe unterdrückt (k-Anonymität)"


def _prepare_issue(issue: CompletenessIssue, threshold: int, komplemente: dict[str, int]) -> CompletenessIssue:
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

    vorlage = _EXPORT_MESSAGES.get(issue.code)
    komplement = komplemente.get(issue.code)
    zahl_faellt = vorlage is None or issue.count < threshold or (komplement is not None and komplement < threshold)
    if zahl_faellt:
        return dataclasses.replace(
            issue,
            message=_SUPPRESSED_MESSAGES.get(issue.code, _SUPPRESSED_FALLBACK),
            count=None,
        )
    return dataclasses.replace(issue, message=vorlage.format(count=issue.count))


def _term_status(issues: list[CompletenessIssue], code: str) -> str:
    """``"beziffert"`` | ``"verborgen"`` | ``"fehlt"`` fuer einen Meldungs-Term.

    ``"fehlt"`` heisst **bekannt**: ``validate_completeness`` meldet nur Luecken
    > 0, eine fehlende Meldung ist also deterministisch die Zahl 0.
    """
    for issue in issues:
        if issue.code == code:
            return "beziffert" if issue.count is not None else "verborgen"
    return "fehlt"


def _bekannte_terme(summary: dict, issues: list[CompletenessIssue]) -> int:
    """Wie viele der vier Identitaets-Terme ein Empfaenger bestimmen kann."""
    bekannt = 0
    if summary.get("total") is not None:
        bekannt += 1
    # Σ der Altersgruppen ist nur ohne unterdrueckte Zelle bestimmbar; die leere
    # Liste ist bestimmbar (= 0).
    if not any(zeile.get("suppressed") for zeile in summary.get("by_age_cluster", [])):
        bekannt += 1
    bekannt += sum(1 for code in _IDENTITY_CODES if _term_status(issues, code) != "verborgen")
    return bekannt


def _verberge_term(issues: list[CompletenessIssue], code: str) -> list[CompletenessIssue]:
    """Nimmt einem Meldungs-Term seine Aussagekraft fuer die Identitaet.

    Bei bezifferter Meldung faellt die Zahl. Fehlt die Meldung, wird ein
    Unterdrueckungs-Hinweis **eingefuegt** — sonst bliebe die Abwesenheit selbst
    die Information „dieser Term ist 0".
    """
    ersatz_text = _SUPPRESSED_MESSAGES[code]
    for index, issue in enumerate(issues):
        if issue.code == code:
            ersetzt = dataclasses.replace(issue, message=ersatz_text, count=None)
            return [*issues[:index], ersetzt, *issues[index + 1 :]]

    platzhalter = CompletenessIssue(code, ersatz_text, _ISSUE_SEVERITIES[code], count=None)
    # Kanonische Reihenfolge der Meldungen beibehalten (unmapped zuerst).
    position = 0 if code == "unmapped_system_types" else len(issues)
    return [*issues[:position], platzhalter, *issues[position:]]


def _schliesse_identitaets_leak(summary: dict, issues: list[CompletenessIssue]) -> list[CompletenessIssue]:
    """Schliesst die Rueckrechnung ueber die Vier-Term-Identitaet.

    Konstruktionsbedingt gilt ``total + unmapped_events = Σ by_age_cluster +
    without_client`` (beide Seiten = alle sichtbaren Ereignisse im Zeitraum).
    Sind drei Terme bekannt und der vierte verborgen, ist der vierte exakt
    bestimmt — die Suppression liefe leer. Dann faellt ein weiterer Term, und
    zwar **immer** ueber die Meldungen: ``total`` und die Altersgruppen-Zellen
    gehoeren dem Report-Koerper (``suppress_jugendamt_stats``), an dem hier
    nicht nachgebessert wird.

    Bevorzugt faellt eine bezifferte Meldung (sichtbarer Informationsverlust,
    aber kein neuer Hinweis); erst wenn keine beziffert ist, wird eine fehlende
    Meldung durch einen Unterdrueckungs-Hinweis ersetzt. Jede Runde verwandelt
    einen bekannten Term in einen verborgenen, die Schleife terminiert also
    spaetestens nach zwei Runden (mehr Meldungs-Terme gibt es nicht).
    """
    ergebnis = list(issues)
    # Bewusst durch die Zahl der Meldungs-Terme begrenzt statt ``while``: jede
    # Runde verbirgt einen Term, mehr als zwei kann es nicht geben.
    for _runde in _IDENTITY_CODES:
        if _bekannte_terme(summary, ergebnis) != 3:
            break
        kandidat = next(
            (code for code in _IDENTITY_CODES if _term_status(ergebnis, code) == "beziffert"), None
        ) or next((code for code in _IDENTITY_CODES if _term_status(ergebnis, code) == "fehlt"), None)
        if kandidat is None:  # pragma: no cover - bei drei bekannten Termen unerreichbar
            break
        ergebnis = _verberge_term(ergebnis, kandidat)
    return ergebnis


def _base_queryset(facility, date_from, date_to):
    """Sichtbare Ereignisse der Einrichtung im Zeitraum (Basis aller Auswertungen)."""
    return Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )


@register_backend("jugendamt")
class JugendamtBackend(DatasetBackend):
    """Kontakte/Beratung/Versorgung/Vermittlung je Zeitraum, plus Altersgruppen."""

    # Refs #1279: das sichtbare Label wurde bewusst umbenannt; interne
    # Bezeichner (Slug, Modul, Kategorien-Map) bleiben "jugendamt".
    name = _("Beispiel-Sachbericht")
    snapshot_enabled = True

    def get_field_specs(self) -> list[FieldSpec]:
        """Spalten der tabellarischen Projektion (eine Zeile je Kategorie)."""
        return [
            FieldSpec("category", "Kategorie"),
            FieldSpec("count", "Anzahl"),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Aggregiert Ereignisse zu Jugendamt-Kategorien und Altersgruppen."""
        base_qs = _base_queryset(facility, date_from, date_to)

        # Aggregation ueber DocumentType.system_type
        by_dt = (
            base_qs.values("document_type__system_type")
            .annotate(count=Count("id"))
            .order_by("document_type__system_type")
        )

        # Konsolidierung auf die Jugendamt-Kategorien
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

        # (a) Dokumenttypen ohne Jugendamt-Zuordnung (inkl. system_type NULL/leer).
        if counts["unmapped_events"]:
            issues.append(
                CompletenessIssue(
                    "unmapped_system_types",
                    f"{counts['unmapped_events']} Ereignisse in {counts['unmapped_types']} "
                    "Dokumenttypen ohne Jugendamt-Zuordnung im Zeitraum",
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
                    f"{counts['without_client']} von {counts['events_in_period']} Ereignissen im Zeitraum "
                    f"ohne Personenbezug ({share} %) — nicht in Altersgruppen/Personenzahl enthalten",
                    "info",
                    count=counts["without_client"],
                )
            )

        return issues

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel-Vorschau (Refs #1278, T1).

        Dieselbe Maschinerie wie fuer das PDF (``suppress_jugendamt_stats``):
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
        ``_schliesse_identitaets_leak`` die Vier-Term-Identitaet als Ganzes.
        Der Hinweis auf die Luecke bleibt in jedem Fall stehen.
        ``build_dataset`` bleibt roh (Snapshot-Payload).

        Erwartet das **rohe** ``build_dataset``-Ergebnis: ``by_category``
        kommt als ``(name, count)``-Tupel herein und geht als Dict-Zeilen
        heraus — ein zweiter Durchlauf ueber dasselbe Ergebnis ist damit
        kein sinnvoller Aufruf.
        """
        threshold = _get_threshold(facility)

        summary = suppress_jugendamt_stats(facility, result.summary)
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
        komplemente = {
            "events_without_client": sum(row.get("count") or 0 for row in result.summary.get("by_age_cluster", [])),
            "unmapped_system_types": result.summary.get("total") or 0,
        }
        completeness = [_prepare_issue(issue, threshold, komplemente) for issue in result.completeness]
        # Zweiter Durchgang gegen die Rueckrechnung ueber die Identitaet als
        # Ganzes — die paarweise Komplementregel oben deckt nur eine Richtung ab.
        completeness = _schliesse_identitaets_leak(summary, completeness)

        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
