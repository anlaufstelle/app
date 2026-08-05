"""Beispiel-Backend „Monatsverlauf nach Kategorie" (Refs #1754).

**Zweck.** Zeigt an einem zweiten, realistischeren Fall als
:mod:`~core.reporting.backends.example`, wie ein Bericht ausschliesslich aus
**Modellfeldern** entsteht: ``Event.occurred_at`` liefert die Zeitachse, der
Dokumentationstyp ueber :func:`~core.reporting.backends.sample_report_1.resolve_category`
die Sachkategorie. Kein neues Feld, keine Migration, keine View-/URL-Aenderung,
kein neuer ``SystemType``-Wert (die Werteliste ist eingefroren, RFC #1748) —
Datei ablegen, ``@register_backend`` setzen, fertig.

**Was hier zu kopieren ist.** Drei Dinge, die ein Nachahmer leicht uebersieht:

1. **Die Kategorie-Aufloesung wird importiert, nicht kopiert.**
   ``CATEGORY_MAP``/``SLUG_MAP``/``resolve_category`` leben im Sachbericht-
   Backend und bleiben dort die einzige Wahrheit. Eine zweite Kopie driftet
   garantiert — und eine driftende Kopie ist hier kein Schoenheitsfehler,
   sondern ein Disclosure-Risiko: sie koennte ``ban``/``note`` hereinholen, die
   der Erstschluessel bewusst ausschliesst (Refs #1785).
2. **Monatsbuckets entstehen in lokaler Zeit.** ``TruncMonth`` trunkiert ohne
   ``tzinfo``-Argument in der aktiven Zeitzone (``TIME_ZONE = Europe/Berlin``) —
   dieselbe Semantik, die die ``occurred_at__date``-Zeitraumgrenzen ohnehin
   verwenden. Waere eines der beiden UTC-basiert, faende ein Ereignis vom
   31.01. 23:30 Ortszeit im Februar-Bucket eines Januar-Zeitraums statt — eine
   Zeile, die sich nicht auf die Randsumme summiert, und damit ein kaputtes
   Inferenzmodell im K-Anon-Guard.
3. **Die Zeilenordnung ist wertunabhaengig.** ``records`` wird nach
   ``(monat, kategorie)`` sortiert, nicht nach Anzahl und nicht in der
   Reihenfolge, in der die Quelltypen zufaellig auftauchen. Nur deshalb darf
   :data:`~core.services.dashboard.external_report.ORDER_INPUT` verwendet
   werden: die Position einer Zeile traegt dann keine Rangaussage (K5,
   Refs #1767). Die Konsolidierungsreihenfolge des Sachberichts ist genau
   deshalb datenabhaengig und braucht ``ORDER_CATEGORY``.

**K-Anonymitaet.** Die einzige Rueckrechnungs-Identitaet dieses Backends ist
``summary["total"] = Σ records[*]["count"]`` — ``records`` partitioniert die
Randsumme exakt. Behandelt wird sie wie in ``example.py``: Randsumme < *k*
faellt selbst (R14), Zellen < *k* fallen ueber :func:`_suppress_small` inklusive
Machbarkeits-Audit (K2), und :func:`apply_residual_guard` ist der letzte
Schritt (K1). Eigene Suppressionsregeln gibt es nicht und darf es nicht geben
(Architektur-Guard ``test_architecture_guards_services.py``).

Der Vollstaendigkeitshinweis laeuft nach der K6-Regel des Sachberichts
(Refs #1769): im Export **datenunabhaengig und unbeziffert**. Eine bezifferte
Meldung „3 Ereignisse ohne Berichtszuordnung" waere selbst eine publizierte
Kleinstfallzahl — und zwar eine, die durch keine Suppression laeuft, weil sie
nicht Teil der Partition ist. ``build_dataset`` behaelt die bezifferte Fassung
fuer die interne Pruefung (Panel-Vorschau und Export sehen sie nie).

``snapshot_enabled = False``: der Bericht ist eine Beispielvorlage, sein
``summary`` soll nicht in ``StatisticsSnapshot.backend_data`` persistiert
werden (kein Monats-Merge-Thema, siehe Vertrag an
:attr:`~core.reporting.base.DatasetBackend.snapshot_enabled`).
"""

import dataclasses
from collections import defaultdict

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event

# DRY (Refs #1741/#1785): Die Kategorie-Aufloesung ist Fachlogik des
# Sachberichts und wird importiert, nicht nachgebaut.
from core.reporting.backends.sample_report_1 import CATEGORY_MAP, SLUG_MAP, resolve_category
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.data_source import MAPPED_EVENTS
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _get_threshold,
    _suppress_small,
    apply_residual_guard,
)

