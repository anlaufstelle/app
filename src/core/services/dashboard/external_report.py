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

from datetime import date
from typing import Any

from django.utils import timezone

from core.models import Client, Settings
from core.reporting.data_source import DATA_SOURCE_NOTES, LIVE
from core.services.dashboard.statistics import PERSONS_KEY, get_statistics

DEFAULT_K_THRESHOLD = 5

# Modellreihenfolge der Altersgruppen — die kanonische, wertunabhaengige
# Ordnung fuer unterdrueckte ``by_age_cluster``-Zeilen (K5, Refs #1767). Aus
# ``Client.AgeCluster`` abgeleitet statt hier dupliziert: eine neue Altersgruppe
# soll die Ordnung automatisch erben.
_AGE_CLUSTER_ORDER = {value: index for index, value in enumerate(Client.AgeCluster.values)}


def _age_cluster_key(row: dict[str, Any]) -> tuple[int, str]:
    """Altersgruppen in Modellreihenfolge; Unbekanntes stabil ans Ende."""
    cluster = row.get("cluster")
    return (_AGE_CLUSTER_ORDER.get(cluster, len(_AGE_CLUSTER_ORDER)), str(cluster or ""))


def _document_type_key(row: dict[str, Any]) -> tuple[str, str]:
    """Dokumenttypen nach ``(category, name)`` — beides wertunabhaengig."""
    return (str(row.get("category") or ""), str(row.get("name") or ""))


# Erkennungsfeld -> kanonischer Sortierschluessel. Die Zuordnung laeuft ueber die
# **Zeilenform**, nicht ueber die Aufrufstelle: so erbt jedes Artefakt, das
# dieselben Zeilen durch ``_suppress_small`` schickt (Templates, JSON, XML,
# PDF-Pfad, Hybrid-/Snapshot-Pfad), die Ordnung automatisch.
#
# Bewusst NICHT erfasst: ``by_category`` des Beispiel-Sachberichts (Zeilen mit
# ``name``/``count``). Diese Liste entsteht in fester Kategorien-Ordnung
# (Konsolidierung ueber ``system_type``), nicht nach Anzahl — ihre Position ist
# keine Rangaussage, eine Umsortierung braechte keinen Schutz.
_CANONICAL_ROW_KEYS = (
    ("cluster", _age_cluster_key),
    ("category", _document_type_key),
)


def _get_threshold(facility) -> int:
    """Holt die K-Anon-Schwelle aus den Facility-Settings, fallback auf Default."""
    try:
        settings = Settings.objects.get(facility=facility)
        return settings.k_anonymity_threshold or DEFAULT_K_THRESHOLD
    except Settings.DoesNotExist:
        return DEFAULT_K_THRESHOLD


def _apply_secondary_suppression(rows: list[dict[str, Any]], count_key: str) -> None:
    """A6.1 (Refs #1024 / #1016): komplementaere Offenlegung verhindern.

    Ist nach der primaeren k-Anon-Unterdrueckung genau EINE Zelle unterdrueckt,
    laesst sie sich aus der publizierten Randsumme (z.B. ``total_contacts``) und
    den sichtbaren Zellen zurueckrechnen. Dann zusaetzlich die naechstkleinere
    sichtbare Zelle unterdruecken, sodass mindestens zwei Unbekannte bleiben.
    Mutiert ``rows`` in place.
    """
    suppressed = [r for r in rows if r.get("suppressed")]
    if len(suppressed) != 1:
        return
    visible = [r for r in rows if not r.get("suppressed")]
    if not visible:
        return
    smallest = min(visible, key=lambda r: r[count_key])
    smallest[count_key] = None
    smallest["suppressed"] = True


