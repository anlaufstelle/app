"""Beispiel-Backend „Spritzentausch-Bilanz" — erstes Backend ueber FELDWERTEN (Refs #1754).

Alle bisherigen Backends zaehlen **Ereignisse** (``Count("id")``); dieses hier
liest zum ersten Mal die in ``Event.data_json`` gespeicherten **Feldwerte** aus
— die Kernluecke aus #1754.
Es ist deshalb bewusst als Referenz gebaut: wer ein eigenes Backend ueber
Feldwerte schreibt, kopiert dieses Modul.

**Was der Bericht zeigt.** Ausgegebene und zurueckgenommene Stueckzahlen im
Zeitraum sowie ihre Differenz (``saldo``) — ueber alle Spritzentausch-Ereignisse
der Einrichtung.

**Feld-Adressierung.** Ausschliesslich ueber die Feld-Template-Slugs des
Standardpakets (``core/packages/default.yaml``): ``ausgabe`` und ``rueckgabe``.
Slugs sind je Einrichtung eindeutig und nach Erstellung unveraenderlich
(M2, ADR-037) — dieselbe Stabilitaetszusage wie bei ``system_type``.

**Typaufloesung — ``system_type`` zuerst, Slug nur als Zweitschluessel.**
Exakt das Muster aus :func:`core.reporting.backends.sample_report_1.resolve_category`
(Refs #1741/#1785): ein GESETZTER ``system_type`` ist eine vollstaendige Aussage
und entscheidet allein — auch wenn er ``note``/``ban`` lautet und damit „gehoert
nicht in diesen Bericht" bedeutet. Der Paket-Slug ``spritzentausch`` greift NUR,
wenn ``system_type`` leer/NULL ist; sonst zoege ein Notiz-Typ mit dem Slug
``spritzentausch`` seine Ereignisse in den Bericht.

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

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import _get_threshold, apply_residual_guard
from core.services.file_vault import safe_decrypt

# Feld-Template-Slugs des Standardpakets (``core/packages/default.yaml``).
AUSGABE_SLUG = "ausgabe"
RUECKGABE_SLUG = "rueckgabe"

# Sprachunabhaengiger Code (Vertrag: ``CompletenessIssue.code`` ist Klartext,
# nie uebersetzt) und die eine, ziffernfreie Meldung dazu. Die Ziffernfreiheit
# gilt je ``code`` und muss in JEDER Sprache halten — eine Uebersetzung darf
# weder eine Zahl noch eine Bezugsgroesse ergaenzen (Refs #1744/#1769).
MISSING_VALUES_CODE = "missing_field_values"
_MISSING_VALUES_MESSAGE = _(
    "Ereignisse ohne verwertbare Zahlenwerte in „Ausgabe“/„Rückgabe“ sind in den Summen nicht enthalten"
)

# Erstschluessel der Typaufloesung.
SYSTEM_TYPE = DocumentType.SystemType.NEEDLE_EXCHANGE
# Zweitschluessel: der Paket-Slug — nur bei leerem ``system_type`` (siehe
# Modul-Docstring). Bewusst genau EIN Slug: der Zweitschluessel darf keinen Typ
# hereinholen, den der Erstschluessel bewusst auslaesst.
PAKET_SLUG = "spritzentausch"


def matching_q(prefix: str = "") -> Q:
    """SQL-Entsprechung der Typaufloesung — ``prefix`` fuer JOIN-Pfade.

    ``matching_q()`` filtert auf :class:`~core.models.DocumentType` selbst,
    ``matching_q("document_type__")`` auf :class:`~core.models.Event`. EINE
    Definition fuer beide Aufrufstellen: liefen Ereignis-Filter und
    Verfuegbarkeits-Pruefung auseinander, waere das Backend fuer eine Einrichtung
    waehlbar, fuer die es nie eine Zeile liefert (oder umgekehrt).

    Der ``isnull``-ODER-Zweig ist bewusst redundant (Muster ``_UNMAPPED_Q`` in
    ``sample_report_1``): ``system_type`` ist NULL-bar, und blankes SQL-``NOT IN``
    faengt NULL-Zeilen nicht.
    """
    unset = Q(**{f"{prefix}system_type__isnull": True}) | Q(**{f"{prefix}system_type": ""})
    return Q(**{f"{prefix}system_type": SYSTEM_TYPE}) | (unset & Q(**{f"{prefix}slug": PAKET_SLUG}))


def as_number(raw):
    """Feldwert nach Zahl koerzieren — ``None`` heisst „kein verwertbarer Wert".

    Fail-safe statt fail-fast: ``data_json`` ist ein freies JSONB, in dem alles
    stehen kann (Alt-Formular, Offline-Bundle, geloeschte Feldvorlage). Ein
    ``ValueError`` an dieser Stelle wuerde den ganzen Bericht killen, obwohl ein
    einziges kaputtes Feld die Ursache ist — der Ausschuss wird stattdessen
    gezaehlt und gemeldet.

    ``bool`` wird ausdruecklich ausgeschlossen, obwohl es in Python ein ``int``
    ist: ein Ja/Nein-Feld, das versehentlich unter dem Zahl-Slug liegt, ergibt
    als Stueckzahl 1 bzw. 0 — eine still falsche Summe waere schlimmer als eine
    gemeldete Luecke.

    Strings kommen von verschluesselten Feldern (``safe_decrypt`` liefert immer
    ``str``) und aus Alt-Formularen. Nur ein sauber parsbarer Wert zaehlt;
    Tausenderpunkte, Dezimalkommas und Einheiten sind Ausschuss, weil jede
    Rate-Regel hier eine still falsche Zahl erzeugen koennte.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int | float):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                continue
    return None


