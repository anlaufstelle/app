"""Beispiel-Backend „Hausverbots-Übersicht" (Refs #1754).

**Zweck und Abgrenzung — bitte zuerst lesen.** Hausverbote sind bewusst **kein**
Teil des Sachberichts: der Backfill gibt Hausverbots-Typen den Sentinel
``excluded``, und seit E3 (Refs #1849) gibt es auch keinen Slug-Zweitschluessel
mehr, ueber den sie durch die Hintertuer hereinkaemen (Disclosure-Lehre,
Refs #1785). Dieser Bericht ist ein **interner**
Auswertungspfad und existiert per ausdruecklichem Produktentscheid (Maintainer,
2026-08-05, #1754) — nicht, weil die Ausschluss-Entscheidung des Sachberichts
aufgeweicht worden waere. Wer dieses Modul als Vorlage nimmt, uebernimmt damit
auch die Auflage: ein Bericht ueber besonders eingriffsintensive Vorgaenge zeigt
Zahlen, nichts weiter.

**Was der Bericht bewusst NICHT kann.** Keine Aufschluesselung (nach Grund,
Dauer, Person, Dokumenttyp), keine Personenliste, kein Freitext. Der
``grund``-Wert wird nirgends gelesen — er ist der sensibelste Teil des
Datensatzes und hat in einem Aggregat nichts zu suchen; die Rollenpruefung, die
ihn in der Oberflaeche schuetzt (``_user_may_see_grund`` in
``core/services/system/bans.py``), gibt es im Berichtspfad gar nicht.

Die „strengste Suppression" dieses Berichts ist deshalb **strukturell**, nicht
numerisch: eine eigene, hoehere Schwelle waere eine zweite Suppressionslogik
neben der zentralen — genau das, was der Architektur-Guard verbietet und was
erfahrungsgemaess driftet. Stattdessen gibt es schlicht nichts zu
aggregieren, was feiner waere als drei Gesamtzahlen, und jede davon laeuft durch
die zentrale Schwelle.

**Kennzahlen.**

* ``ausgesprochen`` — Hausverbots-Ereignisse mit ``occurred_at`` im Zeitraum.
* ``aktiv_aktuell`` — **Momentaufnahme auf den Stichtag heute**: ``aktiv``
  gesetzt UND ``bis`` leer oder >= heute. Bewusst ohne Zeitraumbezug (ein
  frueher ausgesprochenes Hausverbot gilt weiter) — und bewusst mit dem
  **heutigen** Feldwert: ``aktiv``/``bis`` sind aktuelle Zustaende, kein
  Verlauf. Wird ein Hausverbot heute aufgehoben, sinkt diese Zahl auch fuer
  einen laengst vergangenen Berichtszeitraum. Das Artefakt traegt die
  Kennzeichnung selbst (:data:`_POINT_IN_TIME_ISSUE`) — ein Docstring wird nicht
  mitexportiert, und „so viele gelten heute" ist eine andere Aussage ueber eine
  Einrichtung als „so viele im ersten Halbjahr".
* ``abgelaufen_im_zeitraum`` — Hausverbote mit nichtleerem, gueltigem ``bis``
  innerhalb des Zeitraums. Der ``aktiv``-Schalter wird hier **nicht**
  ausgewertet: er beschreibt den heutigen Zustand, und ihn hier einzurechnen
  hiesse, dass ein Aufheben von heute die Zahl eines abgeschlossenen Zeitraums
  nachtraeglich veraendert.

**Datengrundlage.** ``Event`` mit ``document_type__system_type == "ban"``,
``is_deleted=False``, Facility-gefiltert. Ein Slug-Fallback existiert seit E3
(Refs #1849) nirgends mehr; hier gab es ihn ohnehin nie — ein bewusst nicht
gesetzter ``system_type`` durfte sich nie ueber die Namenskonvention Zugang
verschaffen (Refs #1785). Die ``data_json``-Lesart folgt exakt
``_collect_active_bans`` (``core/services/system/bans.py``) — inklusive
``safe_decrypt`` (die Felder koennen feldverschluesselt sein) und der
Behandlung eines defekt gespeicherten ``bis``-Datums als „kein Ablauf".

**Bewusste Abweichung von ``get_active_bans``.** Jene Funktion filtert
zusaetzlich auf ``DocumentType.is_active`` — sie steuert die **Durchsetzung**
(wer gilt heute als gesperrt) und folgt deshalb der aktuellen Konfiguration.
Dieser Bericht beschreibt, was **passiert ist**; ein heute umgelegter
Konfigurationsschalter darf einen vergangenen Zeitraum nicht umschreiben. Die
Konfiguration steuert stattdessen die **Sichtbarkeit** des Berichts
(:attr:`BanOverviewBackend.verfuegbarkeit`).

**K-Anonymitaet.** Alle drei Zahlen fallen unter der Schwelle auf ``None``, ueber
die zentrale :func:`_suppress_small` auf den ``records``-Zeilen; ``summary``
wird aus denselben suppressierten Zeilen neu gebaut (genau eine
Entscheidungsstelle). Eine Randsumme gibt es nicht: keine der drei Zahlen ist
die Summe der uebrigen, und keine wird als Aufschluesselung einer anderen
publiziert (``aktiv_aktuell`` zaehlt ueber alle Zeiten,
``abgelaufen_im_zeitraum`` ueber ein anderes Datumsfeld als ``ausgesprochen``).
Die Aufschluesselung wird deshalb mit ``margin_key=None`` deklariert, und
:func:`apply_residual_guard` laeuft trotzdem — als Pruefung, nicht als
Behauptung.

**K4a — die Stichtagszahl ist ein Differenzkanal ueber den Erstellungszeitpunkt**
(Refs #1754, finale Fix-Welle). ``aktiv_aktuell`` haengt am Tag der Erstellung,
nicht am Berichtszeitraum. Zwei Herausgaben **desselben** Zeitraums zu
verschiedenen Zeitpunkten unterscheiden sich deshalb genau in dieser einen Zahl,
und ihre Differenz ist die Netto-Veraenderung der geltenden Hausverbote zwischen
den beiden Tagen — eine Groesse, die in keinem der beiden Artefakte steht und
durch keine Suppressionsregel laeuft. Bei taeglichen Abrufen wird daraus eine
Zeitreihe mit Aufloesung 1, obwohl jede einzelne Zahl die k-Schwelle passiert
hat. Dasselbe gilt fuer ``offene_faelle_aktuell`` in
:mod:`~core.reporting.backends.case_metrics`.

Einordnung wie K4a (Refs #1770): **akzeptierte Definitionsgrenze, kein offener
Befund.** Die Zusicherung gilt je Artefakt und je Zeitraum; hier ist der zweite
Bezugspunkt gar kein Zeitraum, sondern ein Zeitpunkt. Artefaktlokal ist nichts
auszurichten — es gibt keine Zelle, an der eine Regel ansetzen koennte, und ein
Guard muesste wissen, welche frueheren Ausgaben desselben Berichts an denselben
Empfaenger gegangen sind. Die Kennzahl bleibt (Maintainer-Entscheid 2026-08-05:
„so viele gelten heute" ist die Frage, die die Traeger stellen); flankierend
macht die **Freigabe-Uebersicht** (:class:`core.views.statistics.ReportReleaseLogView`,
:mod:`core.services.dashboard.release_log`) wiederholte Herausgaben desselben
Zeitraums sichtbar, und :data:`_POINT_IN_TIME_ISSUE` sagt der Empfaengerin im
Artefakt selbst, dass die Zahl am Erstellungstag haengt.

Die drei Zeilen werden **datenunabhaengig** emittiert, auch bei Wert 0: waere
ihre Existenz an Daten geknuepft, verriete die blosse Gestalt der Tabelle, dass
es Hausverbote gibt (K6-Lehre, Refs #1769) — bei diesem Thema besonders heikel.

``snapshot_enabled = False``: Der Payload darf nicht in
``StatisticsSnapshot.backend_data`` landen. Das ist hier keine Formalie — die
Spalte ist im PII-Residue-Sweep pauschal als non-PII klassifiziert, und ein
Hausverbots-Aggregat einer kleinen Einrichtung ist genau die Art Zahl, die man
dort nicht ungeprueft ablegen will.
"""

