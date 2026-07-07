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

from core.models import Settings
from core.services.dashboard.statistics import get_statistics

DEFAULT_K_THRESHOLD = 5


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


def _suppress_small(rows: list[dict[str, Any]], threshold: int, count_key: str = "count") -> list[dict[str, Any]]:
    """Markiert Aggregate < threshold als unterdrueckt (count=None, suppressed=True)."""
    result = []
    for row in rows:
        row_copy = dict(row)
        original_count = row_copy.get(count_key, 0)
        if original_count < threshold:
            row_copy[count_key] = None
            row_copy["suppressed"] = True
        else:
            row_copy["suppressed"] = False
        result.append(row_copy)
    _apply_secondary_suppression(result, count_key)
    return result


def _suppress_stage_dict(stage_dict: dict[str, int], threshold: int) -> dict[str, Any]:
    """Suppress-Variante fuer den by_contact_stage-Dict (mit sekundaerer
    Suppression analog ``_apply_secondary_suppression``, A6.1)."""
    suppressed_keys = {key for key, value in stage_dict.items() if value < threshold}
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
    by_age_cluster = _suppress_small(raw["by_age_cluster"], threshold)
    by_contact_stage = _suppress_stage_dict(raw["by_contact_stage"], threshold)

    return {
        "total_contacts": raw["total_contacts"],
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
        },
    }


def suppress_jugendamt_stats(facility, stats: dict[str, Any]) -> dict[str, Any]:
    """K-Anon-Kleinstfallzahl-Suppression fuer das Jugendamt-PDF (Refs #1278, T1).

    Das Jugendamt-PDF ist das am ehesten extern zirkulierende Artefakt und muss
    derselben Small-Cell-Suppression unterliegen wie :func:`build_external_report`
    — bisher lief die Suppression nur im On-Screen-Bericht, das PDF gab Roh-
    Kleinstfallzahlen aus.

    - ``by_category`` (Leistungskategorien) und ``by_age_cluster`` (Altersgruppen):
      Zellen < Schwelle werden via :func:`_suppress_small` auf ``count=None`` /
      ``suppressed=True`` gesetzt — inkl. sekundaerer Suppression gegen
      Randsummen-Rueckrechnung.
    - ``unique_clients`` < Schwelle -> ``None`` (wie in ``build_external_report``).
    - ``total`` bleibt als publizierte Randsumme erhalten (analog ``total_contacts``).

    ``by_category`` kommt als ``(name, count)``-Tupel-Liste (aus
    ``get_jugendamt_statistics``) und wird auf Dicts mit ``name``/``count``/
    ``suppressed`` normalisiert, damit das Template den Unterdrueckungs-Marker
    rendern kann. Gibt ein neues Dict zurueck; die Eingabe wird nicht mutiert.
    """
    threshold = _get_threshold(facility)

    category_rows = [{"name": name, "count": count} for name, count in stats.get("by_category", [])]
    by_category = _suppress_small(category_rows, threshold, count_key="count")
    by_age_cluster = _suppress_small(list(stats.get("by_age_cluster", [])), threshold, count_key="count")

    unique = stats.get("unique_clients", 0)
    return {
        **stats,
        "by_category": by_category,
        "by_age_cluster": by_age_cluster,
        "unique_clients": unique if unique >= threshold else None,
    }


def suppress_report_stats(facility, stats: dict[str, Any]) -> dict[str, Any]:
    """K-Anon-Kleinstfallzahl-Suppression fuer den Halbjahres-Sachbericht.

    Security R4: Das Sachbericht-PDF (Standard-Modus = externes Artefakt fuer
    Traeger/Foerderer, Refs #792) lieferte rohe Kleinstfallzahlen, waehrend
    Jugendamt-PDF (#1278) und On-Screen-Bericht laengst unterdruecken.
    Gleiche Semantik wie ``build_external_report``: Zellen < Schwelle werden
    ``count=None``/``suppressed=True`` (inkl. sekundaerer Suppression),
    ``by_contact_stage`` ueber die Dict-Variante, ``unique_clients`` < k -> None.
    Nicht mutierend — gibt eine Kopie zurueck.
    """
    threshold = _get_threshold(facility)
    out = dict(stats)
    out["by_document_type"] = _suppress_small(list(stats.get("by_document_type", [])), threshold)
    out["by_age_cluster"] = _suppress_small(list(stats.get("by_age_cluster", [])), threshold)
    out["by_contact_stage"] = _suppress_stage_dict(dict(stats.get("by_contact_stage", {})), threshold)
    unique = stats.get("unique_clients", 0)
    out["unique_clients"] = unique if unique is not None and unique >= threshold else None
    return out
