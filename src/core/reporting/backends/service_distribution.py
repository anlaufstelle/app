"""Beispiel-Backend „Leistungs-/Krisenarten-Verteilung" — Auswahlfelder (Refs #1754).

Drittes Backend ueber ``data_json``-FELDWERTEN (Grundmuster siehe
:mod:`core.reporting.backends.needle_exchange_balance`), aber das erste ueber
**Auswahl**-Feldern statt Zahlen — und damit der Fall, an dem die
Randsummen-Frage kippt.

**Zwei Feldgruppen, bewusst nebeneinander statt verschmolzen:**

* ``leistungen`` — ``multi_select`` am Kontakt-Typ. Ein Ereignis kann mehrere
  Optionen tragen.
* ``art-der-krise`` — ``select`` am Krisengespraech-Typ. Ein Ereignis traegt
  hoechstens eine Option.

Der Bericht enthaelt nur die Gruppen, die in der Einrichtung tatsaechlich
konfiguriert sind (Feldvorlage aktiv und an einem passenden aktiven
Dokumentationstyp angehaengt); :meth:`ServiceDistributionBackend.is_available`
verlangt mindestens eine davon.

**Optionsschluessel kommen roh aus ``data_json``.** Sie sind Klartext-Slugs und
werden NICHT uebersetzt: sie sind der stabile Maschinenschluessel der Zeile.
Inaktiv gestellte Optionen (Soft-Delete-Muster im Standardpaket, z. B.
``sachspenden``) und Schluessel, die es in ``options_json`` gar nicht mehr gibt,
erscheinen deshalb **wie dokumentiert** — der Bericht bildet ab, was
tatsaechlich erfasst wurde, nicht, was heute noch waehlbar waere. Wer
Anzeigenamen braucht, schlaegt sie ueber ``FieldTemplate.options_json`` nach.

**K-Anonymitaet — der eigentliche Lehrpunkt dieses Backends.**

``art-der-krise`` ist eine **exakte Partition** von ``events_art_der_krise``
(jedes gezaehlte Ereignis liegt in genau einer Option): die Randsumme wird
deklariert (``margin=…`` + :class:`~core.services.dashboard.external_report._Breakdown`),
Sekundaer-Suppression und Residual-Guard rechnen an dieser Gleichung.

``leistungen`` ist **keine**: durch Mehrfachnennung gilt nur die Ungleichung
``Σ Options-Counts >= events_leistungen``. Daraus folgen ZWEI getrennte
Konsequenzen, und die zweite ist die, die man leicht uebersieht:

1. **``margin=None`` an ``_suppress_small``.** Eine Randsumme zu deklarieren,
   die nicht exakt partitioniert, waere schlimmer als keine: das
   Machbarkeits-Audit (``_audit_feasible_span``) und der Fixpunkt-Guard rechnen
   mit ``Residuum = Randsumme − Σ sichtbare Zellen``, und dieses Residuum ist
   hier schlicht falsch. Mit ``margin=None`` laufen beide bewusst ins Leere
   (``_audit_feasible_span`` kehrt sofort zurueck, ``disclosure.findings``
   ueberspringt Partitionen ohne Randsumme).
2. **Total-Doktrin statt Sekundaer-Suppression**
   (:meth:`ServiceDistributionBackend._drop_margin_on_any_suppression`).
   ``margin=None`` ist **kein Schutz, sondern nur dessen Abschaltung** — die
   Ungleichung bleibt ja bestehen und ist selbst ein Kanal: sie liefert fuer
   jede unterdrueckte Zelle die **untere** Schranke
   ``x >= events_leistungen − Σ sichtbare Zellen``, waehrend die
   Primaer-Suppression die obere ``x <= k − 1`` liefert. Fallen beide zusammen,
   ist die Zelle exakt bestimmt: bei *k* = 5, sichtbarer Zelle 6 und
   publizierter Randsumme 10 ist die unterdrueckte Zelle zwingend 4. Deshalb
   faellt ``events_leistungen``, sobald **eine** Zelle der Gruppe unterdrueckt
   ist — ohne publizierte Randsumme gibt es die untere Schranke nicht mehr, und
   es bleibt ``1 <= x <= k − 1``.

Unabhaengig davon durchlaeuft ``events_leistungen`` R14 (< *k* ⇒ ``None``), denn
sie ist selbst eine Ereigniszahl.

**Merke fuer Nachahmer:** „meine Aufschluesselung partitioniert nicht" heisst
nicht „hier gibt es nichts zurueckzurechnen". Es heisst: die zentrale
Maschinerie ist nicht zustaendig, also braucht die Aufschluesselung eine eigene,
benannte Regel. Der Reflex, ``margin=None`` als Entwarnung zu lesen, war der
Fehler in der ersten Fassung dieses Moduls.

**Grenze dieser Begruendung: sie gilt artefakt-LOKAL** (K4a, Refs #1770/#1754).
Alles oben rechnet innerhalb *dieses* Berichts. ``events_leistungen`` zaehlt
dieselbe Ereignismenge wie die ``contact``-Zeile in
:mod:`core.reporting.backends.counseling_hours` — dort allerdings ALLE
Kontakt-Ereignisse, hier nur die mit Auswahlwert. Die **Differenz** ist die Zahl
der Kontakte ohne Leistungs-Eintrag: eine Groesse, die in keinem der beiden
Berichte steht und deshalb durch keine Suppressionsregel laeuft. Fuer
``events_art_der_krise`` und die ``crisis``-Zeile gilt dasselbe.

Das ist **keine offene Baustelle, sondern die akzeptierte Definitionsgrenze**
der k-Anonymitaets-Zusicherung: sie gilt je Artefakt und je Zeitraum. Ein
artefaktuebergreifender Guard muesste wissen, welche Berichte an denselben
Empfaenger gegangen sind — Wissen, das im Erzeugungspfad nicht existiert.
Flankierend gibt es die **Freigabe-Uebersicht**
(:class:`core.views.statistics.ReportReleaseLogView`, K4a): sie verhindert den
Kanal nicht, macht ueberlappende Herausgaben aber sichtbar.

Eine Optionszelle zaehlt **Ereignisse mit dieser Option**, nicht Nennungen:
ein Ereignis, das dieselbe Option doppelt fuehrt (Altdaten), zaehlt einmal.
Sonst waere die Zelle keine Fallzahl und die k-Schwelle darauf bedeutungslos.
"""

