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
  zwischen zwei Aufrufen desselben Zeitraums. Damit die Kennzeichnung sie auch
  erreicht, traegt das Artefakt selbst einen ``CompletenessIssue``
  (:data:`_POINT_IN_TIME_ISSUE`) — ein Docstring wird nicht mitexportiert.
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

**K4a — die Stichtagszahl ist ein Differenzkanal ueber den Erstellungszeitpunkt**
(Refs #1754, finale Fix-Welle). ``offene_faelle_aktuell`` haengt am Tag der
Erstellung, nicht am Berichtszeitraum. Zwei Herausgaben **desselben** Zeitraums
zu verschiedenen Zeitpunkten unterscheiden sich genau in dieser Zahl, und ihre
Differenz ist die Netto-Veraenderung der offenen Faelle zwischen den beiden
Tagen — eine Groesse, die in keinem der beiden Artefakte steht und durch keine
Suppressionsregel laeuft (bei Faellen zugleich eine Personenzahl, siehe oben).
Wer taeglich abruft, baut daraus eine Zeitreihe mit Aufloesung 1, obwohl jede
einzelne Zahl die k-Schwelle passiert hat. Dasselbe gilt fuer ``aktiv_aktuell``
in :mod:`~core.reporting.backends.ban_overview`.

Einordnung wie K4a (Refs #1770): **akzeptierte Definitionsgrenze, kein offener
Befund.** Die Zusicherung gilt je Artefakt und je Zeitraum; der zweite
Bezugspunkt ist hier gar kein Zeitraum, sondern ein Zeitpunkt. Artefaktlokal ist
nichts auszurichten — es gibt keine Zelle, an der eine Regel ansetzen koennte,
und ein Guard muesste wissen, welche frueheren Ausgaben desselben Berichts an
denselben Empfaenger gegangen sind. Die Kennzahl bleibt (Maintainer-Entscheid
2026-08-05); flankierend macht die **Freigabe-Uebersicht**
(:class:`core.views.statistics.ReportReleaseLogView`,
:mod:`core.services.dashboard.release_log`) wiederholte Herausgaben desselben
Zeitraums sichtbar, und :data:`_POINT_IN_TIME_ISSUE` sagt der Empfaengerin im
Artefakt selbst, dass die Zahl am Erstellungstag haengt.

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
from core.reporting.base import (
    CompletenessIssue,
    DatasetBackend,
    DatasetResult,
    FieldSpec,
    project_records,
    stamp_threshold,
)
from core.reporting.data_source import CASES_IN_PERIOD
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

# Momentaufnahme-Kennzeichnung im **Artefakt** (Refs #1754, Review I1). Der
# Docstring erreicht die Empfaengerin nie; ohne diesen Hinweis liest sie
# ``offene_faelle_aktuell`` als vierte Zahl desselben Zeitraums und vergleicht
# zwei Berichte, die nicht vergleichbar sind.
#
# Datenunabhaengig und ziffernfrei — dieselbe Auflage wie beim
# Vollstaendigkeitsblock (K6, Refs #1769): der Hinweis steht immer, in jeder
# Datenlage, und nennt nie eine Groesse. ``info``, weil er kein Mangel ist,
# sondern eine Lesehilfe. ``code`` ist sprachunabhaengiger Klartext.
_POINT_IN_TIME_CODE = "point_in_time_open_cases"
_POINT_IN_TIME_ISSUE = CompletenessIssue(
    _POINT_IN_TIME_CODE,
    _(
        "Die Zahl der aktuell offenen Fälle ist eine Momentaufnahme zum Zeitpunkt der Erstellung "
        "und bezieht sich nicht auf den Berichtszeitraum"
    ),
    "info",
    count=None,
)


def _visible_cases(facility):
    """Sichtbare Faelle der Einrichtung.

    ``is_deleted=False`` war defensiv gedacht — die Invariante „Faelle kennen
    kein Soft-Delete" ist mit dem Fall-Loeschpfad gefallen (Refs #1818,
    ADR-044). Der Filter ist seither tragend, und er trennt zwei Redaktionen,
    die man leicht verwechselt:

    * **Fall-Loeschweg:** der Fall wandert in den Papierkorb und zaehlt ab
      diesem Moment nicht mehr mit — auch nicht nach der Tilgung, denn
      ``anonymize_case`` redigiert nur den Inhalt und laesst ``is_deleted=True``
      stehen. Die Fallzahl sinkt also EINMAL, beim Loeschen (rueckwirkend auch
      fuer bereits abgeschlossene Zeitraeume), und aendert sich beim
      Fristablauf nicht mehr.
    * **Personen-Anonymisierung:** ``_redact_cases_and_episodes`` leert die
      Faelle einer Person, ohne sie zu loeschen — ``is_deleted`` bleibt
      ``False``, sie zaehlen weiter. Das ist die Redaktion, die die Zeitreihe
      stetig haelt.

    Beide Zusagen sind in ``test_reporting_case_metrics.py`` gepinnt.
    """
    return Case.objects.for_facility(facility).filter(is_deleted=False)


@register_backend("case-metrics")
class CaseMetricsBackend(DatasetBackend):
    """Neue, geschlossene und offene Faelle sowie betroffene Personen."""

    name = _("Beispiel: Fall-Kennzahlen")
    description = _("Zählt die im Zeitraum angelegten und abgeschlossenen Fälle sowie den Fallbestand am Stichtag.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770): Die Grundgesamtheit sind **Faelle**, nicht Ereignisse
    # und nicht ``PERSONS``. Der frueher deklarierte ``PERSONS``-Code war
    # sachlich falsch (Refs #1754, Review I1): er ist definiert als „Personen mit
    # mindestens einem Ereignis im Zeitraum", ein Fall setzt aber kein Ereignis
    # voraus — und ``offene_faelle_aktuell`` wertet den Zeitraum ueberhaupt nicht
    # aus. Ein ungefaehr passender Ausweis ist hier schlimmer als keiner: er soll
    # gerade die Differenz zwischen Basismengen benennen. ``CASES_IN_PERIOD``
    # sagt beides — Zeitraum UND Stichtagsanteil.
    population = CASES_IN_PERIOD

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
            completeness=self.validate_completeness(facility, date_from, date_to),
        )

    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]:
        """Kennzeichnet die Stichtagszahl — datenunabhaengig, ohne Datenbankzugriff.

        Der Hinweis haengt an der **Konstruktion** des Berichts, nicht an seinen
        Daten: ``offene_faelle_aktuell`` ist immer eine Momentaufnahme, ob die
        Einrichtung nun drei Faelle fuehrt oder dreitausend. Deshalb keine
        Abfrage und keine Verzweigung — beides waere ein datenabhaengiger Kanal
        ueber die blosse Existenz des Hinweises (K6-Lehre, Refs #1769).
        """
        return [_POINT_IN_TIME_ISSUE]

    # Deklaration fuer den zentralen Residual-Guard (K1, Refs #1770).
    # ``margin_key=None``: dieses Artefakt publiziert **keine** Randsumme, die
    # die Zeilen partitionieren wuerden — es gibt also keine Gleichung, ueber
    # die eine unterdrueckte Zahl rueckrechenbar waere. Der Guard laeuft
    # trotzdem (Pflicht fuer jedes ``prepare_export`` mit ``summary``) und
    # bestaetigt genau das; die Alternative — ihn wegzulassen — waere die
    # Behauptung statt der Pruefung.
    #
    # **Wer hier je eine Randsumme nachruestet, muss zuerst die Nullzeilen
    # loswerden** (Refs #1754): Das Inferenzmodell des Guards setzt fuer Zeilen
    # einer Aufschluesselung die Existenz-Untergrenze ``lo = 1`` an
    # (``external_report._row_min_value``) — eine GROUP-BY-Zeile existiert nur,
    # weil sie mindestens einen Fall traegt. Dieses Backend emittiert seine
    # Zeilen dagegen datenunabhaengig, auch mit Wert 0 (K6, siehe
    # Modul-Docstring). Mit einer deklarierten Randsumme waere das
    # Intervallmodell fuer eine Nullzeile unerfuellbar, und ein leeres Intervall
    # gilt dort als begruendete Ausnahme — der Guard liefe fail-OPEN. Solange
    # ``margin_key=None`` ist, kommt das Modell gar nicht erst zum Zug; die
    # beiden Entscheidungen haengen also zusammen (dieselbe Falle beschreibt
    # ``counseling_hours.build_dataset`` aus der Gegenrichtung: dort werden
    # Nullzeilen weggelassen, WEIL es eine Randsumme gibt).
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

        Der Momentaufnahme-Hinweis wird aus dem **festen** Code gebaut, nicht aus
        ``result.completeness`` abgeleitet (K6-Muster, Refs #1769): jede
        Ableitung waere eine datenabhaengige Verzweigung, und ein kuenftiger,
        bezifferter Hinweis erreichte den Export sonst ungeprueft. Fail-safe in
        die richtige Richtung — lieber eine Meldung zu wenig als eine
        Kleinstfallzahl zu viel.

        Erwartet das **rohe** ``build_dataset``-Ergebnis (nicht idempotent).
        """
        threshold = _get_threshold(facility)

        view = {
            "records": _suppress_small(result.records, threshold, count_key=_COUNT_KEY, margin=None, order=ORDER_INPUT),
        }
        apply_residual_guard(view, threshold, (self._EXPORT_BREAKDOWN,))

        records = project_records(view.get("records", []), self.get_field_specs())

        summary = dict(result.summary)
        # Fail-safe-Reihenfolge: erst alles auf ``None``, dann die
        # suppressierten Zeilen einspielen. Die Eskalationsleiter des Guards
        # kann eine Aufschluesselung im Extremfall komplett fallen lassen
        # (``_drop_breakdowns`` entfernt den Key) — ohne diese Vorbelegung
        # ueberlebten die **rohen** Werte aus ``build_dataset`` im ``summary``.
        # Heute unerreichbar (es gibt keine Randsumme, also keine Befunde), aber
        # eine Suppression darf nie davon abhaengen, dass ein anderer Codepfad
        # nicht ausgeloest wird.
        summary.update(dict.fromkeys(_KENNZAHLEN, None))
        summary.update({row["kennzahl"]: row[_COUNT_KEY] for row in records})
        summary = stamp_threshold(summary, threshold)
        return dataclasses.replace(result, records=records, summary=summary, completeness=[_POINT_IN_TIME_ISSUE])

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