# SQL-Entsprechung von ``resolve_category`` auf ``DocumentType`` (nicht auf
# ``Event`` wie ``_UNMAPPED_Q`` im Sachbericht): gemappt ist ein Typ, wenn sein
# ``system_type`` in ``CATEGORY_MAP`` steht ODER — nur bei leerem/NULL
# ``system_type`` — sein Slug in ``SLUG_MAP``. Der zweite Zweig ist an den
# leeren Erstschluessel gebunden; ein gesetzter, bewusst ausgeschlossener
# ``system_type`` (``ban``/``note``) darf nicht ueber den Slug hereinkommen.
_SYSTEM_TYPE_UNSET_Q = Q(system_type__isnull=True) | Q(system_type="")
_MAPPED_DOC_TYPE_Q = Q(system_type__in=tuple(CATEGORY_MAP)) | (_SYSTEM_TYPE_UNSET_Q & Q(slug__in=tuple(SLUG_MAP)))

# Export-Fassung des Vollstaendigkeitshinweises (K6, Refs #1769) — ohne Zahl.
# Die Formulierung sagt bewusst NICHT, dass es solche Ereignisse gibt: der
# gleiche Text steht auch dann, wenn es gar keine Luecke gibt. Waeren beide
# Faelle unterscheidbar, verriete die blosse Gestalt des Blocks wieder, ob die
# Luecke > 0 ist.
_UNMAPPED_CODE = "unmapped_system_types"
_UNMAPPED_EXPORT_MESSAGE = _("Angabe zu Ereignissen ohne Berichtszuordnung im Zeitraum unterdrückt (k-Anonymität)")


def _base_queryset(facility, date_from, date_to):
    """Sichtbare Ereignisse der Einrichtung im Zeitraum (Basis aller Auswertungen)."""
    return Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )


