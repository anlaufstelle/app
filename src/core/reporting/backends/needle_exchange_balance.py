"""Beispiel-Backend „Spritzentausch-Bilanz" — erstes Backend ueber FELDWERTEN (Refs #1754).

Alle bisherigen Backends zaehlen **Ereignisse** (``Count("id")``); dieses hier
liest zum ersten Mal die in ``Event.data_json`` gespeicherten **Feldwerte** aus
— die Kernluecke aus #1754.
Es ist deshalb bewusst als Referenz gebaut: wer ein eigenes Backend ueber
Feldwerte schreibt, kopiert dieses Modul.

**Was der Bericht zeigt.** Ausgegebene und zurueckgenommene Stueckzahlen im
Zeitraum sowie ihre Differenz (``saldo``) — ueber alle Spritzentausch-Ereignisse
der Einrichtung.

**Feld-Adressierung.** Ueber den ``report_key`` der Feldvorlage
(``ausgabe_stueck``/``rueckgabe_stueck``); gelesen wird der Wert unter dem
**Slug** der so gebundenen Vorlage, je Dokumentationstyp aufgeloest
(:meth:`~core.reporting.base.FeldBindung.leseschluessel`). Im Standardpaket
sind das die Slugs ``ausgabe`` und ``rueckgabe``; eine Einrichtung mit eigener
Feldbenennung ist seit E3 (Refs #1849) nicht mehr ausgeschlossen.

**Typaufloesung — ein Schluessel.** Exakt das Muster aus
:func:`core.reporting.backends.sample_report_1.resolve_category`: der
``report_key`` des Dokumentationstyps entscheidet, und zwar allein. Der frueher
noetige Slug-Zweitschluessel (Refs #1741) ist mit E3 entfallen (Refs #1849) —
mit ihm die Falle, dass ein Notiz-Typ mit dem Slug ``spritzentausch`` seine
Ereignisse in den Bericht zieht (Refs #1785).

**Feldwerte defensiv lesen.** ``data_json`` ist ein freies JSONB: es kann Werte
aus Alt-Formularen, aus dem Offline-Bundle oder aus einer inzwischen geloeschten
Feldvorlage enthalten. Gelesen wird deshalb IMMER ueber
``safe_decrypt(data.get("<slug>"), default=None)`` (verschluesselte Felder geben
einen **String** zurueck!), und die Koersion nach Zahl faellt bei allem, was
keine Zahl ist, auf „kein Wert" zurueck statt zu crashen. Der Ausschuss wird
gezaehlt und als :class:`~core.reporting.base.CompletenessIssue` gemeldet — ein
Bericht, der Luecken verschweigt, waere irrefuehrend vollstaendig.

**K-Anonymitaet fuer Summen (Maintainer-Entscheid 2026-08-05, Refs #1754).**
Eine Summe ueber Feldwerte ist **keine Personenzaehlung** und faellt deshalb
nicht unter dieselbe Regel wie eine Zellzahl. Sie unterliegt trotzdem der
Schwelle, nur mit anderer Bezugsgroesse:

* Eine Summe wird unterdrueckt, wenn die Zahl der **beitragenden Events** unter
  *k* liegt — bei kleiner Fallzahl ist aus der Summe der Einzelwert weniger
  Personen rekonstruierbar (im Grenzfall *ein* Event: die Summe IST der
  Einzelwert).
* Die **Event-Anzahl selbst** wird ebenfalls erst ab *k* publiziert (R14).
* Der ``saldo`` ist aus beiden Summen ableitbar (``ausgabe − rueckgabe``) und
  faellt deshalb **immer** mit ihnen — er ist kein eigener Publikationskanal.
* ``events_ohne_werte`` und die beitragenden Events partitionieren ``events``
  exakt. Eine publizierte Kleinstzelle waere aus der Randsumme direkt
  rueckrechenbar, deshalb faellt ``events_ohne_werte``, sobald eine der beiden
  Zellen unter *k* liegt.

Da ``summe_ausgabe``/``summe_rueckgabe`` nur ab *k* beitragenden Events stehen
und ``beitragende <= events`` gilt, impliziert die Summen-Bedingung die
Event-Bedingung — die Reihenfolge der Pruefungen ist also nicht zufaellig.

**Grenze.** Feldwerte lassen sich nicht in SQL aggregieren, ohne die
Verschluesselung zu umgehen; dieses Backend iteriert deshalb ueber die
``data_json``-Spalte des Zeitraums (eine Query, aber Python-seitige Schleife).
Fuer Jahreszeitraeume grosser Einrichtungen ist das die dokumentierte Grenze
(docs/reporting-plugins.md § 9), nicht ein Versehen.
"""

