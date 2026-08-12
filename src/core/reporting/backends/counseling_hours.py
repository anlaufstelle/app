"""Beispiel-Backend „Beratungsstunden" — Zeilen-Tabelle ueber Feldwerten (Refs #1754).

Zweites Backend ueber ``data_json``-FELDWERTEN (siehe
:mod:`core.reporting.backends.needle_exchange_balance` fuer die
Grundmuster), aber das erste mit einer **Aufschluesselung**: je Kategorie eine
Zeile mit Ereigniszahl und aufsummierten Minuten aus der Paket-Feldvorlage
``dauer``.

**Kategorien.** Die drei Paket-Dokumentationstypen, an denen ``dauer`` haengt:
``kontakt`` (``contact``), ``beratungsgespraech`` (``counseling``) und
``krisengespraech`` (``crisis``). Die Aufloesung folgt dem
``resolve_category``-Muster: seit E3 (Refs #1849) entscheidet der
``report_key`` des Dokumentationstyps, und zwar allein.

Der Zeilenschluessel ``kategorie`` traegt den **``system_type``-Wert selbst**
(``"contact"``/``"counseling"``/``"crisis"``) und ist damit sprachunabhaengig
und stabil — Record-Schluessel UND -Werte einer Maschinenschnittstelle duerfen
sich nicht mit der Oberflaechensprache aendern (dieselbe Begruendung wie bei
``CATEGORY_MAP`` in ``sample_report_1``, nur konsequenter: dort ist es deutscher
Klartext aus Bestandsgruenden).

**Ereignisse ohne ``dauer``** (Entscheid, Brief D2): Sie zaehlen als Ereignis —
sie haben ja stattgefunden — tragen aber 0 Minuten bei und erscheinen als
:class:`~core.reporting.base.CompletenessIssue` ``missing_field_values``. Die
Alternative (gar nicht zaehlen) machte ``events`` zu einer stillen Teilmenge und
die Randsumme unbrauchbar; die gewaehlte Variante ist ehrlich, verlangt aber die
Zusatzbuchhaltung unten.

**K-Anonymitaet (Maintainer-Entscheid 2026-08-05, Refs #1754).** Eine Summe
ueber Feldwerte ist keine Personenzaehlung; ihre Bezugsgroesse ist die Zahl der
**beitragenden Events** — hier also die Ereignisse MIT verwertbarer ``dauer``,
nicht die Zeilen-Ereigniszahl. Deshalb fuehrt jede Zeile die interne Kennzahl
:data:`BEITRAGENDE_KEY` mit (reine Suppressions-**Eingabe**, exakt die Rolle von
``persons`` in ``sample_report_1``) und wird vor der Ausgabe wieder
weggeworfen. Daraus folgt die Kaskade in :meth:`CounselingHoursBackend.prepare_export`:

1. ``events`` je Zeile laeuft durch die zentrale Suppression
   (:func:`~core.services.dashboard.external_report._suppress_small`) gegen die
   publizierte Randsumme ``events_gesamt``; anschliessend entscheidet
   :func:`~core.services.dashboard.external_report.apply_residual_guard`.
2. ``minuten`` einer Zeile faellt, sobald ihre ``events``-Zelle unterdrueckt ist
   ODER weniger als *k* Ereignisse zur Summe beigetragen haben.
3. ``minuten_gesamt`` faellt, sobald **irgendeine** Zeilen-``minuten`` fehlt —
   sonst waere die fehlende Zahl exakt die Differenz zu den sichtbaren Zeilen.
   Es faellt ebenso, wenn ``events_gesamt`` selbst unterdrueckt ist oder der
   Guard die ganze Aufschluesselung hat fallen lassen.

**Grenze dieser Begruendung: sie gilt artefakt-LOKAL** (K4a, Refs #1770/#1754).
Alles oben rechnet innerhalb *dieses* Berichts. Zwischen Artefakten gibt es
Kanaele, die kein Backend allein schliessen kann, und dieser hier hat **zwei**
konkrete:

**(1) Differenz zur Auswahlfeld-Verteilung.** Die ``contact``-Zeile zaehlt
**alle** Kontakt-Ereignisse des Zeitraums, ``events_leistungen`` in
:mod:`core.reporting.backends.service_distribution` nur die **mit** Auswahlwert
(die Ereignismenge ist also nicht dieselbe, wohl aber die Obermenge — der
Berichtsschluessel ist derselbe).
Ihre **Differenz** ist die Zahl der Kontakte **ohne** Leistungs-Eintrag — eine
Groesse, die in keinem der beiden Berichte steht und deshalb durch keine
Suppressionsregel laeuft. Fuer ``crisis`` und ``events_art_der_krise`` gilt
dasselbe. Wer beide Berichte desselben Zeitraums herausgibt, publiziert damit
implizit eine dritte, ungeprueft kleine Zahl.

**(2) Reproduktion der Sachbericht-Kategorien — inklusive Aufhebung von dessen
Sekundaer-Suppression** (Refs #1754, finale Fix-Welle). Die Kategorien dieses
Berichts sind die Kategorien des Sachberichts
(:mod:`core.reporting.backends.sample_report_1`): dieselbe Aufloesung,
dieselben Schluessel, dieselbe Basismenge (sichtbare Ereignisse des Zeitraums je
Typ). ``events`` je Zeile ist damit **dieselbe Zaehlgroesse** wie die
Kategoriezahl dort, nur um die Minutenspalte verfeinert. Es ist also eine
Verfeinerung im Sinne der Auflage aus :mod:`core.reporting.backends.monthly_trend`
— und sie hat eine Folge, die ueber „mehr Detail" hinausgeht:

Die **Sekundaer**-Suppression des Sachberichts ist tabellenlokal konstruiert.
Faellt dort eine Kategorie nur deshalb, damit die primaer unterdrueckte
Nachbarzelle nicht als Differenz zur Randsumme rueckrechenbar wird, dann ist
ihre eigene Zahl >= *k* — und genau die steht hier im Klartext. Der Empfaenger
beider Artefakte kann die Sachbericht-Randsumme also wieder aufloesen, obwohl
dort korrekt unterdrueckt wurde. Der ``events``-Wert dieses Berichts hat seine
eigene k-Pruefung bestanden; es ist keine Kleinstfallzahl, die sichtbar wird,
sondern eine Suppressions**begruendung**, die ihre Wirkung verliert.

Beide Kanaele sind **keine offene Baustelle, sondern die akzeptierte
Definitionsgrenze** der k-Anonymitaets-Zusicherung: sie gilt je Artefakt und je
Zeitraum. Ein artefaktuebergreifender Guard muesste wissen, welche Berichte an
denselben Empfaenger gegangen sind — Wissen, das im Erzeugungspfad nicht
existiert. Flankierend gibt es die **Freigabe-Uebersicht**
(:class:`core.views.statistics.ReportReleaseLogView`, K4a): sie verhindert den
Kanal nicht, macht ueberlappende Herausgaben aber sichtbar. Die Konsequenz
gehoert damit zur organisatorischen Ebene ("nicht beliebig viele Zuschnitte an
denselben Empfaenger"), nicht in dieses Modul.

**Vollstaendigkeit dieser Aufzaehlung** ist eine Momentaufnahme, keine
Zusicherung: sie nennt die Kanaele, die beim Bau und in den Reviews zu #1754
gefunden wurden. Wer den Bericht erweitert, prueft die drei Fragen aus der
Nachahmer-Auflage (dieselben Maps? dieselbe Basismenge? dieselbe Zaehlgroesse?)
gegen **jedes** Bestandsartefakt neu.
"""