import dataclasses
import logging
from datetime import date

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import (
    Anforderung,
    BindungsEintrag,
    CompletenessIssue,
    DatasetBackend,
    DatasetResult,
    FieldSpec,
    TypBindung,
    project_records,
    stamp_threshold,
)
from core.reporting.data_source import BANS_IN_PERIOD
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _get_threshold,
    _suppress_small,
    apply_residual_guard,
)
from core.services.file_vault import safe_decrypt

logger = logging.getLogger(__name__)

# Die Typ-Bindung dieses Berichts (E1, Refs #1824). Zwei Deklarationsfelder
# tragen hier je eine bewusste Entscheidung, die frueher nur im Fliesstext
# stand:
#
# ``bindet="system_type"`` — dieser Bericht ist ein **Verhaltens**-Bericht und
# bleibt dauerhaft an ``system_type=ban`` gebunden; er ist mit E3 NICHT auf
# ``report_key`` gewechselt (Konzept #1847 §3). Die frueher noetige zweite
# Deklaration ``slug_fallback=False`` ist entfallen: seit E3 gibt es den
# Slug-Zweitschluessel ueberhaupt nicht mehr (Entscheid E-E), und damit auch
# nichts mehr abzuschalten.
TYP_BINDUNG = TypBindung(
    (BindungsEintrag("ban", _("Hausverbot")),),
    bindet="system_type",
)