import dataclasses

from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import (
    Anforderung,
    BindungsEintrag,
    CompletenessIssue,
    DatasetBackend,
    DatasetResult,
    FeldBindung,
    FieldSpec,
    TypBindung,
    doppelbindungs_issues,
)
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.registry import register_backend
from core.reporting.values import as_number
from core.services.dashboard.external_report import _get_threshold
from core.services.file_vault import safe_decrypt

# Sprachunabhaengiger Code (Vertrag: ``CompletenessIssue.code`` ist Klartext,
# nie uebersetzt) und die eine, ziffernfreie Meldung dazu. Die Ziffernfreiheit
# gilt je ``code`` und muss in JEDER Sprache halten — eine Uebersetzung darf
# weder eine Zahl noch eine Bezugsgroesse ergaenzen (Refs #1744/#1769).
MISSING_VALUES_CODE = "missing_field_values"
_MISSING_VALUES_MESSAGE = _(
    "Ereignisse ohne verwertbare Zahlenwerte in „Ausgabe“/„Rückgabe“ sind in den Summen nicht enthalten"
)

# Die Typ-Bindung dieses Berichts (E1, Refs #1824): der Berichtsschluessel
# ``needle_exchange``. Einen Slug-Zweitschluessel gibt es seit E3 nicht mehr
# (Entscheid E-E, Refs #1849) — der Bestand kam per Backfill herueber.
TYP_BINDUNG = TypBindung((BindungsEintrag("needle_exchange", _("Spritzentausch")),))

# Die beiden Feld-Bindungen — **getrennt**, nicht als eine Bindung mit zwei
# Slugs: nur so heisst die Verfuegbarkeits-Pruefung „beide Vorlagen am
# DEMSELBEN Typ" (je ``filter()``-Aufruf ein eigener JOIN, siehe
# ``FeldBindung.template_q``). Eine Bindung mit ``slugs=("ausgabe",
# "rueckgabe")`` waere ein ``IN`` und damit ein ODER.
FELD_AUSGABE = FeldBindung(
    (BindungsEintrag("ausgabe_stueck", _("Ausgabe gesamt"), slugs=("ausgabe",)),), bindet="report_key"
)
FELD_RUECKGABE = FeldBindung(
    (BindungsEintrag("rueckgabe_stueck", _("Rückgabe gesamt"), slugs=("rueckgabe",)),), bindet="report_key"
)

# SQL-Entsprechung der Typaufloesung — ``matching_q()`` filtert auf
# ``DocumentType`` selbst, ``matching_q("document_type__")`` auf ``Event``. EINE
# Definition fuer beide Aufrufstellen: liefen Ereignis-Filter und
# Verfuegbarkeits-Pruefung auseinander, waere das Backend fuer eine Einrichtung
# waehlbar, fuer die es nie eine Zeile liefert (oder umgekehrt).
matching_q = TYP_BINDUNG.matching_q


def _feldwert(werte: dict, slug: str | None):
    """Zahlwert unter ``slug`` — ``None``, wenn der Typ kein solches Feld traegt.

    ``slug is None`` heisst: dieser Dokumentationstyp hat fuer die Bindung keine
    (eindeutige) Feldvorlage. Der Fall ist nach E3 regulaer — ein Typ kann
    ``needle_exchange`` tragen und trotzdem nur eines der beiden Zahlenfelder
    fuehren, und bei einer Doppelbindung zaehlt bewusst keines (E-J).
    """
    if not slug:
        return None
    return as_number(safe_decrypt(werte.get(slug), default=None))


