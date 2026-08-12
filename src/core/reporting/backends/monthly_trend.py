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
   ``TYP_BINDUNG`` lebt im Sachbericht-Backend und bleibt dort die einzige
   Wahrheit; dieses Modul importiert die Deklaration und leitet Aufloesung UND
   SQL-Praedikat daraus ab. Eine zweite Kopie driftet garantiert — und eine
   driftende Kopie ist hier kein Schoenheitsfehler, sondern ein
   Disclosure-Risiko: sie koennte ``ban``/``note`` hereinholen, die der
   Erstschluessel bewusst ausschliesst (Refs #1785). Bis E1 (Refs #1824) hatte
   dieses Modul genau deshalb noch ein eigenes ``_MAPPED_DOC_TYPE_Q`` — die
   Warnung stand hier, die zweite Wahrheit auch.
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

**K4a — dieser Bericht ist eine echte Verfeinerung des Sachberichts, und das
muss man wissen** (Refs #1770, Analyse #1742; Fix-Runde 1 zu #1754).

Fuer dieselbe Einrichtung und denselben Zeitraum gilt konstruktionsbedingt:

    Σ ueber die Monatszeilen einer Kategorie
        = die Kategoriezahl des Beispiel-Sachberichts
    Σ ueber alle Monatszeilen
        = dessen ``total``

Das ist kein Zufall und keine Naeherung, sondern eine Identitaet: beide Backends
lesen dieselbe Basismenge (``_base_queryset``: Facility, ``is_deleted=False``,
dieselben ``occurred_at__date``-Grenzen), loesen ueber **dieselben** Maps auf
(:func:`~core.reporting.backends.sample_report_1.resolve_category`, importiert
statt kopiert) und zaehlen dieselben Ereignisse. Dieses Backend zerlegt die
Kategoriezahl nur zusaetzlich nach Monat.

**Konsequenz.** Die k-Anonymitaets-Zusicherung gilt je Artefakt und je Zeitraum.
Werden Sachbericht und Monatsverlauf desselben Zeitraums herausgegeben, ist eine
im Sachbericht unterdrueckte Kategoriezelle aus den sichtbaren Monatszeilen
summierbar — und umgekehrt ein unterdrueckter Monat aus der Kategoriezahl minus
den uebrigen Monaten. Das gilt auch dann, wenn in **keinem** der beiden Berichte
eine einzige Zelle unterdrueckt wurde: die feinere Aufloesung ist selbst der
Zugewinn. Der Kanal wird durch dieses Backend also nicht neu geschaffen (K4a
existiert, seit es Berichte ueber Teilzeitraeume gibt), aber **breiter**.

**Artefaktuebergreifend gibt es keine Suppression, und das ist eine
Definitionsgrenze, kein Versaeumnis** (siehe
:mod:`core.services.dashboard.release_log`): es gibt keine Zelle, an der eine
artefaktlokale Regel ansetzen koennte, und ein Backend darf sie erst recht nicht
erfinden. Die flankierende Massnahme ist die **Freigabe-Uebersicht** (K4a) —
sie macht ueberlappende Herausgaben sichtbar, damit die Einrichtung entscheiden
kann, ob sie beide Berichte an denselben Empfaenger gibt.

**Auflage fuer Nachahmer.** Wer ein Backend baut, das dieselbe Grundgesamtheit
ueber dieselben Maps **feiner** aufschluesselt als ein bestehendes, baut eine
Verfeinerung. Drei Fragen, alle drei mit „ja" beantwortet ⇒ es ist eine:
dieselben Maps? dieselbe Basismenge (Filter, Zeitraumsemantik)? dieselbe
Zaehlgroesse? Dann gehoert die Beziehung **in den Modul-Docstring** — sie ist
sonst nirgends sichtbar, weder im Code noch im Artefakt, und die Freigabepraxis
kann nur beruecksichtigen, was jemand aufgeschrieben hat.

``snapshot_enabled = False``: der Bericht ist eine Beispielvorlage, sein
``summary`` soll nicht in ``StatisticsSnapshot.backend_data`` persistiert
werden (kein Monats-Merge-Thema, siehe Vertrag an
:attr:`~core.reporting.base.DatasetBackend.snapshot_enabled`).
"""

import dataclasses
from collections import defaultdict

from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event

# DRY (Refs #1741/#1785/#1824): Die Typ-Bindung ist Fachlogik des Sachberichts
# und wird importiert, nicht nachgebaut. Aus der einen Deklaration kommen hier
# BEIDE Sichten — die Python-Aufloesung in ``build_dataset`` und das
# SQL-Praedikat in ``validate_completeness``/``verfuegbarkeit``.
from core.reporting.backends.sample_report_1 import (
    _ISSUE_SEVERITIES,
    _SUPPRESSED_MESSAGES,
    TYP_BINDUNG,
    unknown_key_q,
    unmapped_q,
)
from core.reporting.base import Anforderung, CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.data_source import MAPPED_EVENTS
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import ORDER_INPUT, _Breakdown, suppress_single_margin

# Export-Fassung des Vollstaendigkeitshinweises (K6, Refs #1769) — ohne Zahl.
# Die Formulierung sagt bewusst NICHT, dass es solche Ereignisse gibt: der
# gleiche Text steht auch dann, wenn es gar keine Luecke gibt. Waeren beide
# Faelle unterscheidbar, verriete die blosse Gestalt des Blocks wieder, ob die
# Luecke > 0 ist.
_UNMAPPED_CODE = "unmapped_system_types"
_UNKNOWN_CODE = "unknown_report_keys"
# Kanonische, datenunabhaengige Reihenfolge des Export-Blocks dieses Berichts
# (K6, Refs #1769). Beide Codes teilen sich mit dem Sachbericht Text UND
# Severity: importiert statt kopiert (Refs #1754), denn es ist **derselbe** Code
# in beiden Berichten, und die K6-Auflage „ziffernfrei in JEDER Sprache" gilt je
# Code. Zwei gleichlautende Literale waeren zwei Uebersetzungseintraege, die
# getrennt driften koennen — und ein Empfaenger, der beide Artefakte hat, laese
# zu demselben Code zwei verschiedene Saetze.
_EXPORT_ISSUE_CODES = (_UNMAPPED_CODE, _UNKNOWN_CODE)


def _base_queryset(facility, date_from, date_to):
    """Sichtbare Ereignisse der Einrichtung im Zeitraum (Basis aller Auswertungen)."""
    return Event.objects.for_facility(facility).filter(
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )


@register_backend("monthly-trend")
class MonthlyTrendBackend(DatasetBackend):
    """Ereignisse je Monat und Berichtskategorie — Zeitreihe aus Modellfeldern."""

    name = _("Beispiel: Monatsverlauf nach Kategorie")
    description = _("Zählt die Ereignisse mit Berichtszuordnung Monat für Monat, aufgeschlüsselt nach Kategorie.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770): gezaehlt werden nur Ereignisse **mit**
    # Berichtszuordnung. Ohne diesen Ausweis liest sich ``total`` wie eine
    # Gesamtzahl aller Ereignisse des Zeitraums, die es nicht ist.
    population = MAPPED_EVENTS
    # Dieselbe Deklaration wie der Sachbericht (Refs #1824) — sichtbar gemacht,
    # nicht nur importiert: E2 liest daraus „dieser Typ erscheint in …".
    typ_bindung = TYP_BINDUNG
    # Waehlbar, sobald die Einrichtung mindestens einen **aktiven**
    # Dokumentationstyp mit Berichtszuordnung UND ``entity_type="event"``
    # fuehrt (Seed-/Paket-Bindung, Maintainer-Entscheid 2026-08-05).
    #
    # Ohne einen solchen Typ waere der Bericht konstruktionsbedingt leer — jedes
    # Ereignis fiele in den Vollstaendigkeitshinweis. ``is_active`` ist die
    # Konfigurationsfrage: ein deaktivierter Typ ist Altlast und begruendet kein
    # Angebot im Dropdown; fuer die **Daten** gilt das nicht, dort zaehlt die
    # historische Zuordnung (siehe ``build_dataset``). ``entity_type`` ist die
    # zweite Bedingung (Refs #1754, analog ``client-metrics``):
    # ``build_dataset`` zaehlt ``Event``-Zeilen, und ein Personen- oder
    # Fall-Formular kann denselben Systemtyp tragen, ohne je ein Ereignis zu
    # erzeugen — der Bericht stuende im Dropdown und zeigte dauerhaft Nullen.
    verfuegbarkeit = (Anforderung(TYP_BINDUNG, entity_type=DocumentType.EntityType.EVENT),)

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("monat", _("Monat")),
            FieldSpec("kategorie", _("Kategorie")),
            FieldSpec("count", _("Anzahl")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Eine Zeile je (Monat, Kategorie); Gesamtzahl der gemappten Ereignisse im Summary.

        Eine Aggregation ueber (Monat, Berichtsschluessel). Welche Felder in
        die Gruppierung gehoeren, sagt die Deklaration selbst
        (``values_fields()``) — seit E3 (Refs #1849) genau eines. Die
        Konsolidierung auf Kategorien passiert danach in Python (wenige Zeilen
        je Monat), das Zaehlen bleibt in der Datenbank.
        """
        rows = (
            _base_queryset(facility, date_from, date_to)
            .annotate(monat=TruncMonth("occurred_at"))
            .values("monat", *TYP_BINDUNG.values_fields("document_type__"))
            .annotate(count=Count("id"))
        )

        buckets: defaultdict[tuple[str, str], int] = defaultdict(int)
        for row in rows:
            kategorie = TYP_BINDUNG.resolve_row(row, "document_type__")
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
        """Beziffert, was der Bericht **nicht** zeigt — in derselben Dreiteilung
        wie der Sachbericht (Refs #1819/#1849).

        Beide Praedikate kommen aus ``sample_report_1``, aus derselben
        Deklaration wie die Aufloesung in ``build_dataset``: zwei Wahrheiten
        hier hiessen, dass der Hinweis Ereignisse als fehlend zaehlt, die im
        Bericht stehen (oder umgekehrt) — und dass zwei Berichte ueber dieselben
        Daten sich widersprechen.

        Ein **gesetzter** Berichtsschluessel ist eine vollstaendige Aussage und
        erzeugt keinen Hinweis mehr, auch wenn sie „gehoert in keinen Bericht"
        lautet. Ein gesetzter, aber unbekannter Schluessel bekommt dagegen einen
        eigenen Code (E-H).
        """
        counts = _base_queryset(facility, date_from, date_to).aggregate(
            unmapped=Count("id", filter=unmapped_q()),
            unknown=Count("id", filter=unknown_key_q()),
        )
        issues: list[CompletenessIssue] = []
        # ``format_lazy`` statt f-String (Refs #1744): der Text folgt der
        # Sprache der Empfaengerin, nicht der des schreibenden Prozesses.
        if counts["unmapped"]:
            issues.append(
                CompletenessIssue(
                    _UNMAPPED_CODE,
                    format_lazy(_("{count} Ereignisse ohne Berichtszuordnung im Zeitraum"), count=counts["unmapped"]),
                    "warning",
                    count=counts["unmapped"],
                )
            )
        if counts["unknown"]:
            issues.append(
                CompletenessIssue(
                    _UNKNOWN_CODE,
                    format_lazy(
                        _("{count} Ereignisse mit einem Berichtsschlüssel, den kein Bericht kennt"),
                        count=counts["unknown"],
                    ),
                    "warning",
                    count=counts["unknown"],
                )
            )
        return issues

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

        Vorlage ist ``ExampleBackend.prepare_export`` — dieselbe zentrale
        Maschinerie ueber :func:`suppress_single_margin` (Refs #1835):
        Randsumme < *k* wird ``None`` (R14), ``_suppress_small`` nullt Zellen
        < *k* inkl. Machbarkeits-Audit (K2, ``margin``/``order`` als
        Pflicht-Keywords), :func:`~core.services.dashboard.external_report.apply_residual_guard`
        (K1) ist der letzte Schritt, danach die Rueckprojektion auf die
        deklarierten FieldSpec-Keys — das interne ``suppressed``-Flag ist
        keine Spalte und landete sonst ungefiltert in CSV/JSON/XML.

        Der Vollstaendigkeitsblock wird **datenunabhaengig** aus dem festen Code
        gebaut (K6, Refs #1769), nicht aus ``result.completeness`` abgeleitet:
        jede Ableitung waere wieder eine datenabhaengige Verzweigung, und eine
        bezifferte Meldung publizierte eine Zahl, die nie durch eine
        Suppressionsregel gelaufen ist.

        Erwartet das **rohe** ``build_dataset``-Ergebnis; zweimal aufrufen ist
        kein sinnvoller Aufruf (``prepare_export`` ist nicht idempotent).
        """
        records, summary = suppress_single_margin(
            facility, result, breakdown=self._EXPORT_BREAKDOWN, field_specs=self.get_field_specs()
        )
        completeness = [
            CompletenessIssue(code, _SUPPRESSED_MESSAGES[code], _ISSUE_SEVERITIES[code], count=None)
            for code in _EXPORT_ISSUE_CODES
        ]
        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