import dataclasses

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
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

# Feld-Template-Slugs des Standardpakets (``core/packages/default.yaml``).
LEISTUNGEN_SLUG = "leistungen"
KRISE_SLUG = "art-der-krise"

# Sprachunabhaengiger ``CompletenessIssue.code`` + ziffernfreie Export-Meldung
# (K6, Refs #1769/#1744).
MISSING_VALUES_CODE = "missing_field_values"
_MISSING_VALUES_MESSAGE = _("Ereignisse ohne Auswahlwert sind in der Verteilung nicht enthalten")


@dataclasses.dataclass(frozen=True)
class Feldgruppe:
    """Eine auswertbare Feld-Konfiguration — Fachlogik dieses Berichts.

    ``system_type``/``paket_slug`` bilden die Typaufloesung ab (Erstschluessel
    zuerst und exklusiv, Slug nur bei leerem ``system_type`` — Muster
    ``resolve_category``, Refs #1741/#1785). ``mehrfachnennung`` entscheidet
    ueber die Randsummen-Deklaration und ist damit **die** sicherheitsrelevante
    Eigenschaft dieser Struktur, nicht bloss Metadatum.
    """

    feld_slug: str
    system_type: str
    paket_slug: str
    mehrfachnennung: bool

    @property
    def summary_key(self) -> str:
        """Randsummen-Schluessel im ``summary`` — sprachunabhaengiger Klartext."""
        return f"events_{self.feld_slug.replace('-', '_')}"

    @property
    def records_key(self) -> str:
        """Interner Schluessel der Aufschluesselung in der Guard-Sicht."""
        return f"records_{self.feld_slug.replace('-', '_')}"

    def matching_q(self, prefix: str = "") -> Q:
        """SQL-Entsprechung der Typaufloesung — ``prefix`` fuer JOIN-Pfade.

        ``matching_q()`` filtert auf :class:`~core.models.DocumentType`,
        ``matching_q("document_type__")`` auf :class:`~core.models.Event`. Eine
        Definition fuer beide Aufrufstellen, damit Ereignis-Filter und
        Konfigurations-Pruefung nie auseinanderlaufen.
        """
        unset = Q(**{f"{prefix}system_type__isnull": True}) | Q(**{f"{prefix}system_type": ""})
        return Q(**{f"{prefix}system_type": self.system_type}) | (unset & Q(**{f"{prefix}slug": self.paket_slug}))


# Feste Reihenfolge = Ausgabeordnung der Gruppen; innerhalb einer Gruppe wird
# nach Optionsschluessel sortiert. Beides wertunabhaengig (K5), deshalb genuegt
# ``ORDER_INPUT`` an den Suppressions-Aufrufstellen.
GRUPPEN: tuple[Feldgruppe, ...] = (
    Feldgruppe(LEISTUNGEN_SLUG, DocumentType.SystemType.CONTACT, "kontakt", mehrfachnennung=True),
    Feldgruppe(KRISE_SLUG, DocumentType.SystemType.CRISIS, "krisengespraech", mehrfachnennung=False),
)


