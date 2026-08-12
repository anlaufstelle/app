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
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec, stamp_threshold
from core.reporting.data_source import MAPPED_EVENTS
from core.reporting.registry import register_backend

# Modul-Ebenen-Import ist zyklusfrei: ``core.services.dashboard`` verweist
# nirgends zur Importzeit auf ``core.reporting`` — der einzige Rueckverweis
# (``core.services.system.export``) ist ein lokaler Import in der Funktion.
from core.services.dashboard.external_report import _get_threshold, suppress_sample_report_1_stats
from core.services.dashboard.statistics import PERSONS_KEY

# Mapping: DocumentType.system_type -> Berichtskategorie (fehlend = ausgeschlossen).
# ``ban``/``note``/NULL fehlen bewusst — sie gehoeren nicht in den Bericht.
# Zweitschluessel ist ``DocumentType.slug`` (:data:`SLUG_MAP`, Refs #1741).
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

# Zweitschluessel: DocumentType.slug -> Berichtskategorie (Refs #1741, Entscheid
# in #1785).
#
# **Wofuer er da ist.** ``system_type`` ist ein Kern-Datenmodell-Enum und
# eingefroren (RFC #1748): ein einrichtungseigener Dokumentationstyp hat oft
# gar keinen — oder ``note``, was exportseitig identisch mit NULL ist. Genau
# diese Typen tauchten bisher ausnahmslos als „ohne Berichtszuordnung" im
# Vollstaendigkeitshinweis auf, obwohl ihr Slug sie eindeutig benennt.
#
# **Warum hart kodiert.** Das Mapping ist Fachlogik *dieses* Berichts und
# gehoert damit ins Backend, nicht in eine Einrichtungs-Einstellung: eine
# Config-UI machte Berichtszahlen per Klick verschiebbar (dasselbe Argument,
# mit dem ``category`` fuers Reporting ausgeschlossen ist, ADR-034/ADR-038).
# Eine spaetere Konfigurierbarkeit bleibt Ausbaupfad, nicht Bestand.
#
# **Globale Slug-Konvention, facility-agnostisch.** Die Schluessel sind die
# Paket-Slugs aus ``core/packages/default.yaml`` — dort tragen sie die
# Bedeutung, die dieses Mapping annimmt. Gematcht wird ausschliesslich der
# Slug-String, unabhaengig davon, ob der Typ aus dem Paket stammt oder in der
# Config-UI entstanden ist: es gibt am Datensatz keine Herkunftsangabe, und ein
# einrichtungseigener Typ mit dem Slug ``beratungsgespraech`` IST ein
# Beratungsgespraech. Die Slugs sind je Einrichtung eindeutig und nach
# Erstellung unveraenderlich (M2, ADR-037) — dieselbe Stabilitaetszusage wie
# bei ``system_type``.
#
# **Bewusst konservativ.** Nur die sieben Paket-Slugs, deren ``system_type``
# ohnehin in ``CATEGORY_MAP`` steht. ``notiz`` und ``hausverbot`` fehlen aus
# demselben Grund, aus dem ``note``/``ban`` oben fehlen — der Zweitschluessel
# darf keine Kategorie hereinholen, die der Erstschluessel bewusst auslaesst.
# Werte sind dieselben deutschen Klartext-Kategorien wie oben (Snapshot-
# Stabilitaet, siehe Kommentar an ``CATEGORY_MAP``).
SLUG_MAP = {
    "kontakt": "Kontakte",
    "krisengespraech": "Beratung",
    "beratungsgespraech": "Beratung",
    "medizinische-versorgung": "Versorgung",
    "spritzentausch": "Versorgung",
    "begleitung": "Vermittlung",
    "vermittlung": "Vermittlung",
}


