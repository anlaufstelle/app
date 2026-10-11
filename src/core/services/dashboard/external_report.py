"""Datenschutzfreundliche externe Berichte (Refs #921).

Wrappt :func:`core.services.dashboard.statistics.get_statistics`:

- entfernt ``top_clients`` (Pseudonym-Ranking) komplett
- wendet K-Anonymity-Schwelle auf Aggregate an: Werte < Schwelle werden auf
  ``None`` gesetzt und mit ``suppressed=True`` markiert
- legt Datenschutzprofil-Metadaten am Report-Kopf ab (Zeitraum, Facility,
  K-Anon-Schwelle, Generierungs-Timestamp)

Re-uses ``Settings.k_anonymity_threshold`` (Default 5) als Schwelle —
konsistent mit der K-Anon-Strategie aus #780.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from django.utils import timezone
from django.utils.translation import gettext_lazy

from core.models import Client, Settings
from core.models.settings import K_ANONYMITY_MIN, K_ANONYMITY_WARN_BELOW
from core.reporting.data_source import ALL_EVENTS, DATA_SOURCE_NOTES, LIVE, PERSONS
from core.services.dashboard.disclosure import MAX_GUARD_ITERATIONS, Cell, Partition, findings
from core.services.dashboard.statistics import PERSONS_KEY, get_statistics

logger = logging.getLogger(__name__)

DEFAULT_K_THRESHOLD = 5

# --- K3-A1 (Refs #1770): Restzeile „ohne Personenbezug" ---------------------
#
# ``by_age_cluster`` zaehlt nur Ereignisse MIT Personenbezug, ``total_contacts``
# alle. Die Differenz W = ``total_contacts`` − Σ ``by_age_cluster`` ist **kein
# publiziertes Aggregat** und beruehrte damit keine Suppressionsregel — sie war
# aber aus zwei sichtbaren (also gerade nicht unterdrueckten, weil grossen)
# Randsummen exakt ausrechenbar. Der Kanal erzeugt kleine Zahlen aus grossen:
# ueber 15 728 distinkte Publikationen war W fast durchweg exakt bestimmbar, in
# 8 820 davon mit 0 < W < k.
#
# Die Restzeile macht W zu einer regulaeren, suppressionsfaehigen Zelle. Damit
# ist die Alterstabelle eine **exakte Partition** von ``total_contacts`` und
# faellt neu unter K1/K2 (siehe ``_EXTERNAL_REPORT_BREAKDOWNS``).
NO_PERSON_CLUSTER = "__no_person__"
NO_PERSON_LABEL = gettext_lazy("Ohne Personenbezug")

# Modellreihenfolge der Altersgruppen — die kanonische, wertunabhaengige
# Ordnung fuer unterdrueckte ``by_age_cluster``-Zeilen (K5, Refs #1767). Aus
# ``Client.AgeCluster`` abgeleitet statt hier dupliziert: eine neue Altersgruppe
# soll die Ordnung automatisch erben. Die Restzeile steht **fest** hinter allen
# Altersgruppen: ihre Position darf nie eine Wertaussage sein.
_AGE_CLUSTER_ORDER = {value: index for index, value in enumerate(Client.AgeCluster.values)}
_AGE_CLUSTER_ORDER[NO_PERSON_CLUSTER] = len(_AGE_CLUSTER_ORDER)


def _age_cluster_key(row: dict[str, Any]) -> tuple[int, str]:
    """Altersgruppen in Modellreihenfolge; Unbekanntes stabil ans Ende."""
    cluster = row.get("cluster")
    # ``_AGE_CLUSTER_ORDER`` hat ausschliesslich str-Schluessel — ein
    # Nicht-str-``cluster`` (inkl. ``None``) faende ohnehin nie einen Treffer
    # und faellt auf den Default zurueck; die isinstance-Pruefung macht das
    # fuer mypy beweisbar, ohne das Verhalten zu aendern.
    fallback = len(_AGE_CLUSTER_ORDER)
    index = _AGE_CLUSTER_ORDER.get(cluster, fallback) if isinstance(cluster, str) else fallback
    return (index, str(cluster or ""))


def with_no_person_row(
    age_rows: Sequence[dict[str, Any]], total_events: int | None, *, count_key: str = "count"
) -> list[dict[str, Any]]:
    """Haengt die Restzeile „ohne Personenbezug" an (K3-A1, Refs #1770).

    Drei Auflagen der Analyse, alle drei hier verankert:

    1. **Immer emittieren, auch bei W = 0.** Erschiene die Zeile nur bei W > 0,
       waere ihre blosse Existenz die Aussage „W >= 1" — genau die
       datenabhaengige Verzweigung, die K6 im Vollstaendigkeitsblock beseitigt
       hat. W = 0 laeuft stattdessen als regulaere Kleinstzelle in die
       Suppression.
    2. **Fuer Alt-Snapshots synthetisieren, nicht bloss tolerieren.** Die Zeile
       wird hier — an der Artefaktgrenze — aus ``total_contacts`` − Σ
       *berechnet* und nie aus einem Snapshot uebernommen. Damit gibt es keinen
       Alt-/Neu-Snapshot-Split und keine Formataenderung: der Merge summiert
       ``total_contacts`` und die Alterszeilen ohnehin konsistent, also ist die
       Differenz ueber den gemergten Gesamtzeitraum exakt. Ein Rueckfall auf
       eine gespeicherte Zeile waere der Bypass.
    3. **Die Tabelle faellt neu unter K1/K2** — das leistet nicht diese
       Funktion, sondern die Randsummen-Deklaration an der Aufrufstelle
       (``margin=published_total``) und der Eintrag in
       ``_EXTERNAL_REPORT_BREAKDOWNS``.

    Idempotent: eine bereits vorhandene Restzeile wird verworfen und neu
    gerechnet — sonst zaehlte ein kuenftiger Lesepfad, der sie mitbraechte, W
    doppelt.

    Die Zeile traegt bewusst **kein** ``persons``-Feld: ihre Aequivalenzklasse
    ist keine Personenmenge, sondern die Menge der Ereignisse ohne
    Personen-FK — untereinander nicht verknuepfbar (dieselbe Begruendung wie
    bei ``persons_by_contact_stage['anonym']``). Sie bleibt damit aus der
    Personen-Randsumme Σ ``persons`` = ``unique_clients`` heraus, die sonst
    still braeche.
    """
    rows = [dict(row) for row in age_rows if row.get("cluster") != NO_PERSON_CLUSTER]
    counted = sum(row.get(count_key) or 0 for row in rows)
    remainder = max(0, (total_events or 0) - counted)
    return [*rows, {"cluster": NO_PERSON_CLUSTER, "label": str(NO_PERSON_LABEL), count_key: remainder}]


def _row_min_value(row: dict[str, Any]) -> int:
    """Zeilen-Existenz-Untergrenze **je Zeile** (K1/K2-Inferenzmodell).

    GROUP-BY-Zeilen entstehen nur fuer nichtleere Buckets, tragen also
    mindestens einen Fall (``lo = 1``). Die Restzeile aus :func:`with_no_person_row`
    wird dagegen **datenunabhaengig** emittiert und darf 0 sein (``lo = 0``) —
    wie die festen Schluessel in ``by_contact_stage``.

    Diese Unterscheidung ist nicht kosmetisch: eine Untergrenze 1 auf einer
    Zelle, die 0 sein kann, macht das Intervallmodell **unerfuellbar**, und ein
    leeres Intervall gilt in ``disclosure.findings`` als begruendete Ausnahme.
    Der Guard liefe damit fail-open — auch fuer die Nachbarzellen, deren
    Obergrenze ueber Σ ``lo`` der uebrigen Zellen berechnet wird.
    """
    return 0 if row.get("cluster") == NO_PERSON_CLUSTER else 1


def _document_type_key(row: dict[str, Any]) -> tuple[str]:
    """Dokumenttypen nach ``name`` — wertunabhaengig.

    Refs #1749 (ADR-040): frueher ``(category, name)``. Mit dem Wegfall von
    ``DocumentType.category`` traegt keine Zeile mehr einen Kategoriewert (auch
    nicht aus Alt-Snapshots — ``_merge_stats`` verwirft ihn), und der Name ist
    je Einrichtung eindeutig. Die Ordnung bleibt damit total und
    wertunabhaengig (K5).
    """
    return (str(row.get("name") or ""),)


def _category_key(row: dict[str, Any]) -> tuple[str]:
    """Berichtskategorien nach ``name`` — wertunabhaengig UND datenunabhaengig.

    Die Kategoriennamen sind deutscher Klartext aus ``CATEGORY_MAP``
    (``backends/sample_report_1.py``) und gerade **nicht** uebersetzt, damit sie
    ueber Snapshots und Merges stabil bleiben; die Ordnung haengt also auch
    nicht an der Sprache des rendernden Prozesses.
    """
    return (str(row.get("name") or ""),)


# Kanonische Ausgabeordnungen (K5, Refs #1767/#1770). Jede Aufrufstelle von
# :func:`_suppress_small` **muss** eine davon benennen (``order`` ist ein
# Pflicht-Keyword) — die frueher formbasierte Erkennung war fail-open: eine
# kuenftige count-sortierte Tabelle mit neuer Zeilenform waere still
# unkanonisiert geblieben, und Zeilen mit ``category`` ohne ``name``
# (``ExampleBackend``) liefen ueber einen Teilschluessel ``(category, "")``,
# dessen ``category`` dort ein **uebersetztes** Label ist (locale-abhaengige
# Blockordnung). Jetzt entscheidet die Aufrufstelle, und der Architektur-Guard
# ``TestZentraleSuppressionsfunktion`` prueft, dass sie es tut.
#
# ``ORDER_INPUT`` heisst: die Eingabeordnung ist bereits **wertunabhaengig**
# (feste Schluesselordnung) — es gibt keinen Rangkanal, den eine Umsortierung
# schliessen koennte. Das gilt fuer ``by_contact_stage`` (feste Stufenfolge aus
# dem Modell) und die ``records``-/``records_<feld>``-Tabellen der
# Beispiel-Backends (Eingabeordnung des jeweiligen Backends, Refs #1754).
#
# NICHT mehr fuer ``by_category``: dessen Einfuegereihenfolge ist die Position
# des alphabetisch **ersten im Zeitraum tatsaechlich vorhandenen** Quell-Typs je
# Kategorie (Ordnung abgeleitet ueber ``TYP_BINDUNG.values_fields()`` →
# ``report_key`` in ``backends/sample_report_1.py``; bis Refs #1849 lieferte
# die Ableitung ``system_type`` + Slug-Zweitschluessel) und damit
# datenabhaengig — zwei Welten mit
# identischen publizierten Werten, aber verschiedenen Quelltypen hinter der
# Kleinstzelle lieferten verschiedene Zeilenpositionen. Deshalb ORDER_CATEGORY,
# und seit Refs #1772 auch fuer die **sichtbaren** Zeilen dieser Ordnung
# (``_ORDERS_CANONICAL_FOR_VISIBLE``).
ORDER_AGE_CLUSTER = "age_cluster"
ORDER_CATEGORY = "category"
ORDER_DOCUMENT_TYPE = "document_type"
ORDER_INPUT = "input"

_CANONICAL_ORDERS: dict[str, Callable[[dict[str, Any]], tuple] | None] = {
    ORDER_AGE_CLUSTER: _age_cluster_key,
    ORDER_CATEGORY: _category_key,
    ORDER_DOCUMENT_TYPE: _document_type_key,
    ORDER_INPUT: None,
}

# Ordnungen, deren **sichtbare** Zeilen ebenfalls kanonisch sortiert werden
# (Refs #1772, Punkt 1). Kriterium ist nicht der Geschmack, sondern die Frage,
# ob die Eingabeordnung aus den publizierten Werten folgt:
#
# * ``by_document_type``/``by_age_cluster`` kommen ``-count``-sortiert — wer die
#   Werte liest, kennt die Ordnung ohnehin; sie zu kanonisieren kostete
#   Nutzwert (die Tabellen werden als Rangliste gelesen) ohne Schutzgewinn.
# * ``by_category`` kommt in der Reihenfolge der Konsolidierung, und die haengt
#   an den **Rohdaten**: eine Kategorie steht dort, wo ihr alphabetisch erster
#   im Zeitraum vorhandener ``report_key`` steht. Zwei Welten mit identischen
#   publizierten Werten — „Vermittlung" aus ``accompaniment`` bzw. aus
#   ``referral`` — lieferten verschiedene Positionen, auch wenn die Zelle
#   sichtbar ist. Das ist ein Kanal ueber die Rohdaten, kein Nebenprodukt der
#   Werte.
_ORDERS_CANONICAL_FOR_VISIBLE = frozenset({ORDER_CATEGORY})


def effective_k_threshold(stored: int) -> int:
    """Threshold the reports compute with for a stored settings value.

    The model validator keeps every value saved through the form at ``K_ANONYMITY_MIN``
    or above; a smaller value is either a stored value from before the validator or a
    write that bypassed validation. ``0`` would suppress nothing at all (no count is
    below 0), so it falls back to the default. ``1`` stays as stored: it is the boundary
    the suppression functions are defined for; it is logged and warned about on the
    settings page and on the report pages.
    """
    return stored if stored >= 1 else DEFAULT_K_THRESHOLD


def _get_threshold(facility) -> int:
    """K-anonymity threshold from the facility settings; the default without settings.

    A stored value below ``K_ANONYMITY_MIN`` is logged, so it never takes effect silently.
    """
    try:
        settings = Settings.objects.get(facility=facility)
    except Settings.DoesNotExist:
        return DEFAULT_K_THRESHOLD
    stored = settings.k_anonymity_threshold
    threshold = effective_k_threshold(stored)
    if stored < K_ANONYMITY_MIN:
        logger.warning(
            "Stored k-anonymity threshold %s for facility %s is below the minimum %s; computing with %s.",
            stored,
            facility.pk,
            K_ANONYMITY_MIN,
            threshold,
        )
    return threshold


def k_threshold_warning(threshold):
    """Risk warning for an effective threshold below 3 (ADR-023), ``None`` otherwise.

    Below 3 the reports publish groups of two; persons in small groups can be
    recognised from the figures. At 1 nothing is suppressed, single cases included.
    Shown on the settings page and the report pages.
    """
    if threshold is None or threshold >= K_ANONYMITY_WARN_BELOW:
        return None
    if threshold < K_ANONYMITY_MIN:
        return gettext_lazy(
            "Die k-Anonymitäts-Schwelle liegt unter 3. Bei k = 1 wird keine Zahl unterdrückt; "
            "auch Einzelfälle werden veröffentlicht und können Personen erkennbar machen. Empfohlen ist k = 5."
        )
    return gettext_lazy(
        "Die k-Anonymitäts-Schwelle liegt unter 3. Gruppen ab zwei Datensätzen werden veröffentlicht; "
        "Personen in kleinen Gruppen können in den Zahlen wiedererkennbar sein. Empfohlen ist k = 5."
    )


def _audit_feasible_span(
    values: Sequence[int],
    flags: list[bool],
    threshold: int,
    *,
    margin: int | None,
    min_values: Sequence[int],
    person_gated: bool,
    tiebreak: Callable[[int], tuple],
) -> None:
    """K2 (Refs #1770): Sekundaer-Suppression an der **Machbarkeitsspanne**.

    Loest die alte Heuristik ab, die an der *Zellenzahl* haengte
    (``if len(suppressed) != 1: return``, A6.1 aus #1024/#1016). Die schuetzte
    in 27,5 % ihrer Einsaetze gar nicht: bei >= 2 primaer unterdrueckten Zellen
    lief sie **nie**, obwohl gerade dann R = s (alle = 1) die Zellen vollstaendig
    bestimmt.

    Stattdessen wird die Spanne selbst gemessen (siehe
    :mod:`core.services.dashboard.disclosure`): solange irgendeine unterdrueckte
    Zelle rueckrechenbar ist, faellt die **kleinste** sichtbare Zelle, danach
    wird neu gerechnet — bis zum Fixpunkt.

    **Warum die kleinste (User-Entscheid #1770) und was daran haengt.** Die
    Analyse empfahl die groesste, weil sie das Residuum maximiert; gemessen war
    das an einem **einmalig** laufenden Audit („kleinste faellt" 2,5 %,
    „groesste faellt" 0,2 % Rest-Leak). Diese Umsetzung iteriert bis zum
    Fixpunkt, und dort sind beide Regeln leakfrei — aber nur unter einer
    **zwingenden Kopplung**: wer die kleinste sichtbare Zelle opfert, publiziert
    damit die Aussage „dieses Opfer liegt unter jeder verbliebenen sichtbaren
    Zelle", also eine **Obergrenze**. Diese Obergrenze MUSS in
    :func:`_local_partition` gespiegelt werden (``hi = min(sichtbare)`` statt
    ``lo = max(k, groesste sichtbare)``), sonst rechnet der Guard gegen ein zu
    schwaches Angreifermodell: ohne die Spiegelung bleiben im Weltraum „3 Zeilen,
    Σ <= 14" zwei exakte Pins stehen
    (``test_reporting_disclosure_regression.py`` ist die Gegenprobe und wird
    ohne sie rot). Utility-Gewinn der Regel: die Spitzenkategorie faellt nur noch
    halb so oft.

    Ohne publizierte Randsumme (``margin is None``) argumentiert das Audit ins
    Leere und laeuft gar nicht — ohne die Gleichung R = Randsumme − Σ sichtbare
    Zellen gibt es nichts zurueckzurechnen.

    Mutiert ``flags`` in place. ``min_values`` ist die Zeilen-Existenz-Untergrenze
    **je Zelle** (1 fuer GROUP-BY-Zeilen, 0 fuer datenunabhaengig emittierte
    Zellen — feste Schluessel wie ``by_contact_stage`` und die K3-A1-Restzeile),
    ``tiebreak`` liefert bei wertgleichen Kandidaten eine wertunabhaengige,
    deterministische Reihenfolge. WELCHE, entscheidet der Aufrufer: der
    Zeilenpfad uebergibt die kanonische K5-Ordnung (:func:`_order_tiebreak`),
    der Stufenpfad (:func:`_suppress_stage_dict`) die Position ueber der festen
    Schluesselordnung des Modells. Wertunabhaengig sind beide — nur die
    Reihenfolge unterscheidet sich.
    """
    if margin is None:
        return
    victims = [False] * len(values)
    for _ in range(MAX_GUARD_ITERATIONS):
        partition = _local_partition(
            values, flags, victims, threshold, margin=margin, min_values=min_values, person_gated=person_gated
        )
        if not findings([partition], threshold=threshold):
            return
        candidates = [index for index, flag in enumerate(flags) if not flag]
        if not candidates:
            # Keine sichtbare Zelle mehr — auf Tabellenebene ist nichts mehr zu
            # holen. Die Eskalation (Randsumme fallen lassen) gehoert dem
            # Report-Guard, der die Randsumme ueberhaupt in der Hand hat.
            return
        victim = min(candidates, key=lambda index: (values[index], tiebreak(index)))
        flags[victim] = True
        victims[victim] = True


def _local_partition(
    values: Sequence[int],
    flags: Sequence[bool],
    victims: Sequence[bool],
    threshold: int,
    *,
    margin: int,
    min_values: Sequence[int],
    person_gated: bool,
) -> Partition:
    """Die Sicht des Empfaengers auf **eine** Tabelle — mit echter Herkunft.

    Innerhalb der Suppression ist bekannt, warum eine Zelle gefallen ist; die
    a-priori-Schranken werden deshalb aus der tatsaechlichen Herkunft gebildet
    (konservativ, siehe Angreifermodell in
    :mod:`core.services.dashboard.disclosure`):

    * Wert < *k* ⇒ ereignisbedingt primaer unterdrueckt ⇒ ``[min_values[i], k-1]``.
    * Wert >= *k* und Sekundaer-Opfer ⇒ **Bedingung X, zwingend an die Opferwahl
      gekoppelt:** :func:`_audit_feasible_span` opfert die **kleinste** sichtbare
      Zelle, der Wert liegt also nicht *ueber* irgendeiner verbliebenen sichtbaren
      Zelle ⇒ ``[k, kleinste sichtbare Zelle]``. Faellt die Opferwahl je wieder
      auf „groesste", muss diese Schranke mitgespiegelt werden
      (``[max(k, groesste sichtbare Zelle), ∞)``) — beides sind Aussagen, die der
      Empfaenger aus dem oeffentlichen Algorithmus ableiten kann, und eine
      *nicht* modellierte Schranke macht den Guard blind (belegt: ohne die
      Spiegelung bleiben im Weltraum „3 Zeilen, Σ <= 14" zwei exakte Pins).
      Ist keine sichtbare Zelle mehr uebrig, gibt es keine solche Schranke
      (``[k, ∞)``) — die Opfer beschraenken dann nur einander, was ein
      Box-Modell nicht ausdruecken kann.
    * Wert >= *k* ohne Opfer-Eigenschaft ⇒ personenbedingt unterdrueckt (K0-b)
      ⇒ ``[k, ∞)``. Genau hier ist die alte Guard-Annahme
      „unterdrueckt ⇒ Wert <= k-1" gebrochen.
    """
    visible = [values[index] for index, flag in enumerate(flags) if not flag]
    visible_min = min(visible, default=None)
    cells = []
    for index, value in enumerate(values):
        min_value = min_values[index]
        if not flags[index]:
            cells.append(Cell(key=str(index), published=value, lo=min_value, hi=None))
        elif value < threshold:
            cells.append(Cell(key=str(index), published=None, lo=min_value, hi=threshold - 1))
        elif victims[index]:
            cells.append(Cell(key=str(index), published=None, lo=threshold, hi=visible_min))
        else:
            cells.append(Cell(key=str(index), published=None, lo=threshold, hi=None))
    return Partition(
        name="local", margin_key="local", margin=margin, cells=tuple(cells), reveals_person_gap=person_gated
    )


def _canonicalize_suppressed_order(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    """K5 (Refs #1767): unterdrueckte Zeilen wertunabhaengig ans Listenende.

    ``by_age_cluster``/``by_document_type`` entstehen absteigend nach Anzahl
    (``order_by("-count")``, im Hybrid-Pfad erneut so sortiert). Die Suppression
    nullt nur die Werte — die **Position** blieb damit eine Rangaussage: die
    unterdrueckte Kleinstzelle stand immer am Tabellenende, das Artefakt verriet
    also ohne jede Arithmetik, welche Altersgruppe/welcher Dokumenttyp die
    Kleinstfallzahl traegt.

    Deshalb: sichtbare Zeilen behalten ihre (count-sortierte) Reihenfolge — ihre
    Werte stehen ohnehin im Klartext —, unterdrueckte Zeilen folgen als Block in
    kanonischer, wertunabhaengiger Ordnung. Der Eingriff sitzt bewusst am Ende
    von :func:`_suppress_small` (nach der Sekundaer-Suppression), damit alle drei
    Wrapper und damit alle Artefakte ihn erben, ohne dass ein Template oder ein
    Renderer etwas davon wissen muss.

    **Ausnahme ``_ORDERS_CANONICAL_FOR_VISIBLE`` (Refs #1772, Punkt 1):** die
    Freistellung sichtbarer Zeilen gilt nur, solange deren Ordnung aus den
    publizierten Werten folgt (``-count``). Bei ``by_category`` tut sie das
    nicht — dort ordnet die Konsolidierung **datenabhaengig**, und die Position
    einer sichtbaren Zeile verriet den Quell-``report_key``. Diese Ordnungen
    werden vollstaendig kanonisiert; unterdrueckte Zeilen bleiben auch dann der
    Block am Ende, damit derselbe Mechanismus greift wie ueberall sonst.

    ``order`` benennt die Ordnung explizit (siehe ``_CANONICAL_ORDERS``); ein
    unbekannter Wert ist ein ``KeyError`` und damit fail-closed.
    """
    key = _CANONICAL_ORDERS[order]
    if key is None or not rows:
        return rows
    visible = [row for row in rows if not row.get("suppressed")]
    if order in _ORDERS_CANONICAL_FOR_VISIBLE:
        visible = sorted(visible, key=key)
    hidden = sorted((row for row in rows if row.get("suppressed")), key=key)
    return [*visible, *hidden]


def _below_threshold(value: int | None, threshold: int) -> bool:
    """Fail-safe-Vergleich: ``None`` (= unbekannt) gilt immer als zu klein.

    Unbekannte Personenzahlen entstehen im Hybrid-Pfad, wo ein Alt-Snapshot
    ohne ``persons`` beitraegt oder die historische Zahl nach Ablauf der
    Loeschfristen nicht mehr rekonstruierbar ist. Geraten wird dann nicht —
    unbekannt wird wie „unter der Schwelle" behandelt (K0-b, Refs #1770).
    """
    return value is None or value < threshold


def _is_below_threshold(row: dict[str, Any], count_key: str, threshold: int) -> bool:
    """K0-b (Refs #1770): min-Logik aus Ereignis- und DISTINCT-Personenzahl.

    Unterdrueckt wird, sobald die **strengere** der beiden Zahlen unter der
    Schwelle liegt. Die Ereignispruefung bleibt bestehen (sie war das
    Bestandskriterium und ist in Sonderfaellen — z. B. der Stufe „anonym", wo
    unverknuepfte Ereignisse je als eigenes Individuum zaehlen — die schaerfere
    Bedingung).

    Ob eine Zeile personenabgeleitet ist, entscheidet ihre **Form**: nur Zeilen
    mit ``persons``-Feld werden personenbasiert geprueft. Ereignisabgeleitete
    Aufschluesselungen (``by_document_type``, ``by_category``) tragen das Feld
    nicht und bleiben rein ereignisbasiert — ihre Buckets sind Dokumenttypen,
    keine Personenmengen.
    """
    if _below_threshold(row.get(count_key, 0), threshold):
        return True
    if PERSONS_KEY in row:
        return _below_threshold(row[PERSONS_KEY], threshold)
    return False


def require_person_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """K0-b (Refs #1770): personenabgeleitete Zeilen MUESSEN eine Personenzahl tragen.

    Jede Zeile ohne ``persons`` bekommt ``persons=None`` — also „unbekannt" und
    damit fail-safe unterdrueckt. Das ist der Eintrittspunkt fuer Alt-Format:
    Snapshots aus der Zeit vor der Payload-Erweiterung liefern ``by_age_cluster``
    ohne Personenzahl, und ohne diese Normalisierung fiele die Zeile still auf
    das alte, ereignisbasierte Kriterium zurueck (fail-open).

    Wird von jedem Wrapper auf ``by_age_cluster`` angewandt — ein benannter,
    greppbarer Schritt statt eines impliziten Defaults.
    """
    return [row if PERSONS_KEY in row else {**row, PERSONS_KEY: None} for row in rows]


def _suppress_small(
    rows: list[dict[str, Any]],
    threshold: int,
    count_key: str = "count",
    *,
    margin: int | None,
    order: str,
) -> list[dict[str, Any]]:
    """Markiert Aggregate < threshold als unterdrueckt (count=None, suppressed=True).

    Das Kriterium ist seit K0-b (Refs #1770) personenbasiert, wo die Zeile eine
    DISTINCT-Personenzahl mitfuehrt (siehe :func:`_is_below_threshold`). Diese
    Zahl ist reine Suppressions-*Eingabe* und wird hier aus **jeder**
    Ausgabezeile entfernt — sichtbar wie unterdrueckt. Bliebe sie stehen, waere
    sie ueber JSON-/XML-Export selbst ein Kleinstfallzahl-Kanal (eine Zelle
    „27+: 30 Ereignisse, 6 Personen" publiziert eine Personenzahl, die nie
    durch eine Suppressionsregel gelaufen ist).

    ``margin`` ist die **publizierte** Randsumme, die diese Zeilen exakt
    partitionieren — oder ``None``, wenn das Artefakt keine solche Randsumme
    zeigt. Das Keyword ist Pflicht (fail-closed): ohne bewusste Angabe an der
    Aufrufstelle liefe das Machbarkeits-Audit (K2) entweder ins Leere oder gegen
    eine Gleichung, die es gar nicht gibt.

    ``order`` benennt die kanonische Ausgabeordnung unterdrueckter Zeilen (K5)
    und ist aus demselben Grund Pflicht.
    """
    raw = [dict(row) for row in rows]
    values = [row.get(count_key) or 0 for row in raw]
    flags = [_is_below_threshold(row, count_key, threshold) for row in raw]
    _audit_feasible_span(
        values,
        flags,
        threshold,
        margin=margin,
        # Zeilen-Existenz je Zeile (siehe ``_row_min_value``): GROUP-BY-Zeilen
        # entstehen nur fuer nichtleere Buckets und tragen >= 1 Ereignis, die
        # K3-A1-Restzeile wird datenunabhaengig emittiert und darf 0 sein.
        min_values=[_row_min_value(row) for row in raw],
        person_gated=any(PERSONS_KEY in row for row in raw),
        tiebreak=_order_tiebreak(raw, order),
    )
    result = []
    for row, suppressed in zip(raw, flags, strict=True):
        row.pop(PERSONS_KEY, None)
        if suppressed:
            row[count_key] = None
        row["suppressed"] = suppressed
        result.append(row)
    return _canonicalize_suppressed_order(result, order)


def _order_tiebreak(rows: Sequence[dict[str, Any]], order: str) -> Callable[[int], tuple]:
    """Wertunabhaengiger Zweitschluessel fuer die Opferwahl (Audit UND Guard).

    Bei wertgleichen Kandidaten darf nicht die Eingabeordnung entscheiden (sie
    ist count-sortiert und damit selbst eine Rangaussage) — es entscheidet die
    kanonische K5-Ordnung, sonst die Position als letzter, stabiler Anker.

    Beide Opferwahlen nehmen das **Minimum** ueber ``(Wert, tiebreak)``; damit
    faellt bei Wertgleichheit die kanonisch **erste** Zelle, in
    :func:`_audit_feasible_span` wie in :func:`_suppress_smallest_visible`.

    Gilt fuer den **Zeilenpfad**. Der Stufenpfad
    (:func:`_suppress_stage_dict`, ``by_contact_stage``) baut seinen
    Zweitschluessel selbst — ``(-index,)`` ueber der festen Schluesselordnung,
    dort faellt bei Gleichstand also die **letzte** Stufe. Auch das ist
    wertunabhaengig; die Zusicherung lautet „wertunabhaengiger Zweitschluessel",
    nicht „ueberall dieselbe Ordnung".
    """
    key = _CANONICAL_ORDERS[order]
    if key is None:
        return lambda index: (index,)
    return lambda index: (key(rows[index]), index)


# Aufschluesselungen, deren Aequivalenzklassen **Personen** sind (nicht
# Ereignisse) — sie fallen gemeinsam mit der Personenzahl (K0, Refs #1768).
_PERSON_DERIVED_KEYS = ("by_age_cluster", "by_contact_stage")


def _drop_person_derived_breakdowns(
    report: dict[str, Any],
    unique_clients: int | None,
    threshold: int,
    *,
    breakdown_keys: Sequence[str] = _PERSON_DERIVED_KEYS,
) -> None:
    """K0-Fail-safe (Refs #1768): Personenzahl < k ⇒ Personen-Tabellen ganz weg.

    Die Zell-Suppression prueft die Schwelle gegen **Ereignis**zahlen
    (``Count("id")``), nicht gegen Personen: eine Alterszelle „Unter 18: 7" kann
    von einer einzigen Person stammen und passierte den Filter trotzdem.
    Selbstbeweisend wurde der Fall, sobald ``unique_clients`` unterdrueckt war —
    dann stand im Artefakt, dass die Gesamtpersonenmenge < k ist, waehrend die
    Verteilung genau dieser Menge im Klartext danebenstand.

    Bis das Zellkriterium selbst auf die DISTINCT-Personenzahl je Bucket
    umgestellt ist (K0-b, eigenes Paket), greift dieser Fail-safe: liegt die
    Personenzahl unter der Schwelle, faellt die **gesamte** personenabgeleitete
    Aufschluesselung, nicht nur die Kennzahl. Ereignisabgeleitete Aggregate
    (``total_contacts``/``total``, ``by_document_type``, ``by_category``) bleiben
    unberuehrt — sie zaehlen Ereignisse, nicht Personen.

    Bewusst **ein** benannter Guard mit **einer** Bedingung (kein verstreutes
    Inline-``if``): K0-b ersetzt bzw. ergaenzt genau diese Stelle. Mutiert
    ``report`` in place; fehlende Keys werden still uebergangen (nicht jeder
    Wrapper fuehrt beide Tabellen).

    **Schluessel-Parameter statt Verdrahtung** (Refs #1754, dasselbe Muster wie
    :func:`drop_person_margin_on_person_suppression`): Der Fail-safe haengt nicht
    an den Namen ``by_age_cluster``/``by_contact_stage``, sondern an der
    **Form** — eine Aufschluesselung, deren Aequivalenzklassen Personen sind,
    neben einer Personenzahl, die unter der Schwelle liegt. Genau die hat auch
    ``ClientMetricsBackend`` (``records`` = Personen je Altersgruppe neben
    ``aktive_personen``), nur unter einem anderen Schluessel. Ohne den Parameter
    baute das Backend die Regel nach — eine zweite Suppressionslogik, die der
    Architektur-Guard aus gutem Grund verbietet (sie driftet).

    Die Defaults sind die Bestandsschluessel; alle drei Bestandsaufrufer rufen
    unveraendert weiter.
    """
    if unique_clients is not None and unique_clients >= threshold:
        return
    for key in breakdown_keys:
        report.pop(key, None)


def _suppress_stage_dict(
    stage_dict: dict[str, int], threshold: int, *, persons: dict[str, int | None] | None, margin: int | None
) -> dict[str, Any]:
    """Suppress-Variante fuer den by_contact_stage-Dict — dasselbe K2-Audit.

    ``persons`` ist die DISTINCT-Personenzahl je Stufe (K0-b, Refs #1770) und
    bewusst ein **Pflicht**-Keyword ohne brauchbaren Default: ein fehlender
    Eintrag — oder ein ``persons=None`` insgesamt — bedeutet „unbekannt" und
    unterdrueckt die Stufe fail-safe. Ein optionaler Parameter haette genau die
    Alt-Format-Luecke offen gelassen, die er schliessen soll.

    ``margin`` ist die publizierte Randsumme (``total_contacts``): die drei
    Stufen partitionieren sie exakt, weil ``anonym`` als „``is_anonymous`` ODER
    ``client IS NULL``" definiert ist und ``Client.ContactStage`` genau zwei
    weitere Werte hat. Die frueher hier duplizierte Sekundaer-Heuristik
    (``len(suppressed_keys) == 1`` + kleinste sichtbare Stufe) ist entfallen —
    es gibt nur noch :func:`_audit_feasible_span`.

    Zwei Unterschiede zur Zeilenvariante, beide strukturell:
    die Stufen haben **feste** Schluessel und werden auch mit 0 emittiert
    (Untergrenze 0 statt 1, es gibt keinen Zeilen-Existenz-Kanal), und sie sind
    personen-gegatet — ein exakt zurueckrechenbarer Ereigniswert verraet dort,
    dass die Stufe personenbedingt gefallen ist, also < *k* Personen traegt.
    """
    person_counts = persons or {}
    keys = list(stage_dict)
    values = [stage_dict[key] or 0 for key in keys]
    flags = [
        _below_threshold(stage_dict[key], threshold) or _below_threshold(person_counts.get(key), threshold)
        for key in keys
    ]
    _audit_feasible_span(
        values,
        flags,
        threshold,
        margin=margin,
        min_values=[0] * len(keys),
        person_gated=True,
        # Feste Schluesselordnung — wertunabhaengig, deshalb reicht die Position.
        tiebreak=lambda index: (-index,),
    )
    return {key: (None if suppressed else stage_dict[key]) for key, suppressed in zip(keys, flags, strict=True)}


# --- K1: Fixpunkt-Guard ueber die fertige, publizierte Sicht ----------------
#
# Der Guard laeuft als **letzter** Schritt der Artefakt-Erzeugung. Er sieht nur
# noch, was der Empfaenger sieht (Randsummen, sichtbare Zellen, Zeilenlabels)
# und ist damit die zweite, unabhaengige Verteidigungslinie hinter dem
# tabellenlokalen K2-Audit: er rechnet **ueber Tabellen hinweg** — zwei
# Aufschluesselungen derselben Randsumme informieren sich gegenseitig, und die
# Personen-Randsumme Σ persons(by_age_cluster) = unique_clients ist eine
# Partition wie jede andere.

# Name der Personen-Partition in Guard-Befunden. Sie hat keine publizierten
# Zellen (``persons`` verlaesst die Suppression nie) — ihre einzige Eskalation
# ist deshalb das Fallenlassen der Randsumme ``unique_clients``.
_PERSON_PARTITION = "by_age_cluster:persons"

PERSON_MARGIN_KEY = "unique_clients"


@dataclasses.dataclass(frozen=True)
class _Breakdown:
    """Deklaration einer Aufschluesselung im Artefakt fuer den K1-Guard."""

    key: str
    margin_key: str | None
    order: str
    kind: str = "rows"  # "rows" (Liste von Zeilen) | "stages" (Dict fester Schluessel)
    count_key: str = "count"
    reveals_person_gap: bool = False


# Die drei Artefakt-Familien. Sie stehen hier und nicht in den Wrappern, damit
# der Architektur-Guard sie statisch findet und ein neues Artefakt mit
# Randsumme + Aufschluesselung sich hier eintragen muss.
_EXTERNAL_REPORT_BREAKDOWNS = (
    _Breakdown("by_document_type", "total_contacts", ORDER_DOCUMENT_TYPE),
    _Breakdown("by_contact_stage", "total_contacts", ORDER_INPUT, kind="stages", reveals_person_gap=True),
    # K3-A1 (Refs #1770): Seit die Restzeile „ohne Personenbezug" mitlaeuft, ist
    # die Alterstabelle eine **exakte Partition von ``total_contacts``** — der
    # Eintrag „geprueft und nicht tragfaehig" der Analyse (kein Artefakt
    # publiziere eine Ereignis-Randsumme = Σ Altersgruppen) ist damit
    # ueberholt, und zwar genau an der Stelle, die er selbst als Ausloeser
    # benannt hat. Die Tabelle deklariert die Randsumme deshalb und faellt neu
    # unter K1/K2. Der **zweite** Kanal dieser Tabelle bleibt die
    # Personen-Randsumme Σ ``persons`` = ``unique_clients``; sie steht als
    # eigene Partition daneben (K0-b, Auflage 2) und ist von der Restzeile
    # unberuehrt, weil diese kein ``persons`` traegt.
    _Breakdown("by_age_cluster", "total_contacts", ORDER_AGE_CLUSTER, reveals_person_gap=True),
)

_SAMPLE_REPORT_1_BREAKDOWNS = (
    _Breakdown("by_category", "total", ORDER_CATEGORY),
    # K3-A2 (Refs #1770) — **bewusst ohne** Randsumme und ohne Restzeile: Hier
    # zaehlt ``total`` nur die **gemappten** Ereignisse, die Alterstabelle die
    # Ereignisse **mit Personenbezug** — zwei verschiedene Grundgesamtheiten.
    # Die **naive** Variante (Zeile bei unveraenderter Randsumme) oeffnete
    # nachweislich einen neuen exakten Kanal (485 von 840 durchgerechneten
    # Kleinzell-Welten pinnbar, heute 0), weil die Sekundaersuppression
    # voraussetzt, dass die Zeilen sich auf die publizierte Randsumme summieren.
    #
    # Das ist **keine technische Unmoeglichkeit**, sondern ein Produktentscheid
    # — und der ist gefallen: **Option A, Status quo ohne Restzeile**
    # (Maintainer, 2026-08-04, Refs #1773/#1785). Tragend war, dass der W-Kanal
    # aus K3 hier ohnehin nicht existiert (es gibt keine Ereignis-Gesamtsumme,
    # aus der W ableitbar waere) und die Grundgesamtheits-Ausweise aus K3-A3 die
    # Doppeldeutigkeit sichtbar machen. Die beiden Routen der Analyse bleiben
    # fuer eine Wiedervorlage gangbar, werden aber ohne neuen Anlass nicht
    # gebaut: (1) umgestellte Randsumme (``total`` = alle Ereignisse) oder
    # (2) teilmengenbewusste Sekundaersuppression (Rechnen mit
    # ``Σ Zeilen <= Randsumme`` statt ``=``, also ein zweites Inferenzmodell
    # neben dem Partitions-Modell in ``disclosure.py``).
    _Breakdown("by_age_cluster", None, ORDER_AGE_CLUSTER, reveals_person_gap=True),
)


def _person_partition_cells(age_rows: Sequence[dict[str, Any]], threshold: int) -> tuple[Cell, ...]:
    """Zellen der Personen-Partition Σ ``persons`` = ``unique_clients``.

    Die Personenzahlen werden **nie** publiziert, alle Zellen sind also
    unbekannt. Bekannt ist dem Empfaenger dennoch etwas: jede gelistete
    Altersgruppe traegt mindestens eine Person (Zeilen-Existenz), und aus der
    Sichtbarkeit bzw. dem Unterdrueckungsgrund folgt die Schranke gegen *k*.
    """
    cells = []
    for index, row in enumerate(age_rows):
        persons = row.get(PERSONS_KEY)
        if persons is None:
            # Unbekannte Personenzahl (Alt-Snapshot, retention-bereinigt) —
            # keine Schranke ausser der Zeilen-Existenz.
            cells.append(Cell(key=str(index), published=None, lo=1, hi=None))
        elif persons < threshold:
            cells.append(Cell(key=str(index), published=None, lo=1, hi=threshold - 1))
        else:
            cells.append(Cell(key=str(index), published=None, lo=threshold, hi=None))
    return tuple(cells)


def drop_person_margin_on_person_suppression(
    report: dict[str, Any],
    age_rows: Sequence[dict[str, Any]],
    threshold: int,
    *,
    count_key: str = PERSONS_KEY,
    margin_key: str = PERSON_MARGIN_KEY,
) -> None:
    """total-Doktrin fuer die Personen-Randsumme (Auflage 1 zu K0-b, Refs #1770).

    K0-b hat eine neue Identitaet erzeugt: ``by_age_cluster`` partitioniert
    dieselbe Personenmenge, die ``unique_clients`` zaehlt, also gilt
    Σ ``persons`` = ``unique_clients`` — eine publizierte Randsumme ueber
    **Personen** mit derselben Rueckrechenbarkeit wie K1 ueber Ereignisse, nur
    ohne die Unschaerfe der Ereignis-/Personen-Ungleichung. Da eine
    personenbedingt unterdrueckte Zelle zusaetzlich ``persons`` <= *k*−1 verraet
    und jede gelistete Zeile ``persons`` >= 1 traegt, kollabiert die Partition an
    den Raendern (bei *k* = 5, ``unique_clients`` = 5 und vier gelisteten,
    saemtlich unterdrueckten Altersgruppen ist (2, 1, 1, 1) die **einzige**
    konsistente Partition — drei Gruppen mit exakt einer Person).

    Deshalb faellt ``unique_clients``, sobald **eine** Altersgruppe
    personenbedingt unterdrueckt ist — analog zur bestehenden Randsummen-Regel
    R14 (``total`` < k ⇒ Randsumme selbst unterdruecken). Ohne publizierte
    Randsumme ist die Partition nicht mehr aufloesbar.

    Bewusst schaerfer als der Fixpunkt-Guard: dessen Kriterium ist der exakte
    Pin, waehrend hier schon die *Einengung* auf ein k-schmales Intervall
    genuegt — in der Konstruktion oben hat jede Zelle noch zwei moegliche Werte
    (1 oder 2) und trotzdem steht fest, dass jede Altersgruppe weniger als *k*
    Personen umfasst. Der Guard ist die zweite Linie, nicht der Ersatz.

    **Schluessel-Parameter statt Verdrahtung** (Refs #1754): Die Doktrin haengt
    nicht an den Namen ``persons``/``unique_clients``, sondern an der **Form**
    der Beziehung — eine publizierte Randsumme ueber Personen, die von den
    Zeilen exakt partitioniert wird. Genau diese Form hat auch
    ``ClientMetricsBackend`` (Σ Personen je Altersgruppe = aktive Personen),
    nur unter anderen Schluesseln. ``count_key``/``margin_key`` machen die
    Wiederverwendung moeglich, ohne dass ein Backend die Regel nachbaut: eine
    zweite Kopie waere eine zweite Suppressionslogik, und die verbietet der
    Architektur-Guard aus gutem Grund (sie driftet).

    Die Defaults sind die Bestandsschluessel — alle drei Bestandsaufrufer
    (``build_external_report``, ``suppress_sample_report_1_stats``,
    ``suppress_report_stats``) rufen unveraendert weiter.

    ``age_rows`` sind die **rohen** Zeilen mit ihren Personenzahlen, nicht die
    bereits suppressierten: nach :func:`_suppress_small` ist die Zahl aus der
    Zeile entfernt (sie ist Suppressions-Eingabe, nie Ausgabe), und
    :func:`_below_threshold` faende dann ausnahmslos ``None`` — die Regel liefe
    zwar fail-safe, aber immer, und der Bericht waere ohne Not randsummenlos.
    """
    if any(_below_threshold(row.get(count_key), threshold) for row in age_rows):
        report[margin_key] = None


def _published_partitions(
    report: dict[str, Any],
    breakdowns: Sequence[_Breakdown],
    person_cells: tuple[Cell, ...],
) -> list[Partition]:
    """Die Sicht des Empfaengers auf das fertige Artefakt.

    Bewusst **ohne** Rohdaten: unterdrueckte Zellen tragen hier nur noch die
    Zeilen-Existenz als Untergrenze und keine Obergrenze — genau das, was ein
    Empfaenger sicher weiss, der Primaer- und Sekundaer-Unterdrueckung nicht
    auseinanderhalten kann. Die schaerferen Schranken hat das tabellenlokale
    K2-Audit bereits ausgewertet; hier zaehlt, was **zwischen** den Tabellen
    passiert.
    """
    partitions: list[Partition] = []
    for spec in breakdowns:
        data = report.get(spec.key)
        if data is None:
            continue
        margin = report.get(spec.margin_key) if spec.margin_key else None
        if spec.kind == "stages":
            cells = tuple(Cell(key=str(key), published=value, lo=0, hi=None) for key, value in sorted(data.items()))
        else:
            cells = tuple(
                Cell(
                    key=str(index),
                    published=None if row.get("suppressed") else row.get(spec.count_key),
                    lo=_row_min_value(row),
                )
                for index, row in enumerate(data)
            )
        partitions.append(
            Partition(
                name=spec.key,
                margin_key=spec.margin_key,
                margin=margin,
                cells=cells,
                reveals_person_gap=spec.reveals_person_gap,
            )
        )
    if person_cells:
        partitions.append(
            Partition(
                name=_PERSON_PARTITION,
                margin_key=PERSON_MARGIN_KEY,
                margin=report.get(PERSON_MARGIN_KEY),
                cells=person_cells,
            )
        )
    return partitions


def _suppress_smallest_visible(report: dict[str, Any], spec: _Breakdown) -> bool:
    """Opfert die kleinste sichtbare Zelle einer Aufschluesselung. Erfolg?

    Dieselbe Opferregel wie im tabellenlokalen Audit (User-Entscheid #1770) und
    derselbe wertunabhaengige Zweitschluessel — beide nehmen das Minimum ueber
    ``(Wert, tiebreak)``, sodass bei Wertgleichheit die kanonisch erste Zelle
    faellt. Zwei verschiedene Opferregeln in derselben Suppression waeren zwei
    Angreifermodelle, von denen mindestens eines nicht modelliert ist.
    """
    data = report.get(spec.key)
    if not data:
        return False
    if spec.kind == "stages":
        visible = {key: value for key, value in data.items() if value is not None}
        if not visible:
            return False
        report[spec.key] = {**data, min(sorted(visible), key=lambda key: visible[key]): None}
        return True
    visible_indices = [index for index, row in enumerate(data) if not row.get("suppressed")]
    if not visible_indices:
        return False
    tiebreak = _order_tiebreak(data, spec.order)
    victim = min(visible_indices, key=lambda index: (data[index][spec.count_key], tiebreak(index)))
    data[victim][spec.count_key] = None
    data[victim]["suppressed"] = True
    # Der Guard laeuft NACH der Kanonisierung — die neue Unterdrueckung muss
    # sonst als letzte Zeile stehen bleiben und waere selbst eine Rangaussage.
    report[spec.key] = _canonicalize_suppressed_order(data, spec.order)
    return True


def apply_residual_guard(
    report: dict[str, Any],
    threshold: int,
    breakdowns: Sequence[_Breakdown],
    *,
    age_rows: Sequence[dict[str, Any]] = (),
) -> None:
    """K1 (Refs #1770): Fixpunkt-Guard gegen Randsummen-Rueckrechnung.

    Fuehrt die Empfaenger-Inferenz selbst aus und unterdrueckt nach, solange
    sich eine unterdrueckte Zelle exakt rekonstruieren laesst. Eskalationsleiter
    je Befund:

    1. die **kleinste** sichtbare Zelle der betroffenen Aufschluesselung fallen
       lassen (dieselbe Opferregel wie das tabellenlokale Audit, siehe
       :func:`_audit_feasible_span`),
    2. ist keine sichtbare Zelle mehr da: die **Randsumme** fallen lassen —
       ohne sie gibt es keine Gleichung mehr,
    3. hilft auch das nicht (oder ist die harte Iterationsgrenze erreicht):
       fail-safe die **ganze Aufschluesselung** fallen lassen.

    Terminierung: jeder Schritt der Leiter unterdrueckt mindestens eine weitere
    Zelle oder Randsumme und ist monoton; zusaetzlich begrenzt
    ``MAX_GUARD_ITERATIONS`` die Schleife hart.

    **Nur wirksam gemeinsam mit K5** (kanonische Ordnung unterdrueckter Zeilen):
    ohne sie ist der Guard zirkulaer validiert, weil die Zeilenposition selbst
    eine Ordnungsaussage bleibt — Ground-Truth der Analyse: 24 von 853 Sichten
    behalten mit Guard **allein** einen exakten Pin, mit K5 davor 0 von 516.
    """
    person_cells = _person_partition_cells(age_rows, threshold) if report.get("by_age_cluster") is not None else ()
    specs = {spec.key: spec for spec in breakdowns}
    for _ in range(MAX_GUARD_ITERATIONS):
        partitions = _published_partitions(report, breakdowns, person_cells)
        current = findings(partitions, threshold=threshold)
        if not current:
            return
        if not _escalate(report, specs, current):
            break
    _drop_breakdowns(report, breakdowns)


def _escalate(report: dict[str, Any], specs: dict[str, _Breakdown], current: list[tuple[str, str]]) -> bool:
    """Ein Schritt der Eskalationsleiter. Gibt zurueck, ob etwas passiert ist."""
    for partition_name, _cell in current:
        spec = specs.get(partition_name)
        if spec is not None and _suppress_smallest_visible(report, spec):
            return True
        margin_key = spec.margin_key if spec is not None else PERSON_MARGIN_KEY
        if margin_key and report.get(margin_key) is not None:
            report[margin_key] = None
            return True
    return False


def _drop_breakdowns(report: dict[str, Any], breakdowns: Sequence[_Breakdown]) -> None:
    """Fail-safe: im Zweifel faellt die ganze Aufschluesselung samt Randsumme.

    Key weg, nicht nur Werte auf ``None`` — eine bloss genullte Zeile ist selbst
    noch die Aussage „in dieser Gruppe gibt es mindestens einen Fall" (dieselbe
    Begruendung wie in :func:`_drop_person_derived_breakdowns`).
    """
    for spec in breakdowns:
        report.pop(spec.key, None)
        if spec.margin_key:
            report[spec.margin_key] = None
    if PERSON_MARGIN_KEY in report:
        report[PERSON_MARGIN_KEY] = None


def _published_if_above(value: int | None, threshold: int) -> int | None:
    """Security R14: Eine Randsumme < *k* ist selbst eine Kleinstfallzahl → ``None``.

    Vier Aufrufer wiederholten dieselbe Regel in zwei Varianten — mit und ohne
    ``is not None``-Guard. ``value`` ist an zwei Stellen (``build_external_report``,
    ``suppress_single_margin``) immer ein ``.count()``-Ergebnis, also nie
    ``None``; der Guard ist dort ein No-Op. An den anderen beiden
    (``suppress_sample_report_1_stats``, ``suppress_report_stats``) kommt der
    Wert aus einem bereits gebauten Stats-Dict und **kann** ``None`` sein —
    dort ist der Guard notwendig. Eine einzige, defensive Fassung deckt beide
    Faelle ab, ohne fuer die ersten beiden Aufrufer etwas zu aendern.
    """
    return value if value is not None and value >= threshold else None


def _finalize_report(
    report: dict[str, Any],
    unique_clients: int | None,
    age_rows: Sequence[dict[str, Any]],
    threshold: int,
    breakdowns: Sequence[_Breakdown],
) -> dict[str, Any]:
    """K0-Fail-safe-Abschluss + K1-Residual-Guard — 3x identisch kopiert.

    ``build_external_report``, ``suppress_sample_report_1_stats`` und
    ``suppress_report_stats`` schlossen alle drei mit derselben Reihenfolge ab:
    erst die Personen-abgeleiteten Tabellen faellen lassen (K0-Fail-safe, Refs
    #1768), dann die Personen-Randsumme bei Personen-Suppression (Refs #1770),
    dann als letzter Schritt der Residual-Guard (K1, Refs #1770). Mutiert
    ``report`` in place und gibt es zurueck (Aufrufer schreiben ``return
    _finalize_report(...)``).
    """
    _drop_person_derived_breakdowns(report, unique_clients, threshold)
    drop_person_margin_on_person_suppression(report, age_rows, threshold)
    apply_residual_guard(report, threshold, breakdowns, age_rows=age_rows)
    return report


def build_external_report(facility, date_from: date, date_to: date) -> dict[str, Any]:
    """Baut einen datenschutzfreundlichen externen Bericht.

    Reuses ``statistics.get_statistics()``, entfernt ``top_clients`` und
    wendet die K-Anon-Schwelle aus ``Settings.k_anonymity_threshold`` an.
    Fuegt einen ``metadata``-Block mit Datenschutzprofil-Informationen hinzu.
    """
    threshold = _get_threshold(facility)
    raw = get_statistics(facility, date_from, date_to)

    # Security R14: Randsumme < k ist selbst eine Kleinstfallzahl — und was
    # nicht publiziert wird, kann das K2-Audit auch nicht als Gleichung nutzen.
    published_total = _published_if_above(raw["total_contacts"], threshold)

    # by_document_type + by_age_cluster durch K-Anon-Filter
    by_document_type = _suppress_small(
        raw["by_document_type"], threshold, margin=published_total, order=ORDER_DOCUMENT_TYPE
    )
    age_rows = require_person_counts(raw["by_age_cluster"])
    # K3-A1 (Refs #1770): Mit der Restzeile ist die Alterstabelle eine exakte
    # Partition von ``total_contacts`` und deklariert deshalb diese Randsumme
    # (vorher ``margin=None``, das Audit lief dort gar nicht). ``age_rows``
    # bleibt bewusst OHNE Restzeile — es ist die Eingabe der **Personen**-
    # Randsumme Σ ``persons`` = ``unique_clients``, in der die personenlosen
    # Ereignisse nichts zu suchen haben.
    by_age_cluster = _suppress_small(
        with_no_person_row(age_rows, raw["total_contacts"]),
        threshold,
        margin=published_total,
        order=ORDER_AGE_CLUSTER,
    )
    by_contact_stage = _suppress_stage_dict(
        raw["by_contact_stage"], threshold, persons=raw.get("persons_by_contact_stage"), margin=published_total
    )

    report = {
        "total_contacts": published_total,
        "unique_clients": _published_if_above(raw["unique_clients"], threshold),
        "by_contact_stage": by_contact_stage,
        "by_document_type": by_document_type,
        "by_age_cluster": by_age_cluster,
        "metadata": {
            "facility": facility.name,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "k_anonymity_threshold": threshold,
            "generated_at": timezone.now().isoformat(),
            "privacy_profile": "external",
            # Quellenausweis (Refs #1743): dieser Bericht liest live ueber
            # ``get_statistics`` — anders als das hybrid gespeiste
            # Sachbericht-PDF. ``data_source`` ist der stabile Code fuer
            # Maschinen (``?format=json``), ``data_source_note`` die
            # uebersetzte Kurzform fuer den HTML-Kopf. Der Cast haelt den
            # JSON-Pfad frei von Lazy-Proxys.
            "data_source": LIVE,
            "data_source_note": str(DATA_SOURCE_NOTES[LIVE]),
            # K3-A3 (Refs #1770): Grundgesamtheit je Tabelle, als **stabiler
            # Code** — die uebersetzte Fassung steht im HTML, der Code hier ist
            # das, worauf ein JSON-Konsument sich verlassen kann (dieselbe
            # Trennung wie bei ``data_source``). ``by_age_cluster`` zaehlt seit
            # der K3-A1-Restzeile ebenfalls alle Ereignisse des Zeitraums.
            "populations": {
                "total_contacts": ALL_EVENTS,
                "unique_clients": PERSONS,
                "by_contact_stage": ALL_EVENTS,
                "by_document_type": ALL_EVENTS,
                "by_age_cluster": ALL_EVENTS,
            },
        },
    }
    # K0-Fail-safe (Refs #1768) — nach dem Aufbau, damit der Guard genau eine
    # Stelle hat und beide Personen-Tabellen gemeinsam trifft.
    return _finalize_report(report, raw["unique_clients"], age_rows, threshold, _EXTERNAL_REPORT_BREAKDOWNS)


def build_shared_document_type_counts(facility, date_from: date, date_to: date) -> dict[str, int | None]:
    """Intersect type and category decisions before publishing event lower bounds.

    A quantity contributor is an event, not a quantity unit. All types belonging
    to a hidden sample-report category must remain hidden, even if the broader
    general-figures population permits their counts. A visible category total
    must not unlock a hidden member type either: block all its type lower bounds.
    Include the hybrid PDF
    decision as well as live categories; combining either file must stay safe.
    """
    from core.models import DocumentType
    from core.reporting.registry import get_backend

    general = build_external_report(facility, date_from, date_to)
    counts = {row["name"]: row["count"] for row in general.get("by_document_type", [])}
    sample = get_backend("sample-report-1")
    binding = sample.typ_bindung
    if binding is None:
        return dict.fromkeys(counts)
    categories = {
        row["name"]: binding.resolve_row(row)
        for row in DocumentType.objects.for_facility(facility).values("name", *binding.values_fields())
        if row["name"] in counts
    }
    if not any(category is not None for category in categories.values()):
        return counts
    blocked_categories = {
        category for name, category in categories.items() if category is not None and counts[name] is None
    }
    for format_name in ("json", "pdf"):
        raw = sample.build_format_dataset(facility, date_from, date_to, format=format_name)
        protected = suppress_sample_report_1_stats(facility, raw.summary)
        published = {row["name"]: row["count"] for row in protected.get("by_category", [])}
        blocked_categories.update(
            category for category in categories.values() if category is not None and published.get(category) is None
        )
    for name, category in categories.items():
        if category in blocked_categories:
            counts[name] = None
    return counts


def suppress_single_margin(
    facility, result, *, breakdown: _Breakdown, field_specs: Sequence[Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """K-Anon-Suppression fuer Backends mit GENAU einer Randsumme (Refs #1835).

    Kapselt die 14 byte-identischen Zeilen, die ``ExampleBackend.prepare_export``
    und ``MonthlyTrendBackend.prepare_export`` bis auf einen Kommentar Wort fuer
    Wort wiederholten: Schwelle ermitteln, Randsumme gegen R14 pruefen, Zeilen
    ueber :func:`_suppress_small` unterdruecken (inkl. Machbarkeits-Audit K2
    gegen die **publizierte** Randsumme), :func:`apply_residual_guard` (K1) als
    letzter Schritt, Rueckprojektion auf die deklarierten FieldSpec-Keys.

    Gilt NUR fuer Backends mit genau einer Randsumme (``result.summary["total"]``)
    UND genau einer Aufschluesselung, die sie exakt partitioniert. Backends mit
    mehreren zusammenhaengenden Kennzahlen (z. B. ``ClientMetricsBackend``,
    ``SampleReport1Backend``) oder ganz ohne Randsumme (``CaseMetricsBackend``,
    ``BanOverviewBackend``) bauen ihre Suppression weiterhin selbst — eine
    zweite Suppressions-Basisklasse/-Helferfunktion fuer diese Faelle gibt es
    bewusst nicht (der Architektur-Guard
    ``test_kein_artefakt_baut_eigene_unterdrueckung`` erlaubt genau EINE
    Suppressionsstelle; diese Funktion lebt deshalb hier im zentralen Modul,
    nicht als Basisklasse in ``core/reporting/base.py``).

    ``order`` ist bewusst kein Parameter, sondern fest :data:`ORDER_INPUT`:
    beide heutigen Aufrufer sortieren schon wertunabhaengig, und ein
    Namens-Literal an der Aufrufstelle (statt ``breakdown.order``) haelt den
    ``test_order_kommt_aus_der_zentralen_liste``-Guard zufrieden, der an der
    ``_suppress_small``-Aufrufstelle einen AST-``Name`` erwartet.

    Rueckgabe: ``(records, summary)`` — der Aufrufer setzt ``completeness``
    selbst (K6: manche Berichte geben einen datenunabhaengigen Hinweis mit,
    andere gar keinen) und ruft ``dataclasses.replace``.
    """
    threshold = _get_threshold(facility)
    total = result.summary["total"]
    # Security R14: Randsumme < k ist selbst eine Kleinstfallzahl.
    published_total = _published_if_above(total, threshold)
    view: dict[str, Any] = {
        "total": published_total,
        "records": _suppress_small(result.records, threshold, margin=published_total, order=ORDER_INPUT),
    }
    apply_residual_guard(view, threshold, (breakdown,))

    field_keys = [spec.key for spec in field_specs]
    records = [{key: row[key] for key in field_keys} for row in view.get("records", [])]

    summary = dict(result.summary)
    summary["total"] = view["total"]
    summary["k_anonymity_threshold"] = threshold
    return records, summary


def suppress_sample_report_1_stats(facility, stats: dict[str, Any]) -> dict[str, Any]:
    """K-Anon-Kleinstfallzahl-Suppression fuer das Sachbericht-PDF (Refs #1278, T1).

    Das Sachbericht-PDF ist das am ehesten extern zirkulierende Artefakt und muss
    derselben Small-Cell-Suppression unterliegen wie :func:`build_external_report`
    — bisher lief die Suppression nur im On-Screen-Bericht, das PDF gab Roh-
    Kleinstfallzahlen aus.

    - ``by_category`` (Leistungskategorien) und ``by_age_cluster`` (Altersgruppen):
      Zellen < Schwelle werden via :func:`_suppress_small` auf ``count=None`` /
      ``suppressed=True`` gesetzt — inkl. sekundaerer Suppression gegen
      Randsummen-Rueckrechnung.
    - ``unique_clients`` < Schwelle -> ``None`` (wie in ``build_external_report``).
    - ``total`` bleibt als publizierte Randsumme erhalten (analog ``total_contacts``)
      — ausser sie liegt selbst unter der Schwelle (R14), dann ist die Randsumme
      selbst eine Kleinstfallzahl und wird ``None``.

    ``by_category`` kommt als ``(name, count)``-Tupel-Liste (aus
    ``SampleReport1Backend.build_dataset(...).summary``) und wird auf Dicts mit ``name``/``count``/
    ``suppressed`` normalisiert, damit das Template den Unterdrueckungs-Marker
    rendern kann. Gibt ein neues Dict zurueck; die Eingabe wird nicht mutiert.
    """
    threshold = _get_threshold(facility)

    unique = stats.get("unique_clients", 0)
    total = stats.get("total", 0)
    # Security R14: Randsumme < k ist selbst eine Kleinstfallzahl.
    published_total = _published_if_above(total, threshold)

    category_rows = [{"name": name, "count": count} for name, count in stats.get("by_category", [])]
    # ``total`` akkumuliert exakt dieselben Zeilen wie ``by_category`` (die
    # Konsolidierung ueber den ``report_key`` zaehlt nur gemappte Ereignisse) —
    # eine exakte Partition und damit eine echte Rueckrechnungs-Gleichung.
    #
    # ``ORDER_CATEGORY`` (K5, Refs #1770): die Einfuegereihenfolge der
    # Konsolidierung ist **datenabhaengig** — eine Kategorie steht dort, wo ihr
    # alphabetisch erster im Zeitraum vorhandener ``report_key`` steht. Ohne
    # kanonische Ordnung verriete die Zeilenposition, welcher Berichtsschluessel
    # hinter einer unterdrueckten Kleinstzelle steckt.
    by_category = _suppress_small(category_rows, threshold, margin=published_total, order=ORDER_CATEGORY)
    age_rows = require_person_counts(list(stats.get("by_age_cluster", [])))
    # K3-A2 (Refs #1770): HIER bleibt es bei ``margin=None`` und **ohne**
    # Restzeile. ``total`` zaehlt nur gemappte Ereignisse, die Alterstabelle
    # Ereignisse mit Personenbezug — zwei verschiedene Grundgesamtheiten. Eine
    # Restzeile bei unveraenderter Randsumme oeffnete nachweislich einen neuen
    # exakten Kanal; Begruendung an ``_SAMPLE_REPORT_1_BREAKDOWNS``.
    by_age_cluster = _suppress_small(age_rows, threshold, margin=None, order=ORDER_AGE_CLUSTER)

    out = {
        **stats,
        "by_category": by_category,
        "by_age_cluster": by_age_cluster,
        "unique_clients": _published_if_above(unique, threshold),
        "total": published_total,
    }
    # K0-Fail-safe (Refs #1768): hier gibt es nur ``by_age_cluster`` — der
    # Beispiel-Sachbericht fuehrt keine Kontaktstufen.
    return _finalize_report(out, unique, age_rows, threshold, _SAMPLE_REPORT_1_BREAKDOWNS)


def suppress_report_stats(facility, stats: dict[str, Any]) -> dict[str, Any]:
    """K-Anon-Kleinstfallzahl-Suppression fuer den Halbjahres-Sachbericht.

    Security R4: Das Sachbericht-PDF (Standard-Modus = externes Artefakt fuer
    Traeger/Foerderer, Refs #792) lieferte rohe Kleinstfallzahlen, waehrend
    Sachbericht-PDF (#1278) und On-Screen-Bericht laengst unterdruecken.
    Gleiche Semantik wie ``build_external_report``: Zellen < Schwelle werden
    ``count=None``/``suppressed=True`` (inkl. sekundaerer Suppression),
    ``by_contact_stage`` ueber die Dict-Variante, ``unique_clients`` < k -> None.
    Nicht mutierend — gibt eine Kopie zurueck.
    """
    threshold = _get_threshold(facility)
    out = dict(stats)
    total = stats.get("total_contacts", 0)
    # Security R14: Randsumme < k ist selbst eine Kleinstfallzahl.
    published_total = _published_if_above(total, threshold)
    out["by_document_type"] = _suppress_small(
        list(stats.get("by_document_type", [])), threshold, margin=published_total, order=ORDER_DOCUMENT_TYPE
    )
    age_rows = require_person_counts(list(stats.get("by_age_cluster", [])))
    # K3-A1 (Refs #1770) — siehe ``build_external_report``: Restzeile aus dem
    # ROHEN ``total_contacts`` (nicht aus ``published_total``, das R14 schon
    # genullt haben kann) und Randsummen-Deklaration.
    out["by_age_cluster"] = _suppress_small(
        with_no_person_row(age_rows, total),
        threshold,
        margin=published_total,
        order=ORDER_AGE_CLUSTER,
    )
    out["by_contact_stage"] = _suppress_stage_dict(
        dict(stats.get("by_contact_stage", {})),
        threshold,
        persons=stats.get("persons_by_contact_stage"),
        margin=published_total,
    )
    # Die Personenzahlen sind Suppressions-Eingabe, kein Berichtsinhalt — sie
    # duerfen den Wrapper nicht verlassen (K0-b, Refs #1770).
    out.pop("persons_by_contact_stage", None)
    unique = stats.get("unique_clients", 0)
    out["unique_clients"] = _published_if_above(unique, threshold)
    out["total_contacts"] = published_total
    # K0-Fail-safe (Refs #1768): Altersgruppen UND Kontaktstufen sind
    # personenabgeleitet und fallen gemeinsam mit der Personenzahl.
    return _finalize_report(out, unique, age_rows, threshold, _EXTERNAL_REPORT_BREAKDOWNS)