def _canonicalize_suppressed_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    Zeilenformen ohne definierte kanonische Ordnung bleiben unveraendert (siehe
    ``_CANONICAL_ROW_KEYS``).
    """
    if not rows:
        return rows
    for field, key in _CANONICAL_ROW_KEYS:
        if all(field in row for row in rows):
            visible = [row for row in rows if not row.get("suppressed")]
            hidden = sorted((row for row in rows if row.get("suppressed")), key=key)
            return [*visible, *hidden]
    return rows


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


def _suppress_small(rows: list[dict[str, Any]], threshold: int, count_key: str = "count") -> list[dict[str, Any]]:
    """Markiert Aggregate < threshold als unterdrueckt (count=None, suppressed=True).

    Das Kriterium ist seit K0-b (Refs #1770) personenbasiert, wo die Zeile eine
    DISTINCT-Personenzahl mitfuehrt (siehe :func:`_is_below_threshold`). Diese
    Zahl ist reine Suppressions-*Eingabe* und wird hier aus **jeder**
    Ausgabezeile entfernt — sichtbar wie unterdrueckt. Bliebe sie stehen, waere
    sie ueber JSON-/XML-Export selbst ein Kleinstfallzahl-Kanal (eine Zelle
    „27+: 30 Ereignisse, 6 Personen" publiziert eine Personenzahl, die nie
    durch eine Suppressionsregel gelaufen ist).
    """
    result = []
    for row in rows:
        row_copy = dict(row)
        below = _is_below_threshold(row_copy, count_key, threshold)
        row_copy.pop(PERSONS_KEY, None)
        if below:
            row_copy[count_key] = None
            row_copy["suppressed"] = True
        else:
            row_copy["suppressed"] = False
        result.append(row_copy)
    _apply_secondary_suppression(result, count_key)
    return _canonicalize_suppressed_order(result)


# Aufschluesselungen, deren Aequivalenzklassen **Personen** sind (nicht
# Ereignisse) — sie fallen gemeinsam mit der Personenzahl (K0, Refs #1768).
_PERSON_DERIVED_KEYS = ("by_age_cluster", "by_contact_stage")


def _drop_person_derived_breakdowns(report: dict[str, Any], unique_clients: int | None, threshold: int) -> None:
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
    """
    if unique_clients is not None and unique_clients >= threshold:
        return
    for key in _PERSON_DERIVED_KEYS:
        report.pop(key, None)


def _suppress_stage_dict(
    stage_dict: dict[str, int], threshold: int, *, persons: dict[str, int | None] | None
) -> dict[str, Any]:
    """Suppress-Variante fuer den by_contact_stage-Dict (mit sekundaerer
    Suppression analog ``_apply_secondary_suppression``, A6.1).

    ``persons`` ist die DISTINCT-Personenzahl je Stufe (K0-b, Refs #1770) und
    bewusst ein **Pflicht**-Keyword ohne brauchbaren Default: ein fehlender
    Eintrag — oder ein ``persons=None`` insgesamt — bedeutet „unbekannt" und
    unterdrueckt die Stufe fail-safe. Ein optionaler Parameter haette genau die
    Alt-Format-Luecke offen gelassen, die er schliessen soll.
    """
    person_counts = persons or {}
    suppressed_keys = {
        key
        for key, value in stage_dict.items()
        if _below_threshold(value, threshold) or _below_threshold(person_counts.get(key), threshold)
    }
    if len(suppressed_keys) == 1:
        visible = {key: value for key, value in stage_dict.items() if key not in suppressed_keys}
        if visible:
            suppressed_keys.add(min(visible, key=lambda k: visible[k]))
    return {key: (None if key in suppressed_keys else value) for key, value in stage_dict.items()}


def build_external_report(facility, date_from: date, date_to: date) -> dict[str, Any]:
    """Baut einen datenschutzfreundlichen externen Bericht.

    Reuses ``statistics.get_statistics()``, entfernt ``top_clients`` und
    wendet die K-Anon-Schwelle aus ``Settings.k_anonymity_threshold`` an.
    Fuegt einen ``metadata``-Block mit Datenschutzprofil-Informationen hinzu.
    """
    threshold = _get_threshold(facility)
    raw = get_statistics(facility, date_from, date_to)

    # by_document_type + by_age_cluster durch K-Anon-Filter
    by_document_type = _suppress_small(raw["by_document_type"], threshold)
    by_age_cluster = _suppress_small(require_person_counts(raw["by_age_cluster"]), threshold)
    by_contact_stage = _suppress_stage_dict(
        raw["by_contact_stage"], threshold, persons=raw.get("persons_by_contact_stage")
    )

    report = {
        # Security R14: Randsumme < k ist selbst eine Kleinstfallzahl.
        "total_contacts": raw["total_contacts"] if raw["total_contacts"] >= threshold else None,
        "unique_clients": raw["unique_clients"] if raw["unique_clients"] >= threshold else None,
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
        },
    }
    # K0-Fail-safe (Refs #1768) — nach dem Aufbau, damit der Guard genau eine
    # Stelle hat und beide Personen-Tabellen gemeinsam trifft.
    _drop_person_derived_breakdowns(report, raw["unique_clients"], threshold)
    return report


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

    category_rows = [{"name": name, "count": count} for name, count in stats.get("by_category", [])]
    by_category = _suppress_small(category_rows, threshold, count_key="count")
    by_age_cluster = _suppress_small(
        require_person_counts(list(stats.get("by_age_cluster", []))), threshold, count_key="count"
    )

    unique = stats.get("unique_clients", 0)
    total = stats.get("total", 0)
    out = {
        **stats,
        "by_category": by_category,
        "by_age_cluster": by_age_cluster,
        "unique_clients": unique if unique is not None and unique >= threshold else None,
        # Security R14: Randsumme < k ist selbst eine Kleinstfallzahl.
        "total": total if total is not None and total >= threshold else None,
    }
    # K0-Fail-safe (Refs #1768): hier gibt es nur ``by_age_cluster`` — der
    # Beispiel-Sachbericht fuehrt keine Kontaktstufen.
    _drop_person_derived_breakdowns(out, unique, threshold)
    return out


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
    out["by_document_type"] = _suppress_small(list(stats.get("by_document_type", [])), threshold)
    out["by_age_cluster"] = _suppress_small(require_person_counts(list(stats.get("by_age_cluster", []))), threshold)
    out["by_contact_stage"] = _suppress_stage_dict(
        dict(stats.get("by_contact_stage", {})), threshold, persons=stats.get("persons_by_contact_stage")
    )
    # Die Personenzahlen sind Suppressions-Eingabe, kein Berichtsinhalt — sie
    # duerfen den Wrapper nicht verlassen (K0-b, Refs #1770).
    out.pop("persons_by_contact_stage", None)
    unique = stats.get("unique_clients", 0)
    out["unique_clients"] = unique if unique is not None and unique >= threshold else None
    # Security R14: Randsumme < k ist selbst eine Kleinstfallzahl.
    total = stats.get("total_contacts", 0)
    out["total_contacts"] = total if total is not None and total >= threshold else None
    # K0-Fail-safe (Refs #1768): Altersgruppen UND Kontaktstufen sind
    # personenabgeleitet und fallen gemeinsam mit der Personenzahl.
    _drop_person_derived_breakdowns(out, unique, threshold)
    return out