def resolve_category(system_type: str | None, slug: str | None) -> str | None:
    """Berichtskategorie eines Dokumentationstyps — ``None`` = ohne Zuordnung.

    Auflösungsordnung (Refs #1741): erst :data:`CATEGORY_MAP` ueber
    ``system_type``, dann :data:`SLUG_MAP` ueber ``slug``. Der Erstschluessel
    gewinnt immer — er ist die Systemkennung, der Slug nur eine Konvention.

    „Gewinnt immer" schliesst seine **Ausschluesse** ein (Refs #1785, Review):
    ein GESETZTER ``system_type`` ist eine vollstaendige Aussage — auch wenn er
    ``note``/``ban`` lautet und damit „gehoert nicht in den Bericht" bedeutet.
    Der Slug greift deshalb NUR, wenn der Erstschluessel gar keine Aussage
    trifft (``system_type`` leer/NULL). Andernfalls zoege ein Hausverbots-Typ
    mit dem Slug ``kontakt`` seine Bann-Ereignisse in den Bericht — ein
    Disclosure-Leck.

    Einzige Auflösungsstelle des Backends: ``build_dataset`` und der
    Unmapped-Filter (:data:`_UNMAPPED_Q`) muessen dieselbe Antwort geben, sonst
    zaehlt der Vollstaendigkeitshinweis Ereignisse als fehlend, die im Bericht
    stehen (oder umgekehrt).
    """
    if system_type:
        return CATEGORY_MAP.get(system_type)
    return SLUG_MAP.get(slug or "")


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
#
# Seit dem Slug-Zweitschluessel (Refs #1741) ist „gemappt" eine Disjunktion —
# die exakte SQL-Entsprechung von :func:`resolve_category`:
#
#   gemappt = system_type IN CATEGORY_MAP
#             ODER (system_type leer/NULL UND slug IN SLUG_MAP)
#
# Der zweite Zweig ist an ``system_type`` leer/NULL **gebunden** (Refs #1785,
# Review): ein gesetzter, aber bewusst ausgeschlossener ``system_type``
# (``note``/``ban``) darf NICHT ueber den Slug hereingeholt werden. „Ohne
# Zuordnung" ist die Negation. ``slug`` ist nicht NULL-bar (``blank=True``, aber
# ``NOT NULL``) und ``document_type`` ein PROTECT-FK ohne ``null=True``, es gibt
# hier also keine Outer-Join-NULLs.
_SYSTEM_TYPE_UNSET_Q = Q(document_type__system_type__isnull=True) | Q(document_type__system_type="")
_UNMAPPED_Q = ~(
    Q(document_type__system_type__in=tuple(CATEGORY_MAP))
    | (_SYSTEM_TYPE_UNSET_Q & Q(document_type__slug__in=tuple(SLUG_MAP)))
)

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
    return Event.objects.for_facility(facility).filter(
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
    description = _("Fasst die Ereignisse mit Berichtszuordnung zu den Kategorien eines Sachberichts zusammen.")
    snapshot_enabled = True
    # K3-A3 (Refs #1770): Die ``records``-Projektion sind die Berichtskategorien
    # — sie zaehlen nur Ereignisse mit Berichtszuordnung (``CATEGORY_MAP``),
    # nicht alle Ereignisse des Zeitraums. Ohne diesen Ausweis liest sich
    # "Gesamtkontakte" wie eine Gesamtzahl, die es nicht ist.
    population = MAPPED_EVENTS

    def get_field_specs(self) -> list[FieldSpec]:
        """Spalten der tabellarischen Projektion (eine Zeile je Kategorie)."""
        return [
            FieldSpec("category", _("Kategorie")),
            FieldSpec("count", _("Anzahl")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Aggregiert Ereignisse zu Berichtskategorien und Altersgruppen."""
        base_qs = _base_queryset(facility, date_from, date_to)

        # Aggregation ueber DocumentType.system_type UND .slug — beide Schluessel
        # der Auflösung (Refs #1741) muessen in der Gruppierung stehen, sonst
        # koennte der Zweitschluessel nicht greifen. Die Ordnung bleibt
        # system_type-fuehrend (Bestandsformat des Snapshot-Payloads); die
        # publizierte Zeilenordnung stellt ohnehin die Artefaktgrenze her
        # (``ORDER_CATEGORY``, Refs #1767/#1772).
        by_dt = (
            base_qs.values("document_type__system_type", "document_type__slug")
            .annotate(count=Count("id"))
            .order_by("document_type__system_type", "document_type__slug")
        )

        # Konsolidierung auf die Berichtskategorien
        category_counts: OrderedDict[str, int] = OrderedDict()
        total = 0
        for row in by_dt:
            cat = resolve_category(row["document_type__system_type"], row["document_type__slug"])
            if cat is None:
                # Nicht zugeordnete Typen still ueberspringen — auch ``total``
                # zaehlt sie nicht mit (Bestandsverhalten). Sichtbar werden sie
                # ueber ``validate_completeness``.
                continue
            category_counts[cat] = category_counts.get(cat, 0) + row["count"]
            total += row["count"]

        by_category = [(name, count) for name, count in category_counts.items()]

        # Altersgruppen (nur Ereignisse mit Personenbezug).
        #
        # K0-b (Refs #1770): jede Zeile traegt zusaetzlich die DISTINCT-
        # Personenzahl ihres Buckets — die Groesse, gegen die die k-Schwelle im
        # Export-/PDF-Pfad prueft. Sie ist Teil des **Snapshot-Payloads**
        # (Formataenderung; Bestand per Migration 0128 nachgetragen) und
        # verlaesst den Suppressionspfad nie (``_suppress_small`` entfernt sie).
        #
        # Die Buckets entstehen bewusst allein aus ``age_cluster`` —
        # **mapping-agnostisch**: sie zaehlen jedes Ereignis mit Personenbezug,
        # zugeordnet oder nicht. Der Slug-Zweitschluessel (Refs #1741) aendert
        # daran nichts; er verschiebt nur, was in ``by_category``/``total``
        # landet.
        by_age_cluster = [
            {
                "cluster": row["client__age_cluster"],
                "label": _AGE_LABELS.get(row["client__age_cluster"], ""),
                "count": row["count"],
                PERSONS_KEY: row["persons"],
            }
            for row in base_qs.exclude(client__isnull=True)
            .values("client__age_cluster")
            .annotate(count=Count("id"), persons=Count("client", distinct=True))
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
                        _("{events} Ereignisse in {types} Dokumentationstypen ohne Berichtszuordnung im Zeitraum"),
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
        summary = stamp_threshold(summary, threshold)

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