def as_options(raw) -> list[str]:
    """Feldwert nach Options-Schluesseln koerzieren — leere Liste heisst „kein Wert".

    ``data_json`` ist ein freies JSONB: dasselbe Feld kann eine Liste (heutiges
    ``multi_select``), einen blanken String (Alt-/Fremdschreibpfad, und der
    Normalfall bei ``select``) oder Unsinn enthalten. Alles Nicht-Verwertbare
    faellt still weg und wird ueber die Vollstaendigkeits-Meldung sichtbar —
    ein ``TypeError`` hier killte den ganzen Bericht wegen eines Feldes.

    Duplikate werden entfernt: die Zelle zaehlt Ereignisse mit dieser Option,
    nicht Nennungen (siehe Modul-Docstring).
    """
    werte = raw if isinstance(raw, list | tuple) else [raw]
    treffer = []
    for wert in werte:
        if isinstance(wert, str) and wert.strip() and wert.strip() not in treffer:
            treffer.append(wert.strip())
    return treffer


@register_backend("service-distribution")
class ServiceDistributionBackend(DatasetBackend):
    """Options-Verteilung der Auswahlfelder ``leistungen`` und ``art-der-krise``."""

    name = _("Beispiel: Leistungs- und Krisenarten-Verteilung")
    description = _("Zählt, wie oft die Optionen der Leistungs- und Krisenarten-Felder im Zeitraum gewählt wurden.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770/#1754): die Randsummen zaehlen Ereignisse der
    # jeweiligen Feld-Typen MIT Auswahlwert, also eine Teilmenge einer
    # Teilmenge. ``ALL_EVENTS`` waere hier doppelt falsch.
    population = TYPED_EVENTS

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("feld", _("Feld")),
            FieldSpec("option", _("Option")),
            FieldSpec("count", _("Anzahl")),
        ]

    def _ist_konfiguriert(self, facility, gruppe: Feldgruppe) -> bool:
        """Haengt die Feldvorlage der Gruppe aktiv an einem passenden aktiven Typ?"""
        return (
            DocumentType.objects.filter(facility=facility, is_active=True)
            .filter(gruppe.matching_q())
            .filter(fields__field_template__slug=gruppe.feld_slug, fields__field_template__is_active=True)
            .exists()
        )

    def is_available(self, facility) -> bool:
        """Waehlbar, sobald MINDESTENS eine der beiden Gruppen konfiguriert ist."""
        return any(self._ist_konfiguriert(facility, gruppe) for gruppe in GRUPPEN)

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Eine Zeile je (Feld, Option); Randsumme je Feld sind die Ereignisse mit Wert.

        Nur **konfigurierte** Gruppen erscheinen — mit Zeilen UND Randsumme. Das
        haengt an der Einrichtungs-Konfiguration, nicht an den Fallzahlen, und
        ist deshalb keine datenabhaengige Verzweigung im Sinne von K6: die
        Gestalt des Berichts verraet nichts ueber den Bestand.

        Es entstehen nur Zeilen fuer tatsaechlich vorkommende Optionen. Das ist
        Absicht: das Inferenzmodell des zentralen Guards setzt fuer solche
        GROUP-BY-Zeilen die Existenz-Untergrenze ``lo = 1`` an
        (``external_report._row_min_value``); datenunabhaengig emittierte
        Nullzeilen machten es unerfuellbar und den Guard damit fail-open.
        """
        records: list[dict] = []
        summary: dict = {}
        ohne_wert = 0
        for gruppe in GRUPPEN:
            if not self._ist_konfiguriert(facility, gruppe):
                continue
            payloads = (
                Event.objects.filter(
                    facility=facility,
                    is_deleted=False,
                    occurred_at__date__gte=date_from,
                    occurred_at__date__lte=date_to,
                )
                .filter(gruppe.matching_q("document_type__"))
                .values_list("data_json", flat=True)
            )
            counts: dict[str, int] = {}
            events_mit_wert = 0
            for data in payloads:
                optionen = as_options(safe_decrypt((data or {}).get(gruppe.feld_slug), default=None))
                if not optionen:
                    ohne_wert += 1
                    continue
                events_mit_wert += 1
                for option in optionen:
                    counts[option] = counts.get(option, 0) + 1
            summary[gruppe.summary_key] = events_mit_wert
            records.extend(
                {"feld": gruppe.feld_slug, "option": option, "count": counts[option]} for option in sorted(counts)
            )

        return DatasetResult(
            mode="rows",
            records=records,
            summary=summary,
            # Bezifferte INTERNE Fassung; ``prepare_export`` ersetzt sie durch
            # die ziffernfreie Export-Fassung (K6, Refs #1769).
            completeness=(
                [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=ohne_wert)]
                if ohne_wert
                else []
            ),
        )

    @staticmethod
    def _drop_margin_on_any_suppression(view: dict, gruppe: Feldgruppe) -> None:
        """Total-Doktrin fuer die Mehrfachnennungs-Randsumme (C1, Refs #1754).

        Analog :func:`~core.services.dashboard.external_report.drop_person_margin_on_person_suppression`:
        die Randsumme faellt, sobald **eine** Zelle der Gruppe unterdrueckt ist.

        **Warum es nicht ohne geht.** ``Σ Options-Counts >= events_leistungen``
        (jedes Ereignis mit Wert traegt nach Dedup mindestens eine Option bei).
        Diese Ungleichung liefert fuer jede unterdrueckte Zelle eine **untere**
        Schranke ``x >= Randsumme − Σ sichtbare Zellen``, und die
        Primaer-Suppression liefert die obere ``x <= k − 1``. Fallen beide
        zusammen, ist die Zelle exakt bestimmt (k = 5, sichtbar 6, Randsumme 10
        ⇒ die unterdrueckte Zelle ist genau 4). Ohne publizierte Randsumme gibt
        es die untere Schranke nicht mehr, und es bleibt ``1 <= x <= k − 1``.

        Bewusst **schaerfer** als der Fixpunkt-Guard und bewusst hier statt
        dort: der Guard modelliert exakte Partitionen (``Σ Zellen = Randsumme``),
        und eine Ungleichung ist kein Fall seines Inferenzmodells — sie in
        ``disclosure.py`` nachzuruesten waere ein zweites Modell neben dem
        bestehenden (die in ``_SAMPLE_REPORT_1_BREAKDOWNS`` als Route (2)
        vermerkte, bewusst nicht gebaute Erweiterung). Bis dahin ist die
        pauschale Total-Doktrin die sichere Richtung.

        Setzt **nur** die Randsumme auf ``None``; der Unterdrueckungs-Marker der
        Zeilen wird ausschliesslich gelesen (Architektur-Guard
        ``test_kein_artefakt_baut_eigene_unterdrueckung``).
        """
        if any(row.get("suppressed") for row in view.get(gruppe.records_key, ())):
            view[gruppe.summary_key] = None

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression je Gruppe — Randsummen-Begruendung im Modul-Docstring.

        Die Gruppen werden strikt getrennt suppressiert: sie teilen keine
        Randsumme, und eine Zellsuppression in der einen darf in der anderen
        nichts ausloesen.

        Zwei **verschiedene** Schutzwege, je nach Partitionseigenschaft der
        Gruppe (Begruendung im Modul-Docstring):

        * Einfachauswahl — exakte Partition: ``margin`` deklariert,
          Sekundaer-Suppression und :func:`apply_residual_guard` uebernehmen.
        * Mehrfachauswahl — nur Ungleichung: ``margin=None`` (die zentrale
          Maschinerie darf nicht an einer falschen Gleichung rechnen) UND
          :meth:`_drop_margin_on_any_suppression` als Total-Doktrin. ``margin=None``
          allein waere **kein** Schutz, sondern nur dessen Abschaltung.
        """
        threshold = _get_threshold(facility)
        view: dict = {}
        breakdowns: list[_Breakdown] = []
        aktive: list[Feldgruppe] = []

        for gruppe in GRUPPEN:
            if gruppe.summary_key not in result.summary:
                continue
            aktive.append(gruppe)
            events = result.summary[gruppe.summary_key]
            # R14: eine Randsumme < k ist selbst eine Kleinstfallzahl — auch
            # dort, wo sie keine Partition traegt.
            published = events if events >= threshold else None
            # ``margin`` NUR bei exakter Partition: eine deklarierte, aber
            # unzutreffende Randsumme laesse das Machbarkeits-Audit an einer
            # falschen Gleichung rechnen.
            margin = None if gruppe.mehrfachnennung else published
            zeilen = [row for row in result.records if row["feld"] == gruppe.feld_slug]
            view[gruppe.summary_key] = published
            view[gruppe.records_key] = _suppress_small(zeilen, threshold, margin=margin, order=ORDER_INPUT)
            if gruppe.mehrfachnennung:
                self._drop_margin_on_any_suppression(view, gruppe)
            else:
                breakdowns.append(_Breakdown(gruppe.records_key, gruppe.summary_key, ORDER_INPUT))

        apply_residual_guard(view, threshold, tuple(breakdowns))

        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [
            {key: row[key] for key in field_keys}
            # Der Guard kann eine ganze Aufschluesselung fallen lassen (Key weg,
            # fail-safe) — deshalb ``get`` statt Indexzugriff.
            for gruppe in aktive
            for row in view.get(gruppe.records_key, ())
        ]
        summary = {
            **{gruppe.summary_key: view[gruppe.summary_key] for gruppe in aktive},
            "k_anonymity_threshold": threshold,
        }
        completeness = [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=None)]
        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