# Kanonische, datenunabhaengige Zeilenreihenfolge. Die Bezeichner sind
# sprachunabhaengiger Klartext (Record-Wert und ``summary``-Schluessel in einem);
# uebersetzt werden die Spaltenkoepfe (``FieldSpec.label``, Refs #1744).
_KENNZAHLEN = ("ausgesprochen", "aktiv_aktuell", "abgelaufen_im_zeitraum")

_COUNT_KEY = "wert"

# Momentaufnahme-Kennzeichnung im **Artefakt** (Refs #1754, Review I1). Ohne sie
# liest die Empfaengerin ``aktiv_aktuell`` als dritte Zahl desselben Zeitraums —
# bei diesem Thema keine Kleinigkeit: „so viele Hausverbote im ersten Halbjahr"
# statt „so viele gelten heute" ist eine andere Aussage ueber eine Einrichtung.
#
# Datenunabhaengig und ziffernfrei (K6-Auflage, Refs #1769), Severity ``info``:
# kein Mangel, sondern eine Lesehilfe. ``code`` ist sprachunabhaengiger Klartext.
_POINT_IN_TIME_CODE = "point_in_time_active_bans"
_POINT_IN_TIME_ISSUE = CompletenessIssue(
    _POINT_IN_TIME_CODE,
    _(
        "Die Zahl der aktuell geltenden Hausverbote ist eine Momentaufnahme zum Zeitpunkt der Erstellung "
        "und bezieht sich nicht auf den Berichtszeitraum"
    ),
    "info",
    count=None,
)


def _ban_events(facility):
    """Sichtbare Hausverbots-Ereignisse der Einrichtung.

    Bewusst **ohne** ``is_active``: der Bericht beschreibt, was passiert ist;
    die Konfigurationsfrage steckt in :attr:`BanOverviewBackend.verfuegbarkeit`.
    """
    return (
        Event.objects.for_facility(facility).filter(is_deleted=False).filter(TYP_BINDUNG.matching_q("document_type__"))
    )


def _bis_datum(data: dict) -> date | None:
    """Das ``bis``-Datum eines Hausverbots — ``None`` = kein Ablauf.

    Lesart wie ``_collect_active_bans``: ``safe_decrypt`` (das Feld kann
    feldverschluesselt sein), und ein defekt gespeichertes Datum gilt als „kein
    Ablauf", wird aber fuer die Diagnose protokolliert (Refs #1011). Raten waere
    hier die schlechtere Richtung — ein unlesbares Ablaufdatum darf ein
    Hausverbot nicht still beenden.
    """
    bis_raw = safe_decrypt(data.get("bis", ""), default="")
    if not bis_raw:
        return None
    try:
        return date.fromisoformat(str(bis_raw))
    except (ValueError, TypeError):
        logger.debug("Ungueltiges Ban-'bis'-Datum im Bericht ignoriert: %r", bis_raw)
        return None