import dataclasses

from django.utils.translation import gettext_lazy as _

from core.models import Event

# Die Zahl-Koersion wird bewusst aus dem Referenz-Backend importiert statt
# kopiert: sie definiert, was „beitragendes Ereignis" heisst, und genau diese
# Zahl ist die Bezugsgroesse der K-Anon-Regel fuer Summen. Zwei Kopien
# driften — und eine gedriftete Koersion hiesse: zwei Backends unterdruecken
# nach verschiedenen Massstaeben. Wer dieses Modul als Vorlage kopiert, nimmt
# ``as_number`` mit oder importiert es weiter von hier.
from core.reporting.backends.needle_exchange_balance import as_number
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
    project_records,
    stamp_threshold,
)
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _get_threshold,
    _suppress_small,
    apply_residual_guard,
)
from core.services.file_vault import safe_decrypt

# Die Typ-Bindung dieses Berichts (E1, Refs #1824). ``ziel`` bleibt der
# Schluessel selbst — der Zeilenschluessel ``kategorie`` ist eine
# Maschinenschnittstelle und darf sich nicht mit der Oberflaechensprache aendern
# (siehe Modul-Docstring). Die Deklarationsreihenfolge IST die Ausgabeordnung:
# sie ist fest und damit wertunabhaengig (K5), deshalb genuegt ``ORDER_INPUT``
# an den Suppressions-Aufrufstellen.
#
TYP_BINDUNG = TypBindung(
    (
        BindungsEintrag("contact", _("Kontakt")),
        BindungsEintrag("counseling", _("Beratungsgespräch")),
        BindungsEintrag("crisis", _("Krisengespräch")),
    )
)

