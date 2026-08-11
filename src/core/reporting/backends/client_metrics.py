"""Beispiel-Backend „Klienten-Kennzahlen" (Refs #1754).

**Zweck.** Referenzfall fuer einen Bericht, dessen Zahlen ausnahmslos
**Personen** zaehlen — die schaerfste Klasse im K-Anon-Modell. Er zeigt drei
Dinge, die ein Ereignis-Backend (:mod:`~core.reporting.backends.example`,
:mod:`~core.reporting.backends.monthly_trend`) nicht zeigen kann: die
DISTINCT-Zaehlung ueber ``Event.client``, die Trennung von Bestands- und
Zugangszahl, und eine Aufschluesselung, die ihre Randsumme **exakt**
partitioniert.

**Kennzahlen.**

* ``aktive_personen`` — Personen mit mindestens einem sichtbaren Ereignis im
  Zeitraum (DISTINCT ueber ``Event.client``; anonyme Ereignisse ohne
  Personenbezug zaehlen naturgemaess nicht mit).
* ``neuaufnahmen`` — Personen, deren Datensatz im Zeitraum angelegt wurde
  (``Client.created_at``). Bewusst **unabhaengig** von ``aktive_personen``: eine
  neu angelegte Person muss im Zeitraum kein Ereignis haben, und eine aktive
  Person kann aus einem frueheren Jahr stammen. Die beiden Zahlen stehen
  deshalb in **keiner** arithmetischen Beziehung — genau das macht sie
  k-anon-technisch unkritisch nebeneinander (siehe unten).
* Die Aufschluesselung ``records`` — Personen je ``Client.age_cluster``.

**Zwei Momentaufnahmen, die man kennen muss.**

1. ``Client.age_cluster`` ist der **heutige** Wert des Personendatensatzes,
   nicht der zum Ereigniszeitpunkt. Ein Bericht ueber einen zurueckliegenden
   Zeitraum ordnet eine Person also der Gruppe zu, in der sie **jetzt** steht;
   ein Gruppenwechsel schreibt Vergangenheit um. Ein historisch exakter
   Altersschnitt braeuchte eine Historisierung am Personendatensatz, die es
   bewusst nicht gibt (Datensparsamkeit). Dieselbe Einschraenkung gilt fuer die
   Altersgruppen des Sachberichts.
2. ``neuaufnahmen`` zaehlt nur **nicht geloeschte** Personen. Eine spaeter
   geloeschte Person verschwindet damit rueckwirkend aus der Zahl — gewollt
   (der Loeschpfad soll keine Spur in Auswertungen lassen), aber es heisst,
   dass derselbe Zeitraum spaeter eine kleinere Zahl liefern kann. Das
   ``is_active``-Flag wird dagegen **nicht** ausgewertet: Deaktivieren ist ein
   Arbeitsstand, keine Aussage darueber, ob die Aufnahme stattgefunden hat.

**Die Soft-Delete-Asymmetrie zwischen den Kennzahlen ist beabsichtigt**
(Refs #1754). ``neuaufnahmen`` filtert ``Client.is_deleted=False``,
``aktive_personen`` und ``records`` tun das **nicht** — sie zaehlen ueber die
Ereignisse (``Event.is_deleted=False``) und nehmen eine Person im Papierkorb
weiter mit. Grund: Die beiden Zahlen beantworten verschiedene Fragen.
``neuaufnahmen`` beschreibt den **Personenbestand** („wen fuehren wir seit dem
Zeitraum?"), und wer im Papierkorb liegt, wird gerade nicht mehr gefuehrt.
``aktive_personen`` beschreibt **Arbeit, die stattgefunden hat** — der Besuch
ist ein Fakt des Zeitraums, den ein spaeterer Loeschantrag nicht ungeschehen
macht; ein Ereignis, das nicht mehr zaehlen soll, wird selbst soft-deletet, und
genau darauf filtert die Abfrage.

Zwei Folgen, die man kennen muss: (a) ``aktive_personen`` kann groesser sein als
die Zahl der aktuell gefuehrten Personen; (b) nach Ablauf der Papierkorb-Frist
anonymisiert ``anonymize_client`` den Datensatz und setzt ``age_cluster`` auf
``unknown`` — die Person bleibt also in ``aktive_personen``, wandert in der
Verteilung aber in die ``unknown``-Zeile. Ein zweiter Lauf ueber denselben
Zeitraum liefert dann dieselbe Randsumme bei anderer Verteilung. Beides ist
Folge derselben Entscheidung: die Loeschung greift am **Personendatensatz**, die
Zaehlung haengt am **Ereignis**.

**K-Anonymitaet.** Alle drei Groessen sind Personenzaehlungen und laufen
vollstaendig durch die zentrale Maschinerie:

* ``aktive_personen`` und ``neuaufnahmen`` fallen unter der Schwelle je fuer
  sich (R14) — eine Randsumme < *k* ist selbst eine Kleinstfallzahl.
* ``records`` partitioniert ``aktive_personen`` **exakt** (jede Person traegt
  genau einen ``age_cluster``, und beide Zahlen kommen aus derselben
  Ereignismenge). Damit greift die volle Kette: :func:`_suppress_small` mit
  ``margin`` = publizierte Randsumme (Machbarkeits-Audit K2) und
  :func:`apply_residual_guard` als letzter Schritt (K1). Bliebe die Randsumme
  nach einer Zellen-Suppression roh stehen, waere die unterdrueckte Gruppe als
  Differenz rueckrechenbar.
* **Die Personen-Randsummen-Doktrin gilt hier genauso wie im Bestand**
  (:func:`drop_person_margin_on_person_suppression`, Auflage 1 zu K0-b,
  Refs #1770): faellt **eine** Altersgruppe personenbedingt unter die Schwelle,
  faellt ``aktive_personen`` mit. ``aktive_personen`` ist dieselbe Groesse, die
  ``core.services.dashboard.statistics.count_unique_clients`` zaehlt, und
  ``records`` ist die DISTINCT-Verteilung genau dieser Personenmenge — es ist
  also **dieselbe Partition**, fuer die die Doktrin in allen drei
  Bestandsartefakten laeuft, nur unter anderen Schluesseln. Der Fixpunkt-Guard
  allein genuegt nicht: bei vier Gruppen (2, 1, 1, 1) und ``aktive_personen``
  = 5 misst er genau ``MIN_FEASIBLE_SPAN`` und greift nicht, obwohl die einzige
  konsistente Partition drei Gruppen mit exakt einer Person festlegt. Die
  Doktrin setzt bewusst schon bei der *Einengung* an, der Guard erst beim
  exakten Pin — sie ist die erste Linie, nicht die zweite.
* **K0-Fail-safe** (:func:`_drop_person_derived_breakdowns`, Refs #1768/#1754):
  Liegt ``aktive_personen`` **selbst** unter der Schwelle, faellt die
  Altersverteilung **ganz** — nicht nur ihre Werte. Werte zu nullen genuegt
  nicht, weil schon die **Zeilenexistenz** eine Aussage ueber die Verteilung
  der < *k* Personen ist: eine einzige Zeile „u18, leer" publiziert „alle
  weniger als *k* aktiven Personen sind unter 18", also einen Pin bis hinunter
  zu *k* = 1. Der Bestand laesst in genau dieser Lage die ganze Tabelle fallen;
  dieser Bericht darf nicht genauer sein als das Artefakt, dessen Menge er
  zaehlt (siehe K4a unten).
* ``neuaufnahmen`` ist **keine** Zelle dieser Partition und darf deshalb nicht
  in die Kaskade: es gibt keine Gleichung, ueber die sie eine unterdrueckte
  Alterszelle aufloesen koennte. Sie steht einzeln an der Schwelle — auch der
  K0-Fail-safe fasst sie nicht an.

**Zur ``persons``-Mechanik der Helper.** ``_suppress_small`` kennt ein zweites,
personenbasiertes Kriterium ueber den internen Schluessel ``persons`` (K0-b,
Refs #1770): Ereigniszeilen fuehren dort zusaetzlich ihre DISTINCT-Personenzahl
mit, damit eine Zelle „30 Ereignisse" nicht von zwei Personen stammen kann. Die
Zeilen hier brauchen das **nicht** — ihr Zaehlwert *ist* die Personenzahl —, und
sie duerfen die Spalte auch gar nicht ``persons`` nennen: ``_suppress_small``
entfernt ``persons`` aus jeder Ausgabezeile (die Zahl ist Suppressions-Eingabe
und nie Ausgabe), die Datenspalte verschwaende also spurlos. Konsequenz fuer den
Guard: ``person_gated``/``reveals_person_gap`` bleiben ``False``. Das ist kein
Nachlass, sondern korrekt — die Ausnahme „exakter Pin >= *k* ist kein Befund"
gilt genau dann, wenn der rueckrechenbare Wert selbst keine Kleinstfallzahl ist,
und ein Pin auf >= *k* Personen ist keine.

**K4a — dieses Backend publiziert Zahlen, die der Bestands-Guard als „nie
publiziert" modelliert** (Refs #1770; Fix-Runde 1 zu #1754).

:func:`~core.services.dashboard.external_report._person_partition_cells` baut die
Personen-Partition der Bestandsartefakte mit ``published=None`` fuer **jede**
Zelle, ausdruecklich begruendet mit „Die Personenzahlen werden **nie**
publiziert" — sie sind dort reine Suppressions-Eingabe und werden von
:func:`_suppress_small` aus jeder Ausgabezeile entfernt. Innerhalb jedes
Bestandsartefakts stimmt das weiterhin.

**Artefaktuebergreifend stimmt es seit diesem Backend nicht mehr.** ``records``
publiziert genau diese Groesse: Personen je Altersgruppe im Zeitraum, dieselbe
Menge, die ``by_age_cluster``/``unique_clients`` im externen Bericht und im
Sachbericht-PDF zaehlen. Wer beide Artefakte desselben Zeitraums hat, kennt fuer
jede sichtbare Gruppe den exakten Wert, den das Inferenzmodell des
Bestands-Guards dort nur als ``[k, ∞)`` kennt.

**Was das praktisch begrenzt** — und warum es kein offener Befund ist: beide
Seiten treffen ihre Suppressionsentscheidung ueber **dieselben** Personenzahlen,
**dieselbe** Schwelle und seit der finalen Fix-Welle (Refs #1754) auch **dieselben
drei Regeln**: Zell-Suppression, Personen-Randsummen-Doktrin und K0-Fail-safe
(alle drei oben, alle drei aus dem zentralen Modul). Eine Gruppe mit weniger als
*k* Personen faellt hier wie dort; ist die Gesamtpersonenmenge kleiner als *k*,
faellt hier wie dort die ganze Tabelle. Publiziert wird also nur, was auch der
Bestand publizieren duerfte; die Verschaerfung liegt in der **Genauigkeit** der
uebrigen Zellen, nicht darin, dass eine Kleinstfallzahl sichtbar wuerde.

(Vor der finalen Fix-Welle stimmte dieser Satz **nicht**: der K0-Fail-safe fehlte
hier, und eine Tabelle mit genullten Zeilen neben ``aktive_personen = null``
publizierte die Altersverteilung einer Personenmenge, die der Bestand in
derselben Lage komplett zurueckhaelt.)

**Trotzdem gehoert es hierher.** Es ist ein K4a-Fall — artefaktlokal nicht
loesbar (es gibt keine Zelle, an der eine Regel ansetzen koennte), und ein
Backend darf keine eigene erfinden. Die flankierende Massnahme ist dieselbe wie
dort: die **Freigabe-Uebersicht** macht ueberlappende Herausgaben sichtbar.
Sollte die Guard-Annahme je verschaerft werden muessen, ist dies die Stelle, an
der steht, warum.

``snapshot_enabled = False`` (Beispielvorlage, kein Monats-Merge-Thema).
"""

