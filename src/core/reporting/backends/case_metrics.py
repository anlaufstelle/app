"""Beispiel-Backend „Fall-Kennzahlen" (Refs #1754).

**Zweck.** Referenzfall fuer ein Aggregat aus mehreren **unabhaengigen
Skalaren** — der haeufigste Zuschnitt eines Traeger-Kennzahlenblatts und
zugleich der, bei dem am leichtesten zu viel Maschinerie eingebaut wird. Hier
gibt es keine Aufschluesselung, die eine Randsumme partitioniert; jede Zahl
steht fuer sich an der k-Schwelle. Der Bericht zeigt damit den Gegenpol zu
:mod:`~core.reporting.backends.client_metrics` (exakte Partition) und
:mod:`~core.reporting.backends.monthly_trend` (Zeilen + Randsumme).

**Kennzahlen.** Faelle sind 1:1 personengebunden (``Case.client`` ist
Pflichtfeld) — jede dieser Zahlen ist deshalb eine Personenzaehlung im
K-Anon-Sinn, auch wo sie Faelle zaehlt.

* ``neue_faelle`` — im Zeitraum angelegte Faelle (``Case.created_at``).
* ``geschlossene_faelle`` — im Zeitraum geschlossene Faelle (``Case.closed_at``,
  gesetzt von ``core.services.case.cases.close_case``). Ein spaeter
  wiedereroeffneter Fall faellt heraus: ``reopen_case`` nullt ``closed_at``.
  Das ist gewollt — die Zahl beschreibt den **aktuellen** Kenntnisstand, nicht
  eine Ereignishistorie, und einen Statusverlauf gibt es am Datenmodell nicht.
* ``offene_faelle_aktuell`` — **Momentaufnahme auf den Stichtag heute**, nicht
  auf das Zeitraumende. Ohne Statuslog laesst sich nicht rekonstruieren, welche
  Faelle an einem vergangenen Datum offen waren; ein „offen am 31.12." waere
  eine Zahl, die das Modell nicht hergibt. Wer sie braucht, braucht zuerst eine
  Statushistorie. Konsequenz fuer die Leserin: diese eine Zahl aendert sich
  zwischen zwei Aufrufen desselben Zeitraums.
* ``betroffene_personen`` — DISTINCT-Personen der im Zeitraum neuen **oder**
  geschlossenen Faelle. Bewusst als eigene Zahl statt als Summe: eine Person
  kann mehrere Faelle haben, und ein Fall kann im selben Zeitraum angelegt und
  geschlossen worden sein.

**K-Anonymitaet.** Alle vier Zahlen fallen unter der Schwelle auf ``None``, und
zwar ueber die zentrale :func:`_suppress_small` auf den ``records``-Zeilen —
``summary`` wird danach aus **denselben** suppressierten Zeilen neu gebaut.
Damit gibt es genau einen Ort, an dem ueber Unterdrueckung entschieden wird
(Architektur-Guard ``test_architecture_guards_services.py``), und Tabelle und
Aggregat koennen nicht auseinanderlaufen.

**Warum hier kein Residual-Risiko besteht.** Der Guard schuetzt gegen die
Gleichung „Randsumme − Σ sichtbare Zellen = unterdrueckte Zelle". Eine solche
Gleichung gibt es hier nicht: keine der vier Zahlen ist die Summe der uebrigen,
und keine wird als Aufschluesselung einer anderen publiziert. Die
Aufschluesselung wird deshalb mit ``margin_key=None`` deklariert — der Guard
laeuft (Pflicht fuer jedes ``prepare_export``, das ein ``summary`` schreibt),
findet aber korrekt keine Partition, an der zu rechnen waere. Zwischen den
Zahlen bestehen nur **Ungleichungen** (``betroffene_personen`` <=
``neue_faelle`` + ``geschlossene_faelle``); daraus folgt fuer eine unterdrueckte
Zahl bestenfalls eine Schranke, nie ein exakter Wert — und die Schranke ist
schwaecher als das, was die Suppression ohnehin einraeumt (< *k*).

Die vier Zeilen werden **datenunabhaengig** emittiert, auch bei Wert 0: waere
ihre Existenz an Daten geknuepft, verriete die blosse Gestalt der Tabelle,
welche Kennzahl > 0 ist (dieselbe Lehre wie beim Vollstaendigkeitsblock, K6,
Refs #1769). Die Zeilenreihenfolge ist fest verdrahtet und damit
wertunabhaengig — Voraussetzung fuer ``ORDER_INPUT`` (K5).

``snapshot_enabled = False`` (Beispielvorlage, kein Monats-Merge-Thema).
"""

import dataclasses

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import Case
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.data_source import PERSONS
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _get_threshold,
    _suppress_small,
    apply_residual_guard,
)

# Kanonische Zeilenreihenfolge — Single Source of Truth fuer den
# datenunabhaengigen Aufbau der Tabelle.
#
# Die Bezeichner sind bewusst **sprachunabhaengiger Klartext** und werden nicht
# uebersetzt: sie stehen gleichzeitig als ``summary``-Schluessel und als Wert der
# ``kennzahl``-Spalte, ein Konsument kann CSV- und JSON-Fassung also ueber
# denselben String verbinden. Uebersetzt werden die **Spaltenkoepfe**
# (``FieldSpec.label``) — das ist die Stelle, an der die Oberflaechensprache
# hingehoert (Refs #1744).
_KENNZAHLEN = ("neue_faelle", "geschlossene_faelle", "offene_faelle_aktuell", "betroffene_personen")

_COUNT_KEY = "wert"