@register_backend("ban-overview")
class BanOverviewBackend(DatasetBackend):
    """Ausgesprochene, aktuell geltende und im Zeitraum abgelaufene Hausverbote."""

    name = _("Beispiel: Hausverbots-Übersicht")
    disclosure_dimensions = frozenset({"event_counts"})
    description = _("Zählt die im Zeitraum ausgesprochenen und abgelaufenen Hausverbote sowie den Bestand am Stichtag.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770): gezaehlt werden Hausverbots-Ereignisse, nicht Personen
    # (eine Person kann mehrere Hausverbote haben) — und ausdruecklich **nicht**
    # alle Ereignisse. Der frueher deklarierte ``ALL_EVENTS``-Code war sachlich
    # falsch (Refs #1754, Review I1): er nennt eine Grundgesamtheit, die um
    # Groessenordnungen weiter ist als die tatsaechliche, und zwei der drei
    # Zahlen werten ``occurred_at`` ueberhaupt nicht aus (Stichtag bzw.
    # Ablaufdatum). ``BANS_IN_PERIOD`` sagt beides.
    population = BANS_IN_PERIOD
    typ_bindung = TYP_BINDUNG
    # Waehlbar, sobald die Einrichtung einen **aktiven** Dokumentationstyp mit
    # ``system_type="ban"`` fuehrt (Seed-/Paket-Bindung, Maintainer-Entscheid
    # 2026-08-05). Ohne einen solchen Typ kann sie gar keine Hausverbote
    # dokumentieren, und ein Bericht ueber ein Thema, das es dort nicht gibt,
    # gehoert nicht ins Dropdown — bei diesem Thema auch aus Gruenden der
    # Erwartungshaltung, nicht nur der Ergonomie.
    #
    # ``is_active`` ist hier die **Konfigurationsfrage** ("bietet die Einrichtung
    # das an?") und darf nicht mit dem Datenfilter verwechselt werden:
    # ``build_dataset`` zaehlt bewusst auch Hausverbote unter einem inzwischen
    # deaktivierten Typ, damit ein Konfigurationsschalter keine Vergangenheit
    # umschreibt. Dieselbe Deklaration wie bei den Daten (Refs #1785).
    verfuegbarkeit = (Anforderung(TYP_BINDUNG),)

    # Display metadata only; export keys and records retain their contract (Refs #2602).
    metric_labels = {
        "ausgesprochen": _("Ausgesprochene Hausverbote"),
        "aktiv_aktuell": _("Aktuell aktive Hausverbote"),
        "abgelaufen_im_zeitraum": _("Im Zeitraum abgelaufene Hausverbote"),
    }

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("kennzahl", _("Kennzahl")),
            FieldSpec(_COUNT_KEY, _("Wert")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Drei Gesamtzahlen; ``records`` ist die Langform desselben Aggregats.

        ``ausgesprochen`` zaehlt in der Datenbank. ``aktiv_aktuell`` und
        ``abgelaufen_im_zeitraum`` haengen an ``data_json``-Werten, die
        feldverschluesselt sein koennen — sie brauchen deshalb eine
        Python-Schleife ueber ``safe_decrypt`` statt eines SQL-Praedikats. Beide
        werden in **einem** Durchlauf gezaehlt; die Menge der
        Hausverbots-Ereignisse einer Einrichtung ist klein.
        """
        events = _ban_events(facility)

        ausgesprochen = events.filter(
            occurred_at__date__gte=date_from,
            occurred_at__date__lte=date_to,
        ).count()

        heute = timezone.localdate()
        aktiv_aktuell = 0
        abgelaufen_im_zeitraum = 0
        # ``iterator()`` statt Voll-Materialisierung (Refs #1754): Die Schleife
        # braucht **alle** Hausverbots-Ereignisse — ``aktiv_aktuell`` kennt
        # keinen Zeitraum (ein 2019 ausgesprochenes Hausverbot kann heute
        # gelten), und ``bis`` liegt in ``data_json`` und ist womoeglich
        # feldverschluesselt, laesst sich also nicht als SQL-Praedikat
        # ausdruecken. Ein Zeitraum- oder Schluesselfilter waere hier deshalb
        # kein Optimierungsschritt, sondern eine Semantikaenderung. Was bleibt,
        # ist die Speichergrenze: der Server-Cursor haelt immer nur einen Block
        # im RAM, statt die Payloads aller Ereignisse auf einmal aufzubauen.
        # ``values_list("data_json")`` laedt bereits nur diese eine Spalte.
        for data_json in events.values_list("data_json", flat=True).iterator(chunk_size=1000):
            data = data_json or {}
            bis = _bis_datum(data)
            # Truthiness wie in ``_collect_active_bans``: ein entschluesselter
            # Wert kommt als String zurueck, die Pruefung bleibt bewusst
            # dieselbe wie im Durchsetzungspfad — zwei Lesarten desselben Feldes
            # waeren ein Widerspruch zwischen Bericht und Oberflaeche.
            if safe_decrypt(data.get("aktiv", False), default=False) and (bis is None or bis >= heute):
                aktiv_aktuell += 1
            if bis is not None and date_from <= bis <= date_to:
                abgelaufen_im_zeitraum += 1

        werte = {
            "ausgesprochen": ausgesprochen,
            "aktiv_aktuell": aktiv_aktuell,
            "abgelaufen_im_zeitraum": abgelaufen_im_zeitraum,
        }

        return DatasetResult(
            mode="aggregate",
            records=[{"kennzahl": key, _COUNT_KEY: werte[key]} for key in _KENNZAHLEN],
            summary=dict(werte),
            completeness=self.validate_completeness(facility, date_from, date_to),
        )

    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]:
        """Kennzeichnet die Stichtagszahl — datenunabhaengig, ohne Datenbankzugriff.

        Der Hinweis haengt an der Konstruktion des Berichts, nicht an seinen
        Daten: ``aktiv_aktuell`` ist immer eine Momentaufnahme. Keine Abfrage,
        keine Verzweigung — beides waere ein datenabhaengiger Kanal ueber die
        blosse Existenz des Hinweises (K6-Lehre, Refs #1769), und hier verriete
        er, ob es ueberhaupt Hausverbote gibt.
        """
        return [_POINT_IN_TIME_ISSUE]

    # Deklaration fuer den zentralen Residual-Guard (K1, Refs #1770).
    # ``margin_key=None``: keine der drei Zahlen partitioniert eine andere,
    # es gibt also keine Gleichung zum Rueckrechnen. Der Guard laeuft trotzdem
    # und bestaetigt das — Pruefung statt Behauptung.
    #
    # Es haengt zusaetzlich an den datenunabhaengigen Nullzeilen (Refs #1754,
    # ausfuehrlich am selben Punkt in ``case_metrics``): das Inferenzmodell des
    # Guards setzt fuer Zeilen einer Aufschluesselung ``lo = 1`` an, eine
    # Nullzeile macht es unerfuellbar, und ein leeres Intervall gilt dort als
    # begruendete Ausnahme — mit deklarierter Randsumme liefe der Guard also
    # fail-open. Wer hier eine Randsumme nachruestet, muss die Nullzeilen
    # zuerst loswerden — und damit die K6-Zusage aufgeben, dass die Gestalt der
    # Tabelle nichts ueber die Existenz von Hausverboten verraet.
    _EXPORT_BREAKDOWN = _Breakdown("records", None, ORDER_INPUT, count_key=_COUNT_KEY)

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel-Vorschau (Refs #1278/#1770).

        Alle drei Zahlen laufen als Zeilen durch :func:`_suppress_small` und
        werden unterhalb der Schwelle ``None``; ``margin=None`` ist die
        wahrheitsgemaesse Angabe (keine publizierte Randsumme). ``summary``
        entsteht aus **denselben** suppressierten Zeilen — eine zweite
        Suppressionsstelle gibt es nicht. Danach die Rueckprojektion auf die
        FieldSpec-Keys; das interne ``suppressed``-Flag ist keine Spalte.

        Der Momentaufnahme-Hinweis wird aus dem **festen** Code gebaut, nicht aus
        ``result.completeness`` abgeleitet (K6-Muster, Refs #1769) — bei diesem
        Thema besonders wichtig: ein kuenftiger, bezifferter Hinweis erreichte
        den Export sonst ungeprueft.

        Erwartet das **rohe** ``build_dataset``-Ergebnis (nicht idempotent).
        """
        threshold = _get_threshold(facility)

        view = {
            "records": _suppress_small(result.records, threshold, count_key=_COUNT_KEY, margin=None, order=ORDER_INPUT),
        }
        apply_residual_guard(view, threshold, (self._EXPORT_BREAKDOWN,))

        records = project_records(view.get("records", []), self.get_field_specs())

        summary = dict(result.summary)
        # Fail-safe-Reihenfolge wie in ``case_metrics``: erst alles auf ``None``,
        # dann die suppressierten Zeilen einspielen. Faellt eine Zeile weg (die
        # Eskalationsleiter des Guards kann eine Aufschluesselung ganz
        # entfernen), bleibt ``None`` stehen statt des Rohwerts aus
        # ``build_dataset``.
        summary.update(dict.fromkeys(_KENNZAHLEN, None))
        summary.update({row["kennzahl"]: row[_COUNT_KEY] for row in records})
        summary = stamp_threshold(summary, threshold)
        return dataclasses.replace(result, records=records, summary=summary, completeness=[_POINT_IN_TIME_ISSUE])