import dataclasses

from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from core.models import Client, DocumentType, Event
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.data_source import PERSONS
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _drop_person_derived_breakdowns,
    _get_threshold,
    _suppress_small,
    apply_residual_guard,
    drop_person_margin_on_person_suppression,
)

# Zaehlschluessel der Aufschluesselung. Bewusst NICHT ``persons``: das ist der
# reservierte interne Schluessel der zentralen Suppression, der aus jeder
# Ausgabezeile entfernt wird (siehe Modul-Docstring).
_COUNT_KEY = "personen"


def _events_with_person(facility, date_from, date_to):
    """Sichtbare Ereignisse **mit** Personenbezug im Zeitraum."""
    return Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    ).exclude(client__isnull=True)


@register_backend("client-metrics")
class ClientMetricsBackend(DatasetBackend):
    """Aktive Personen, Neuaufnahmen und Altersverteilung im Zeitraum."""

    name = _("Beispiel: Klienten-Kennzahlen")
    description = _("Zählt die Personen mit mindestens einem Ereignis im Zeitraum, aufgeschlüsselt nach Altersgruppe.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770): Grundgesamtheit sind **Personen** mit mindestens einem
    # Ereignis im Zeitraum, nicht Ereignisse. Ohne den Ausweis liest sich die
    # Tabelle wie eine Ereigniszaehlung und summiert sich anders als jede
    # Nachbartabelle.
    population = PERSONS

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("alterscluster", _("Altersgruppe")),
            FieldSpec(_COUNT_KEY, _("Personen")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Personen-Kennzahlen im Zeitraum; ``records`` = Verteilung ueber die Altersgruppen.

        ``summary`` traegt die beiden Randgroessen, ``records`` die
        Aufschluesselung — beide zusammen sind das vollstaendige Aggregat. Die
        Zeilen kommen nach ``age_cluster``-Schluessel sortiert und damit
        **wertunabhaengig** (K5): keine Rangliste, deshalb ``ORDER_INPUT`` im
        Export.
        """
        base_qs = _events_with_person(facility, date_from, date_to)

        aktive_personen = base_qs.values("client").distinct().count()

        neuaufnahmen = Client.objects.filter(
            facility=facility,
            is_deleted=False,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        ).count()

        records = [
            {"alterscluster": row["client__age_cluster"], _COUNT_KEY: row[_COUNT_KEY]}
            for row in base_qs.values("client__age_cluster")
            .annotate(**{_COUNT_KEY: Count("client", distinct=True)})
            .order_by("client__age_cluster")
        ]

        return DatasetResult(
            mode="aggregate",
            records=records,
            summary={"aktive_personen": aktive_personen, "neuaufnahmen": neuaufnahmen},
            completeness=[],
        )

    # Deklaration fuer den zentralen Residual-Guard (K1, Refs #1770): die
    # Altersverteilung partitioniert ``aktive_personen`` exakt.
    _EXPORT_BREAKDOWN = _Breakdown("records", "aktive_personen", ORDER_INPUT, count_key=_COUNT_KEY)

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel-Vorschau (Refs #1278/#1770).

        Reihenfolge **exakt wie in den drei Bestandsartefakten** — nur mit
        anderen Schluesseln:

        1. Beide Randzahlen unter der Schwelle werden ``None`` (R14).
        2. :func:`_suppress_small` nullt Alterszellen < *k* und fuehrt das
           Machbarkeits-Audit (K2) gegen die **publizierte** Randsumme aus —
           ``margin=published_aktive``, nicht die Rohzahl: gegen eine gar nicht
           veroeffentlichte Randsumme kann niemand rueckrechnen.
        3. :func:`drop_person_margin_on_person_suppression` — die Personen-
           Randsummen-Doktrin (Auflage 1 zu K0-b, Refs #1770). Sie ist hier
           **Pflicht und nicht Kuer**: ``aktive_personen`` ist dieselbe Groesse,
           die ``statistics.count_unique_clients()`` zaehlt, und ``records`` ist
           die DISTINCT-Altersverteilung genau dieser Personenmenge — also die
           Partition, fuer die die Doktrin in allen drei Bestandsartefakten
           laeuft. Ohne sie bliebe die Randsumme im Fall (2, 1, 1, 1) bei
           ``aktive_personen`` = 5 stehen: der Fixpunkt-Guard misst dort genau
           ``MIN_FEASIBLE_SPAN`` und greift nicht, obwohl die einzige
           konsistente Partition drei Gruppen mit exakt einer Person festlegt.
           Die Doktrin ist bewusst schaerfer als der Guard — sie setzt schon
           bei der *Einengung* an, nicht erst beim exakten Pin.
        4. :func:`apply_residual_guard` (K1).
        5. :func:`_drop_person_derived_breakdowns` — der K0-Fail-safe
           (Refs #1768/#1754). Liegt die **rohe** ``aktive_personen`` unter der
           Schwelle, faellt ``records`` als ganze Tabelle: die Zeilenexistenz
           allein publiziert sonst die Verteilung einer Personenmenge, von der
           das Artefakt gleichzeitig sagt, dass sie kleiner als *k* ist. Auch
           dieser Guard kommt mit Schluessel-Parameter aus dem zentralen Modul,
           statt hier als ``if`` nachgebaut zu werden.

        Aufgerufen wird die Doktrin mit den **rohen** ``result.records`` (dort
        stehen die Personenzahlen noch) und mit den Schluesseln dieses Backends
        — die Regel selbst bleibt im zentralen Modul. Ein backend-lokales
        ``if`` waere eine zweite Suppressionslogik und damit genau das, was der
        Architektur-Guard verbietet.

        ``neuaufnahmen`` bleibt bewusst **ausserhalb** von ``view``: sie ist
        keine Zelle der Partition, und sie in die Kaskade zu nehmen waere eine
        Eigenbau-Regel ohne Gleichung dahinter (uebersuppression ohne
        Schutzgewinn).

        Zum Schluss die Rueckprojektion auf die FieldSpec-Keys — das interne
        ``suppressed``-Flag ist keine Spalte und landete sonst in CSV/JSON/XML.

        Erwartet das **rohe** ``build_dataset``-Ergebnis (nicht idempotent).
        """
        threshold = _get_threshold(facility)

        aktive = result.summary["aktive_personen"]
        published_aktive = None if aktive < threshold else aktive
        view = {
            "aktive_personen": published_aktive,
            "records": _suppress_small(
                result.records, threshold, count_key=_COUNT_KEY, margin=published_aktive, order=ORDER_INPUT
            ),
        }
        drop_person_margin_on_person_suppression(
            view,
            result.records,
            threshold,
            count_key=_COUNT_KEY,
            margin_key="aktive_personen",
        )
        apply_residual_guard(view, threshold, (self._EXPORT_BREAKDOWN,))
        # 5. K0-Fail-safe (Refs #1768/#1754): liegt die **rohe** Personenzahl
        #    unter der Schwelle, faellt die ganze Altersverteilung — Werte nullen
        #    genuegt nicht, weil schon die Zeilenexistenz die Verteilung der < k
        #    Personen publiziert (eine einzige „u18"-Zeile sagt: alle sind u18).
        #    Gegen die **rohe** Zahl, nicht gegen ``published_aktive``: die ist
        #    genau dann ``None``, wenn der Fail-safe greifen muss.
        _drop_person_derived_breakdowns(view, aktive, threshold, breakdown_keys=("records",))

        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [{key: row[key] for key in field_keys} for row in view.get("records", [])]

        neuaufnahmen = result.summary["neuaufnahmen"]
        summary = dict(result.summary)
        summary["aktive_personen"] = view["aktive_personen"]
        summary["neuaufnahmen"] = None if neuaufnahmen < threshold else neuaufnahmen
        summary["k_anonymity_threshold"] = threshold
        return dataclasses.replace(result, records=records, summary=summary)

    def is_available(self, facility) -> bool:
        """Waehlbar, sobald die Einrichtung mindestens einen aktiven
        Dokumentationstyp fuer **Ereignisse** fuehrt (Seed-/Paket-Bindung,
        Maintainer-Entscheid 2026-08-05).

        Personen-Kennzahlen setzen dokumentierbare Ereignisse voraus: ohne einen
        ``entity_type="event"``-Typ kann keine Person „aktiv im Zeitraum" werden,
        und der Bericht zeigte dauerhaft Nullen. Personen- und Fall-Typen
        (``entity_type`` ``client``/``case``) genuegen dafuer ausdruecklich
        nicht — sie beschreiben Stammdatenformulare, keine Ereignisse.
        """
        return DocumentType.objects.filter(
            facility=facility,
            is_active=True,
            entity_type=DocumentType.EntityType.EVENT,
        ).exists()