# Die Feld-Bindung: ohne die Feldvorlage ``dauer`` (Standardpaket
# ``core/packages/default.yaml``, Feldtyp ``number``, Einheit Minuten) gaebe es
# nur eine Tabelle mit lauter Nullminuten.
FELD_DAUER = FeldBindung((BindungsEintrag("dauer_minuten", _("Minuten"), slugs=("dauer",)),), bindet="report_key")

# Bestandsname als Ableitung — eine Wahrheit, mehrere Sichten.
KATEGORIEN: tuple[str, ...] = TYP_BINDUNG.ziele()

# Interner Zeilenschluessel: Zahl der Ereignisse, die zur Minutensumme dieser
# Zeile beigetragen haben. Suppressions-EINGABE, nie Ausgabe — die
# Rueckprojektion auf die ``FieldSpec``-Keys wirft ihn weg. Bliebe er stehen,
# waere er ueber JSON/XML selbst eine unkontrollierte Kleinstfallzahl (dieselbe
# Falle wie ``persons`` in ``sample_report_1``, Refs #1770).
BEITRAGENDE_KEY = "beitragende"

# Sprachunabhaengiger ``CompletenessIssue.code`` + ziffernfreie Export-Meldung
# (K6, Refs #1769/#1744): Die Ziffernfreiheit gilt je Code und muss in JEDER
# Sprache halten.
MISSING_VALUES_CODE = "missing_field_values"
_MISSING_VALUES_MESSAGE = _("Ereignisse ohne verwertbare Dauer sind in den Minutensummen nicht enthalten")


# Kategorie eines Dokumentationstyps — ``None`` = gehoert nicht in den Bericht.
# Aufloesung wie im Sachbericht: der ``report_key`` entscheidet, und zwar
# allein (der Slug-Zweitschluessel ist mit E3 entfallen, Entscheid E-E).
resolve_kategorie = TYP_BINDUNG.resolve

# SQL-Entsprechung derselben Deklaration — ``matching_q()`` filtert auf
# ``DocumentType``, ``matching_q("document_type__")`` auf ``Event``. Ereignis-
# Filter und Verfuegbarkeits-Pruefung koennen nicht mehr auseinanderlaufen.
matching_q = TYP_BINDUNG.matching_q


