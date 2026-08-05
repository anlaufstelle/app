"""Beispiel-Backend „Beratungsstunden" — Zeilen-Tabelle ueber Feldwerten (Refs #1754).

Zweites Backend ueber ``data_json``-FELDWERTEN (siehe
:mod:`core.reporting.backends.needle_exchange_balance` fuer die
Grundmuster), aber das erste mit einer **Aufschluesselung**: je Kategorie eine
Zeile mit Ereigniszahl und aufsummierten Minuten aus der Paket-Feldvorlage
``dauer``.

**Kategorien.** Die drei Paket-Dokumentationstypen, an denen ``dauer`` haengt:
``kontakt`` (``contact``), ``beratungsgespraech`` (``counseling``) und
``krisengespraech`` (``crisis``). Die Aufloesung folgt dem
``resolve_category``-Muster (Refs #1741/#1785): ``system_type`` zuerst und
exklusiv, der Paket-Slug nur bei leerem ``system_type``.

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
"""

import dataclasses

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event

# Die Zahl-Koersion wird bewusst aus dem Referenz-Backend importiert statt
# kopiert: sie definiert, was „beitragendes Ereignis" heisst, und genau diese
# Zahl ist die Bezugsgroesse der K-Anon-Regel fuer Summen. Zwei Kopien
# driften — und eine gedriftete Koersion hiesse: zwei Backends unterdruecken
# nach verschiedenen Massstaeben. Wer dieses Modul als Vorlage kopiert, nimmt
# ``as_number`` mit oder importiert es weiter von hier.
from core.reporting.backends.needle_exchange_balance import as_number
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import (
    ORDER_INPUT,
    _Breakdown,
    _get_threshold,
    _suppress_small,
    apply_residual_guard,
)
from core.services.file_vault import safe_decrypt

# Feld-Template-Slug des Standardpakets (``core/packages/default.yaml``);
# Feldtyp ``number``, Einheit Minuten.
DAUER_SLUG = "dauer"

# Erstschluessel UND Ausgabeordnung in einem: die Reihenfolge ist fest und damit
# wertunabhaengig (K5) — die Zeilenposition traegt keinen Rangkanal, deshalb
# genuegt ``ORDER_INPUT`` an den Suppressions-Aufrufstellen.
KATEGORIEN: tuple[str, ...] = (
    DocumentType.SystemType.CONTACT,
    DocumentType.SystemType.COUNSELING,
    DocumentType.SystemType.CRISIS,
)

# Zweitschluessel: Paket-Slug -> Kategorie. Bewusst genau die drei Slugs, deren
# ``system_type`` ohnehin oben steht — der Zweitschluessel darf nichts
# hereinholen, was der Erstschluessel bewusst auslaesst.
SLUG_MAP = {
    "kontakt": DocumentType.SystemType.CONTACT,
    "beratungsgespraech": DocumentType.SystemType.COUNSELING,
    "krisengespraech": DocumentType.SystemType.CRISIS,
}

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


def resolve_kategorie(system_type: str | None, slug: str | None) -> str | None:
    """Kategorie eines Dokumentationstyps — ``None`` = gehoert nicht in den Bericht.

    Aufloesungsordnung wie
    :func:`core.reporting.backends.sample_report_1.resolve_category`: ein
    GESETZTER ``system_type`` entscheidet allein, auch wenn er hier gar nicht
    vorkommt (``note``/``ban`` heisst „nicht in diesen Bericht"). Der Slug greift
    NUR bei leerem ``system_type`` — sonst zoege ein Notiz-Typ mit dem Slug
    ``kontakt`` seine Ereignisse in die Stundenbilanz.
    """
    if system_type:
        return system_type if system_type in KATEGORIEN else None
    return SLUG_MAP.get(slug or "")


def matching_q(prefix: str = "") -> Q:
    """SQL-Entsprechung von :func:`resolve_kategorie` — ``prefix`` fuer JOIN-Pfade.

    ``matching_q()`` filtert auf :class:`~core.models.DocumentType`,
    ``matching_q("document_type__")`` auf :class:`~core.models.Event`. Eine
    Definition fuer beide Aufrufstellen, damit Ereignis-Filter und
    Verfuegbarkeits-Pruefung nie auseinanderlaufen. Der ``isnull``-Zweig ist
    bewusst redundant: ``system_type`` ist NULL-bar, blankes SQL-``NOT IN``
    faengt NULL-Zeilen nicht.
    """
    unset = Q(**{f"{prefix}system_type__isnull": True}) | Q(**{f"{prefix}system_type": ""})
    return Q(**{f"{prefix}system_type__in": KATEGORIEN}) | (unset & Q(**{f"{prefix}slug__in": tuple(SLUG_MAP)}))


@register_backend("counseling-hours")
class CounselingHoursBackend(DatasetBackend):
    """Ereignisse und Minuten je Gespraechs-Kategorie im Zeitraum."""

    name = _("Beispiel: Beratungsstunden")
    snapshot_enabled = False

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("kategorie", _("Kategorie")),
            FieldSpec("events", _("Ereignisse")),
            FieldSpec("minuten", _("Minuten")),
        ]

    def is_available(self, facility) -> bool:
        """Nur waehlbar, wenn ``dauer`` an mindestens einem passenden Typ haengt.

        Ohne die Feldvorlage gaebe es nur eine Tabelle mit lauter Nullminuten —
        ein Bericht, der aussieht, als daure kein Gespraech.
        """
        return (
            DocumentType.objects.filter(facility=facility, is_active=True)
            .filter(matching_q())
            .filter(fields__field_template__slug=DAUER_SLUG, fields__field_template__is_active=True)
            .exists()
        )

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
        payloads = (
            Event.objects.filter(
                facility=facility,
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            .filter(matching_q("document_type__"))
            .values_list("document_type__system_type", "document_type__slug", "data_json")
        )

        events = dict.fromkeys(KATEGORIEN, 0)
        minuten = dict.fromkeys(KATEGORIEN, 0)
        beitragende = dict.fromkeys(KATEGORIEN, 0)
        ohne_dauer = 0
        for system_type, slug, data in payloads:
            kategorie = resolve_kategorie(system_type, slug)
            if kategorie is None:  # pragma: no cover - der Filter laesst nichts anderes durch
                continue
            events[kategorie] += 1
            dauer = as_number(safe_decrypt((data or {}).get(DAUER_SLUG), default=None))
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
            # die ziffernfreie Export-Fassung (K6, Refs #1769).
            completeness=(
                [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=ohne_dauer)]
                if ohne_dauer
                else []
            ),
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

        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [{key: row[key] for key in field_keys} for row in zeilen or ()]
        summary = {
            **result.summary,
            "events_gesamt": view["events_gesamt"],
            "minuten_gesamt": result.summary["minuten_gesamt"] if minuten_vollstaendig else None,
            "k_anonymity_threshold": threshold,
        }
        completeness = [CompletenessIssue(MISSING_VALUES_CODE, _MISSING_VALUES_MESSAGE, "warning", count=None)]
        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
