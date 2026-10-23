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
from core.reporting.values import as_options
from core.services.dashboard.external_report import (
    _get_threshold,
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

    Diese Struktur war die **Vorlage** der Generalisierung (Refs #1824): sie
    buendelte schon vorher ``(feld_slug, system_type, paket_slug)`` und leitete
    ``matching_q`` daraus ab, statt es erneut hinzuschreiben. Seit E1 sind die
    beiden Haelften ausgelagert — :class:`~core.reporting.base.TypBindung`
    (welcher Dokumentationstyp) und :class:`~core.reporting.base.FeldBindung`
    (welche Feldvorlage) — und die Gruppe komponiert sie.

    ``mehrfachnennung`` bleibt hier: es entscheidet ueber die
    Randsummen-Deklaration und ist damit **die** sicherheitsrelevante
    Eigenschaft dieser Struktur, nicht bloss Metadatum.

    ``feld_slug`` bleibt ebenfalls ein **eigenes deklariertes Feld** und wird
    NICHT aus der Feld-Bindung abgeleitet: es ist der **publizierte** Schluessel
    (Spaltenwert ``feld`` in ``records``, Praefix von ``summary_key``) und damit
    Teil des Artefakt- und Snapshot-Formats.
    :meth:`~core.reporting.base.FeldBindung.leseschluessel` ist dagegen der
    reine **Lesepfad** und loest seit E3 je Dokumentationstyp auf — kaeme der
    publizierte Wert von dort, benennte eine Einrichtung mit eigener
    Feldbenennung still Export-Spalten und ``summary``-Schluessel um. Im
    Standardpaket sind beide derselbe String; das ist eine Tatsache, keine
    Kopplung.
    """

    feld_slug: str
    typ_bindung: TypBindung
    feld_bindung: FeldBindung
    mehrfachnennung: bool

    def leseschluessel(self, facility):
        """Der ``data_json``-Lesepfad dieser Gruppe, je Dokumentationstyp (E3).

        Gescoped auf die Typ-Bindung DIESER Gruppe: der Doppelbindungs-Hinweis
        gehoert dem Bericht, nicht der Einrichtung.
        """
        return self.feld_bindung.leseschluessel(facility, self.typ_bindung)

    @property
    def summary_key(self) -> str:
        """Randsummen-Schluessel im ``summary`` — sprachunabhaengiger Klartext."""
        return f"events_{self.feld_slug.replace('-', '_')}"

    @property
    def records_key(self) -> str:
        """Interner Schluessel der Aufschluesselung in der Guard-Sicht."""
        return f"records_{self.feld_slug.replace('-', '_')}"

    @property
    def anforderung(self) -> Anforderung:
        """„Gruppe ist konfiguriert" — passender aktiver Typ MIT aktiver Feldvorlage."""
        return Anforderung(self.typ_bindung, (self.feld_bindung,))

    def matching_q(self, prefix: str = "") -> Q:
        """SQL-Entsprechung der Typaufloesung — ``prefix`` fuer JOIN-Pfade.

        ``matching_q()`` filtert auf :class:`~core.models.DocumentType`,
        ``matching_q("document_type__")`` auf :class:`~core.models.Event`. Eine
        Definition fuer beide Aufrufstellen, damit Ereignis-Filter und
        Konfigurations-Pruefung nie auseinanderlaufen.
        """
        return self.typ_bindung.matching_q(prefix)


# Feste Reihenfolge = Ausgabeordnung der Gruppen; innerhalb einer Gruppe wird
# nach Optionsschluessel sortiert. Beides wertunabhaengig (K5), deshalb genuegt
# ``ORDER_INPUT`` an den Suppressions-Aufrufstellen.
#
# Die Aufloesung — ein Berichtsschluessel je Ebene, kein Zweitschluessel mehr
# (Entscheid E-E, Refs #1849) — steckt in ``TypBindung``/``FeldBindung``, nicht
# in dieser Datei.
GRUPPEN: tuple[Feldgruppe, ...] = (
    Feldgruppe(
        LEISTUNGEN_SLUG,
        TypBindung((BindungsEintrag("contact", _("Kontakt")),)),
        FeldBindung(
            (BindungsEintrag("leistungen", _("Leistungen nach Kategorie"), slugs=(LEISTUNGEN_SLUG,)),),
            bindet="report_key",
        ),
        mehrfachnennung=True,
    ),
    Feldgruppe(
        KRISE_SLUG,
        TypBindung((BindungsEintrag("crisis", _("Krisengespräch")),)),
        FeldBindung((BindungsEintrag("art_der_krise", _("Art der Krise"), slugs=(KRISE_SLUG,)),), bindet="report_key"),
        mehrfachnennung=False,
    ),
)


@register_backend("service-distribution")
class ServiceDistributionBackend(DatasetBackend):
    """Options-Verteilung der Auswahlfelder ``leistungen`` und ``art-der-krise``."""

    name = _("Beispiel: Leistungs- und Krisenarten-Verteilung")
    count_groups = GRUPPEN
    disclosure_dimensions = frozenset({"event_counts"})
    description = _("Zählt, wie oft die Optionen der Leistungs- und Krisenarten-Felder im Zeitraum gewählt wurden.")
    snapshot_enabled = False
    # K3-A3 (Refs #1770/#1754): die Randsummen zaehlen Ereignisse der
    # jeweiligen Feld-Typen MIT Auswahlwert, also eine Teilmenge einer
    # Teilmenge. ``ALL_EVENTS`` waere hier doppelt falsch.
    population = TYPED_EVENTS
    # Die Bindungen liegen je Gruppe in ``verfuegbarkeit`` — dieses Backend hat
    # keine einzelne Typ-Bindung, sondern zwei nebeneinander. Die ODER-Semantik
    # von ``is_available`` (Basisklasse) ist genau die Bestandsregel: waehlbar,
    # sobald MINDESTENS eine der beiden Gruppen konfiguriert ist.
    verfuegbarkeit = tuple(gruppe.anforderung for gruppe in GRUPPEN)

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("feld", _("Feld")),
            FieldSpec("option", _("Option")),
            FieldSpec("count", _("Anzahl")),
        ]

    def _ist_konfiguriert(self, facility, gruppe: Feldgruppe) -> bool:
        """Haengt die Feldvorlage der Gruppe aktiv an einem passenden aktiven Typ?"""
        return gruppe.anforderung.erfuellt(facility)

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
        leseschluessel = []
        for gruppe in GRUPPEN:
            if not self._ist_konfiguriert(facility, gruppe):
                continue
            lese = gruppe.leseschluessel(facility)
            leseschluessel.append(lese)
            payloads = (
                Event.objects.for_facility(facility)
                .filter(
                    is_deleted=False,
                    occurred_at__date__gte=date_from,
                    occurred_at__date__lte=date_to,
                )
                .filter(gruppe.matching_q("document_type__"))
                .values_list("document_type_id", "data_json")
            )
            counts: dict[str, int] = {}
            events_mit_wert = 0
            for document_type_id, data in payloads:
                # Lesepfad aus der Bindung (je Dokumentationstyp aufgeloest),
                # Ausgabe unter dem publizierten Schluessel — die beiden duerfen
                # nicht dieselbe Quelle haben.
                lese_schluessel = lese.je_typ.get(document_type_id)
                roh = (data or {}).get(lese_schluessel) if lese_schluessel else None
                optionen = as_options(safe_decrypt(roh, default=None))
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
            disclosure_context=(date_from, date_to, "live"),
            mode="rows",
            records=records,
            summary=summary,
            # Bezifferte INTERNE Fassung; ``prepare_export`` ersetzt sie durch
            # die ziffernfreie Export-Fassung (K6, Refs #1769).
            completeness=(
                [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=ohne_wert)]
                if ohne_wert
                else []
            )
            + doppelbindungs_issues(*leseschluessel),
        )

    @staticmethod
    def _drop_margin_on_any_suppression(view: dict, group: Feldgruppe) -> None:
        """Delegate the existing multi-option margin contract."""
        from core.services.dashboard.external_report import _drop_service_margin

        _drop_service_margin(view, group)

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """Publish the local contract through the shared population mask."""
        from core.services.dashboard.external_report import _protect_service_dataset
        from core.services.dashboard.shared_disclosure import project_shared_dataset

        local = _protect_service_dataset(
            result,
            _get_threshold(facility),
            field_specs=self.get_field_specs(),
            groups=GRUPPEN,
        )
        local = dataclasses.replace(
            local,
            completeness=[
                CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=None),
                *doppelbindungs_issues(
                    *(group.leseschluessel(facility) for group in GRUPPEN if group.summary_key in result.summary)
                ),
            ],
        )
        return project_shared_dataset(facility, local, "service-distribution")