@register_backend("counseling-hours")
class CounselingHoursBackend(DatasetBackend):
    """Ereignisse und Minuten je Gespraechs-Kategorie im Zeitraum."""

    name = _("Beispiel: Beratungsstunden")
    description = _("Summiert Ereignisse und dokumentierte Minuten je Beratungskategorie im Zeitraum.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770/#1754): gezaehlt werden nur Ereignisse der drei
    # Gespraechs-Typen, nicht alle des Zeitraums. Ohne diese Deklaration
    # stuende "alle Ereignisse im Zeitraum" neben einer echten Teilmenge —
    # genau die irrefuehrende Angabe, die K3-A3 beseitigen sollte.
    population = TYPED_EVENTS
    typ_bindung = TYP_BINDUNG
    feld_bindungen = (FELD_DAUER,)
    # Nur waehlbar, wenn ``dauer`` an mindestens einem passenden **aktiven** Typ
    # haengt: ohne die Feldvorlage gaebe es nur eine Tabelle mit lauter
    # Nullminuten — ein Bericht, der aussieht, als daure kein Gespraech.
    verfuegbarkeit = (Anforderung(TYP_BINDUNG, (FELD_DAUER,)),)

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("kategorie", _("Kategorie")),
            FieldSpec("events", _("Ereignisse")),
            FieldSpec("minuten", _("Minuten")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Eine Zeile je nichtleerer Kategorie, plus Ereignis- und Minuten-Randsumme.

        Emittiert werden **nur nichtleere** Kategorien. Das ist keine Kosmetik:
        das Inferenzmodell des zentralen Guards setzt fuer GROUP-BY-Zeilen die
        Existenz-Untergrenze ``lo = 1`` an
        (``external_report._row_min_value``). Eine datenunabhaengig emittierte
        Nullzeile machte das Intervallmodell unerfuellbar — und ein leeres
        Intervall gilt dort als begruendete Ausnahme, der Guard liefe also
        fail-open.
        """
        # Lesepfad je Dokumentationstyp (E3, Refs #1849): gebunden wird ueber
        # den Berichtsschluessel, gelesen wird unter dem Feldvorlagen-Slug —
        # beides faellt nur im Standardpaket zusammen.
        lese = FELD_DAUER.leseschluessel(facility, TYP_BINDUNG)
        payloads = (
            Event.objects.for_facility(facility)
            .filter(
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            .filter(matching_q("document_type__"))
            .values_list("document_type_id", *TYP_BINDUNG.values_fields("document_type__"), "data_json")
        )

        events = dict.fromkeys(KATEGORIEN, 0)
        minuten = dict.fromkeys(KATEGORIEN, 0)
        beitragende = dict.fromkeys(KATEGORIEN, 0)
        ohne_dauer = 0
        for zeile in payloads:
            # Die Schluesselfelder kommen aus der Deklaration; wie viele es
            # sind, entscheidet sie.
            document_type_id, *schluessel, data = zeile
            kategorie = TYP_BINDUNG.resolve(*schluessel)
            if kategorie is None:  # pragma: no cover - der Filter laesst nichts anderes durch
                continue
            events[kategorie] += 1
            dauer_slug = lese.je_typ.get(document_type_id)
            dauer = as_number(safe_decrypt((data or {}).get(dauer_slug), default=None)) if dauer_slug else None
            if dauer is None:
                ohne_dauer += 1
                continue
            minuten[kategorie] += dauer
            beitragende[kategorie] += 1

        records = [
            {
                "kategorie": kategorie,
                "events": events[kategorie],
                "minuten": minuten[kategorie],
                BEITRAGENDE_KEY: beitragende[kategorie],
            }
            for kategorie in KATEGORIEN
            if events[kategorie]
        ]
        return DatasetResult(
            mode="rows",
            records=records,
            summary={
                "events_gesamt": sum(events.values()),
                "minuten_gesamt": sum(minuten.values()),
            },
            # Bezifferte INTERNE Fassung; ``prepare_export`` ersetzt sie durch
            # die ziffernfreie Export-Fassung (K6, Refs #1769). Der
            # E-J-Hinweis ist bereits ziffernfrei und laeuft unveraendert durch.
            completeness=(
                [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=ohne_dauer)]
                if ohne_dauer
                else []
            )
            + doppelbindungs_issues(lese),
        )

    # Deklaration fuer den zentralen Residual-Guard (K1, Refs #1770):
    # ``events_gesamt`` = Σ Zeilen-``events`` ist eine exakte Partition (jedes
    # gezaehlte Ereignis liegt in genau einer Kategorie). ``ORDER_INPUT``: die
    # Zeilenordnung ist die feste :data:`KATEGORIEN`-Folge und damit bereits
    # wertunabhaengig (K5).
    _EXPORT_BREAKDOWN = _Breakdown("records", "events_gesamt", ORDER_INPUT, count_key="events")

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel — Kaskade siehe Modul-Docstring.

        Die Zeilen-``events`` gehen durch die **zentrale** Maschinerie; ueber
        ``minuten`` entscheidet dieses Backend, weil nur es die Bezugsgroesse
        kennt (:data:`BEITRAGENDE_KEY`). Das ist keine zweite Suppressionslogik
        neben der zentralen: der Unterdrueckungs-Marker wird ausschliesslich
        gelesen, nie gesetzt — gesetzt wird er nur in
        :mod:`core.services.dashboard.external_report` (Architektur-Guard
        ``test_kein_artefakt_baut_eigene_unterdrueckung``).
        """
        threshold = _get_threshold(facility)
        events_gesamt = result.summary["events_gesamt"]
        # R14: eine Randsumme < k ist selbst eine Kleinstfallzahl.
        published_events_gesamt = events_gesamt if events_gesamt >= threshold else None

        view = {
            "events_gesamt": published_events_gesamt,
            "records": _suppress_small(
                result.records,
                threshold,
                count_key="events",
                margin=published_events_gesamt,
                order=ORDER_INPUT,
            ),
        }
        apply_residual_guard(view, threshold, (self._EXPORT_BREAKDOWN,))

        # Der Guard kann die ganze Aufschluesselung fallen lassen (fail-safe,
        # Key weg) und dabei auch die Randsumme nullen — beides muss die
        # Minuten-Randsumme mitnehmen.
        zeilen = view.get("records")
        minuten_vollstaendig = zeilen is not None and view["events_gesamt"] is not None
        for row in zeilen or ():
            if row.get("suppressed") or (row.get(BEITRAGENDE_KEY) or 0) < threshold:
                row["minuten"] = None
                minuten_vollstaendig = False

        records = project_records(zeilen or (), self.get_field_specs())
        summary = stamp_threshold(
            {
                **result.summary,
                "events_gesamt": view["events_gesamt"],
                "minuten_gesamt": result.summary["minuten_gesamt"] if minuten_vollstaendig else None,
            },
            threshold,
        )
        completeness = [
            CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=None),
            *doppelbindungs_issues(FELD_DAUER.leseschluessel(facility, TYP_BINDUNG)),
        ]
        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