@register_backend("monthly-trend")
class MonthlyTrendBackend(DatasetBackend):
    """Ereignisse je Monat und Berichtskategorie — Zeitreihe aus Modellfeldern."""

    name = _("Beispiel: Monatsverlauf nach Kategorie")
    snapshot_enabled = False
    # K3-A3 (Refs #1770): gezaehlt werden nur Ereignisse **mit**
    # Berichtszuordnung. Ohne diesen Ausweis liest sich ``total`` wie eine
    # Gesamtzahl aller Ereignisse des Zeitraums, die es nicht ist.
    population = MAPPED_EVENTS

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("monat", _("Monat")),
            FieldSpec("kategorie", _("Kategorie")),
            FieldSpec("count", _("Anzahl")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Eine Zeile je (Monat, Kategorie); Gesamtzahl der gemappten Ereignisse im Summary.

        Eine Aggregation ueber (Monat, ``system_type``, ``slug``) — beide
        Schluessel der Aufloesung muessen in der Gruppierung stehen, sonst
        koennte der Zweitschluessel nicht greifen. Die Konsolidierung auf
        Kategorien passiert danach in Python (wenige Zeilen je Monat), das
        Zaehlen bleibt in der Datenbank.
        """
        rows = (
            _base_queryset(facility, date_from, date_to)
            .annotate(monat=TruncMonth("occurred_at"))
            .values("monat", "document_type__system_type", "document_type__slug")
            .annotate(count=Count("id"))
        )

        buckets: defaultdict[tuple[str, str], int] = defaultdict(int)
        for row in rows:
            kategorie = resolve_category(row["document_type__system_type"], row["document_type__slug"])
            if kategorie is None:
                # Nicht zugeordnete Typen still ueberspringen — auch ``total``
                # zaehlt sie nicht mit. Sichtbar werden sie ueber
                # ``validate_completeness``.
                continue
            buckets[(row["monat"].strftime("%Y-%m"), kategorie)] += row["count"]

        # Wertunabhaengige Ausgabeordnung (K5) — Voraussetzung fuer ORDER_INPUT.
        records = [
            {"monat": monat, "kategorie": kategorie, "count": count}
            for (monat, kategorie), count in sorted(buckets.items())
        ]

        return DatasetResult(
            mode="rows",
            records=records,
            summary={"total": sum(row["count"] for row in records)},
            completeness=self.validate_completeness(facility, date_from, date_to),
        )

    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]:
        """Beziffert, was der Bericht **nicht** zeigt: Ereignisse ohne Berichtszuordnung.

        Der Check laeuft gegen die Rohdaten, nicht gegen den Datensatz — dort
        fehlen die betroffenen Ereignisse per Definition. Das Praedikat ist die
        SQL-Entsprechung derselben Aufloesung, die ``build_dataset`` verwendet;
        zwei Wahrheiten hier hiessen, dass der Hinweis Ereignisse als fehlend
        zaehlt, die im Bericht stehen (oder umgekehrt).
        """
        unmapped = _base_queryset(facility, date_from, date_to).aggregate(
            count=Count("id", filter=~Q(document_type__in=DocumentType.objects.filter(_MAPPED_DOC_TYPE_Q)))
        )["count"]
        if not unmapped:
            return []
        return [
            CompletenessIssue(
                _UNMAPPED_CODE,
                # ``format_lazy`` statt f-String (Refs #1744): der Text folgt
                # der Sprache der Empfaengerin, nicht der des schreibenden
                # Prozesses.
                format_lazy(_("{count} Ereignisse ohne Berichtszuordnung im Zeitraum"), count=unmapped),
                "warning",
                count=unmapped,
            )
        ]

    # Deklaration fuer den zentralen Residual-Guard (K1, Refs #1770):
    # ``total`` ist die Summe aller Zeilen-``count``s, ``records`` also die
    # exakte Partition dieser Randsumme. ``ORDER_INPUT``, weil ``build_dataset``
    # bereits wertunabhaengig nach (Monat, Kategorie) sortiert.
    # ``reveals_person_gap`` bleibt False: die Zellen sind Ereigniszaehlungen,
    # es gibt kein zusaetzliches Personenkriterium, das ein Pin oberhalb der
    # Schwelle verraten koennte.
    _EXPORT_BREAKDOWN = _Breakdown("records", "total", ORDER_INPUT)

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel-Vorschau (Refs #1278/#1770).

        Vorlage ist ``ExampleBackend.prepare_export`` — dieselbe Reihenfolge,
        dieselben Pflicht-Keywords, kein Eigenbau:

        * Randsumme < *k* wird ``None`` (R14) — sie ist selbst eine
          Kleinstfallzahl.
        * :func:`_suppress_small` nullt Zellen < *k* und fuehrt das
          Machbarkeits-Audit (K2) gleich mit aus; ``margin`` ist die
          **publizierte** Randsumme, ``order`` die kanonische Ausgabeordnung
          unterdrueckter Zeilen.
        * :func:`apply_residual_guard` ist der **letzte** Schritt: er fuehrt die
          Empfaenger-Inferenz aus und laesst notfalls die Randsumme oder die
          ganze Aufschluesselung fallen.

        Danach wird auf die deklarierten FieldSpec-Keys zurueckprojiziert — das
        interne ``suppressed``-Flag aus ``_suppress_small`` ist keine Spalte und
        landete sonst ungefiltert in CSV/JSON/XML.

        Der Vollstaendigkeitsblock wird **datenunabhaengig** aus dem festen Code
        gebaut (K6, Refs #1769), nicht aus ``result.completeness`` abgeleitet:
        jede Ableitung waere wieder eine datenabhaengige Verzweigung, und eine
        bezifferte Meldung publizierte eine Zahl, die nie durch eine
        Suppressionsregel gelaufen ist.

        Erwartet das **rohe** ``build_dataset``-Ergebnis; zweimal aufrufen ist
        kein sinnvoller Aufruf (``prepare_export`` ist nicht idempotent).
        """
        threshold = _get_threshold(facility)
        total = result.summary["total"]
        published_total = None if total < threshold else total
        view = {
            "total": published_total,
            "records": _suppress_small(result.records, threshold, margin=published_total, order=ORDER_INPUT),
        }
        apply_residual_guard(view, threshold, (self._EXPORT_BREAKDOWN,))

        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [{key: row[key] for key in field_keys} for row in view.get("records", [])]

        summary = dict(result.summary)
        summary["total"] = view["total"]
        summary["k_anonymity_threshold"] = threshold

        completeness = [CompletenessIssue(_UNMAPPED_CODE, _UNMAPPED_EXPORT_MESSAGE, "warning", count=None)]
        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)

    def is_available(self, facility) -> bool:
        """Waehlbar, sobald die Einrichtung mindestens einen Dokumentationstyp
        mit Berichtszuordnung fuehrt (Seed-/Paket-Bindung, Maintainer-Entscheid
        2026-08-05).

        Ohne einen solchen Typ waere der Bericht konstruktionsbedingt leer —
        jedes Ereignis fiele in den Vollstaendigkeitshinweis. Gefiltert wird auf
        ``is_active``: ein deaktivierter Typ ist Konfigurations-Altlast und
        begruendet kein Angebot im Dropdown. Fuer die **Daten** gilt das nicht,
        dort zaehlt die historische Zuordnung (siehe ``build_dataset``).
        """
        return DocumentType.objects.filter(facility=facility, is_active=True).filter(_MAPPED_DOC_TYPE_Q).exists()