@register_backend("needle-exchange-balance")
class NeedleExchangeBalanceBackend(DatasetBackend):
    """Ausgabe/Ruecknahme und Saldo je Zeitraum — Referenz fuer Feldwert-Berichte."""

    name = _("Beispiel: Spritzentausch-Bilanz")
    description = _("Summiert Ausgabe und Rückgabe des Spritzentauschs im Zeitraum und bildet den Saldo.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770/#1754): ``events`` zaehlt NICHT alle Ereignisse des
    # Zeitraums, sondern nur die der Spritzentausch-Typen. Der Default
    # ``ALL_EVENTS`` waere hier eine falsche Angabe und laedt zur Differenz mit
    # jeder anderen Tabelle ein — ``base.py`` verlangt deshalb, dass ein enger
    # zaehlendes Backend seine Grundgesamtheit explizit ueberschreibt.
    population = TYPED_EVENTS

    def get_field_specs(self) -> list[FieldSpec]:
        """Eine Aggregatzeile; die Spalten sind die vier Kennzahlen."""
        return [
            FieldSpec("events", _("Ereignisse")),
            FieldSpec("summe_ausgabe", _("Ausgabe gesamt")),
            FieldSpec("summe_rueckgabe", _("Rückgabe gesamt")),
            FieldSpec("saldo", _("Saldo")),
        ]

    def is_available(self, facility) -> bool:
        """Nur waehlbar, wenn BEIDE Feldvorlagen an einem passenden Typ haengen.

        Ohne beide Felder waere der ``saldo`` eine Differenz gegen eine
        konstante Null — ein Bericht, der aussieht, als haette die Einrichtung
        nie etwas zurueckgenommen. Lieber gar nicht anbieten.

        Die zwei ``filter``-Aufrufe sind bewusst getrennt: ueber eine
        Mehrfach-Relation erzeugt Django je Aufruf einen eigenen JOIN, sodass
        „beide Slugs an DEMSELBEN Typ" geprueft wird. In EINEM ``filter``
        muesste dieselbe Zeile beide Slugs zugleich tragen — nie erfuellbar.
        """
        return (
            DocumentType.objects.filter(facility=facility, is_active=True)
            .filter(matching_q())
            .filter(fields__field_template__slug=AUSGABE_SLUG, fields__field_template__is_active=True)
            .filter(fields__field_template__slug=RUECKGABE_SLUG, fields__field_template__is_active=True)
            .exists()
        )

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Summiert ``ausgabe``/``rueckgabe`` ueber die Spritzentausch-Ereignisse."""
        payloads = (
            Event.objects.filter(
                facility=facility,
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            .filter(matching_q("document_type__"))
            .values_list("data_json", flat=True)
        )

        events = 0
        summe_ausgabe: float = 0
        summe_rueckgabe: float = 0
        ohne_werte = 0
        for data in payloads:
            events += 1
            werte = data or {}
            ausgabe = as_number(safe_decrypt(werte.get(AUSGABE_SLUG), default=None))
            rueckgabe = as_number(safe_decrypt(werte.get(RUECKGABE_SLUG), default=None))
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
            ),
        )

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel — Begruendung im Modul-Docstring.

        ``apply_residual_guard`` laeuft als letzter Schritt, wie in jedem
        ``prepare_export`` mit publizierter Randsumme (Architektur-Invariante,
        ``test_architecture_guards_services.py``). Er bekommt hier bewusst eine
        **leere** Breakdown-Liste: dieses Backend publiziert gar keine
        Aufschluesselung, an der sich zurueckrechnen liesse — die einzige
        Identitaet zwischen seinen Kennzahlen ist ``saldo = ausgabe − rueckgabe``,
        und die drei fallen gemeinsam. Die zweite Identitaet
        (``events`` = beitragende + ``events_ohne_werte``) wird oberhalb des
        Guards aufgeloest, weil die beitragende Zelle gar nicht erst publiziert
        wird.
        """
        threshold = _get_threshold(facility)
        events = result.summary["events"]
        ohne_werte = result.summary["events_ohne_werte"]
        beitragende = events - ohne_werte

        # R14: eine Randsumme < k ist selbst eine Kleinstfallzahl.
        published_events = events if events >= threshold else None
        # Summen-Regel: Bezugsgroesse ist die Zahl der beitragenden Events.
        # ``beitragende <= events``, die Bedingung impliziert also R14.
        summen_sichtbar = beitragende >= threshold
        # Die Partition ``events`` = beitragende + ``events_ohne_werte``: eine
        # sichtbare Kleinstzelle waere aus der Randsumme rueckrechenbar, und die
        # jeweils andere Zelle folgt aus der Differenz.
        luecke_sichtbar = published_events is not None and min(beitragende, ohne_werte) >= threshold

        view = {
            "events": published_events,
            "summe_ausgabe": result.summary["summe_ausgabe"] if summen_sichtbar else None,
            "summe_rueckgabe": result.summary["summe_rueckgabe"] if summen_sichtbar else None,
            "saldo": result.summary["saldo"] if summen_sichtbar else None,
            "events_ohne_werte": result.summary["events_ohne_werte"] if luecke_sichtbar else None,
        }
        apply_residual_guard(view, threshold, ())

        summary = {**result.summary, **view, "k_anonymity_threshold": threshold}
        records = [{spec.key: view[spec.key] for spec in self.get_field_specs()}]
        # K6 (Refs #1769): datenunabhaengig und unbeziffert — die Meldung steht
        # immer, auch wenn es gar keine Luecke gibt. Waeren beide Faelle
        # unterscheidbar, verriete schon die Existenz der Meldung, dass der
        # Ausschuss > 0 ist, und die gerade unterdrueckte Zahl waere qualitativ
        # zurueck.
        completeness = [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=None)]
        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