@register_backend("needle-exchange-balance")
class NeedleExchangeBalanceBackend(DatasetBackend):
    """Ausgabe/Ruecknahme und Saldo je Zeitraum — Referenz fuer Feldwert-Berichte."""

    name = _("Beispiel: Spritzentausch-Bilanz")
    disclosure_dimensions = frozenset({"event_counts"})
    description = _("Summiert Ausgabe und Rückgabe des Spritzentauschs im Zeitraum und bildet den Saldo.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770/#1754): ``events`` zaehlt NICHT alle Ereignisse des
    # Zeitraums, sondern nur die der Spritzentausch-Typen. Der Default
    # ``ALL_EVENTS`` waere hier eine falsche Angabe und laedt zur Differenz mit
    # jeder anderen Tabelle ein — ``base.py`` verlangt deshalb, dass ein enger
    # zaehlendes Backend seine Grundgesamtheit explizit ueberschreibt.
    population = TYPED_EVENTS
    typ_bindung = TYP_BINDUNG
    feld_bindungen = (FELD_AUSGABE, FELD_RUECKGABE)
    # Nur waehlbar, wenn BEIDE Feldvorlagen an einem passenden **aktiven** Typ
    # haengen. Ohne beide Felder waere der ``saldo`` eine Differenz gegen eine
    # konstante Null — ein Bericht, der aussieht, als haette die Einrichtung nie
    # etwas zurueckgenommen. Lieber gar nicht anbieten. Die zwei Bindungen
    # werden in ZWEI ``filter()``-Aufrufe uebersetzt (``Anforderung.erfuellt``),
    # damit „an DEMSELBEN Typ" geprueft wird.
    verfuegbarkeit = (Anforderung(TYP_BINDUNG, (FELD_AUSGABE, FELD_RUECKGABE)),)

    def get_field_specs(self) -> list[FieldSpec]:
        """Eine Aggregatzeile; die Spalten sind die vier Kennzahlen."""
        return [
            FieldSpec("events", _("Ereignisse")),
            FieldSpec("summe_ausgabe", _("Ausgabe gesamt")),
            FieldSpec("summe_rueckgabe", _("Rückgabe gesamt")),
            FieldSpec("saldo", _("Saldo")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Summiert ``ausgabe``/``rueckgabe`` ueber die Spritzentausch-Ereignisse."""
        # Lesepfad je Dokumentationstyp (E3, Refs #1849): gebunden wird ueber den
        # Berichtsschluessel, gelesen wird unter dem Feldvorlagen-Slug.
        lese_ausgabe = FELD_AUSGABE.leseschluessel(facility, TYP_BINDUNG)
        lese_rueckgabe = FELD_RUECKGABE.leseschluessel(facility, TYP_BINDUNG)
        payloads = (
            Event.objects.for_facility(facility)
            .filter(
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            .filter(matching_q("document_type__"))
            .values_list("document_type_id", "data_json")
        )

        events = 0
        summe_ausgabe: float = 0
        summe_rueckgabe: float = 0
        ohne_werte = 0
        for document_type_id, data in payloads:
            events += 1
            werte = data or {}
            ausgabe = _feldwert(werte, lese_ausgabe.je_typ.get(document_type_id))
            rueckgabe = _feldwert(werte, lese_rueckgabe.je_typ.get(document_type_id))
            if ausgabe is None and rueckgabe is None:
                # Beide Werte unbrauchbar: das Ereignis traegt zu KEINER Summe
                # bei und zaehlt deshalb nicht als beitragend. Ein Ereignis mit
                # nur einem Wert traegt dagegen bei (die fehlende Haelfte ist
                # fachlich eine 0 — es wurde nichts zurueckgenommen).
                ohne_werte += 1
                continue
            summe_ausgabe += ausgabe or 0
            summe_rueckgabe += rueckgabe or 0

        summary = {
            "events": events,
            "summe_ausgabe": summe_ausgabe,
            "summe_rueckgabe": summe_rueckgabe,
            "saldo": summe_ausgabe - summe_rueckgabe,
            "events_ohne_werte": ohne_werte,
        }
        return DatasetResult(
            disclosure_context=(date_from, date_to, "live"),
            mode="aggregate",
            # Die Aggregatzeile wird IMMER emittiert, auch fuer einen leeren
            # Zeitraum: haenge ihre Existenz am Bestand, waere schon ihr
            # Vorhandensein die Aussage „es gab Ereignisse" (dieselbe
            # Begruendung wie bei der K3-A1-Restzeile, Refs #1770).
            records=[{spec.key: summary[spec.key] for spec in self.get_field_specs()}],
            summary=summary,
            # Bezifferte INTERNE Fassung, aus dem bereits gebauten Aggregat
            # statt aus einer zweiten Query (zwei Zaehlwege driften garantiert).
            # ``prepare_export`` ersetzt sie durch die ziffernfreie Export-
            # Fassung — nur diese verlaesst je das Haus (K6, Refs #1769).
            completeness=(
                [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=ohne_werte)]
                if ohne_werte
                else []
            )
            + doppelbindungs_issues(lese_ausgabe, lese_rueckgabe),
        )

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """Publish the local contract through the shared population mask."""
        from core.services.dashboard.external_report import _protect_needle_dataset
        from core.services.dashboard.shared_disclosure import project_shared_dataset

        local = _protect_needle_dataset(result, _get_threshold(facility), field_specs=self.get_field_specs())
        local = dataclasses.replace(
            local,
            completeness=[
                CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=None),
                *doppelbindungs_issues(FELD_AUSGABE.leseschluessel(facility), FELD_RUECKGABE.leseschluessel(facility)),
            ],
        )
        return project_shared_dataset(facility, local, "needle-exchange-balance")