def _visible_cases(facility):
    """Sichtbare Faelle der Einrichtung.

    ``is_deleted=False`` ist defensiv: die Invariante „Faelle kennen kein
    Soft-Delete" ist in ``src/tests/test_cases_trash_invariant.py`` gepinnt.
    Faellt sie, zaehlt dieser Bericht trotzdem richtig weiter — ein Bericht ist
    der schlechteste Ort, um eine solche Modellaenderung zu bemerken.
    """
    return Case.objects.filter(facility=facility, is_deleted=False)


@register_backend("case-metrics")
class CaseMetricsBackend(DatasetBackend):
    """Neue, geschlossene und offene Faelle sowie betroffene Personen."""

    name = _("Beispiel: Fall-Kennzahlen")
    snapshot_enabled = False
    # K3-A3 (Refs #1770): Faelle sind 1:1 personengebunden — die Grundgesamtheit
    # ist damit eine Personenmenge, nicht eine Ereignismenge. Ohne den Ausweis
    # summierte sich diese Tabelle sichtbar anders als jede Ereignistabelle
    # daneben, ohne dass eine Leserin erkennen koennte warum.
    population = PERSONS

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("kennzahl", _("Kennzahl")),
            FieldSpec(_COUNT_KEY, _("Wert")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Vier Kennzahlen im Zeitraum; ``records`` ist die Langform desselben Aggregats.

        ``offene_faelle_aktuell`` ignoriert den Zeitraum bewusst — siehe
        Modul-Docstring (Momentaufnahme auf den Stichtag heute).
        """
        cases = _visible_cases(facility)
        im_zeitraum = Q(created_at__date__gte=date_from, created_at__date__lte=date_to)
        geschlossen_im_zeitraum = Q(closed_at__date__gte=date_from, closed_at__date__lte=date_to)

        werte = {
            "neue_faelle": cases.filter(im_zeitraum).count(),
            "geschlossene_faelle": cases.filter(geschlossen_im_zeitraum).count(),
            "offene_faelle_aktuell": cases.filter(status=Case.Status.OPEN).count(),
            "betroffene_personen": cases.filter(im_zeitraum | geschlossen_im_zeitraum)
            .values("client")
            .distinct()
            .count(),
        }

        return DatasetResult(
            mode="aggregate",
            # Feste Reihenfolge, alle Zeilen immer — datenunabhaengig (siehe
            # Modul-Docstring).
            records=[{"kennzahl": key, _COUNT_KEY: werte[key]} for key in _KENNZAHLEN],
            summary=dict(werte),
            completeness=[],
        )

    # Deklaration fuer den zentralen Residual-Guard (K1, Refs #1770).
    # ``margin_key=None``: dieses Artefakt publiziert **keine** Randsumme, die
    # die Zeilen partitionieren wuerden — es gibt also keine Gleichung, ueber
    # die eine unterdrueckte Zahl rueckrechenbar waere. Der Guard laeuft
    # trotzdem (Pflicht fuer jedes ``prepare_export`` mit ``summary``) und
    # bestaetigt genau das; die Alternative — ihn wegzulassen — waere die
    # Behauptung statt der Pruefung.
    _EXPORT_BREAKDOWN = _Breakdown("records", None, ORDER_INPUT, count_key=_COUNT_KEY)

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel-Vorschau (Refs #1278/#1770).

        Alle vier Zahlen laufen als Zeilen durch :func:`_suppress_small` und
        werden unterhalb der Schwelle ``None``. ``margin=None`` ist die
        wahrheitsgemaesse Angabe (es gibt keine publizierte Randsumme) — das
        Machbarkeits-Audit (K2) laeuft damit korrekt gar nicht, statt gegen eine
        Gleichung zu rechnen, die es nicht gibt.

        ``summary`` wird aus **denselben** suppressierten Zeilen neu gebaut, es
        gibt also keine zweite Suppressionsstelle und keine Chance auf Drift
        zwischen Tabelle und Aggregat. Danach die Rueckprojektion auf die
        FieldSpec-Keys — das interne ``suppressed``-Flag ist keine Spalte.

        Erwartet das **rohe** ``build_dataset``-Ergebnis (nicht idempotent).
        """
        threshold = _get_threshold(facility)

        view = {
            "records": _suppress_small(result.records, threshold, count_key=_COUNT_KEY, margin=None, order=ORDER_INPUT),
        }
        apply_residual_guard(view, threshold, (self._EXPORT_BREAKDOWN,))

        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [{key: row[key] for key in field_keys} for row in view.get("records", [])]

        summary = dict(result.summary)
        summary.update({row["kennzahl"]: row[_COUNT_KEY] for row in records})
        summary["k_anonymity_threshold"] = threshold
        return dataclasses.replace(result, records=records, summary=summary)

    def is_available(self, facility) -> bool:
        """Immer waehlbar — und das ist hier kein vergessener Hook.

        Die Regel „``is_available`` an die Seed-/Paket-Konfiguration binden"
        (Maintainer-Entscheid 2026-08-05) greift dort, wo ein Backend
        **Konfiguration konsumiert**: ``monthly-trend`` braucht zugeordnete
        Dokumentationstypen, ``client-metrics`` Ereignistypen,
        ``ban-overview`` einen Hausverbots-Typ. Dieses Backend liest
        ausschliesslich Modellfelder von ``Case`` — Faelle gibt es in jeder
        Einrichtung, unabhaengig von jedem Dokumentationstyp. Eine Bindung an
        ``entity_type="case"``-Typen waere sogar falsch: das Standardpaket
        (``core/packages/default.yaml``) enthaelt ausschliesslich
        ``entity_type="event"``-Typen, der Bericht waere also flaechendeckend
        unsichtbar, obwohl er ueberall Daten haette.
        """
        return True
