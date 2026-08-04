"""Snapshot service: create, ensure and retrieve monthly statistics snapshots."""

import calendar
import json
import logging
from datetime import date

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from core.models import DocumentType, StatisticsSnapshot
from core.reporting.data_source import LIVE

# Zyklusfrei: ``core.reporting.registry`` importiert nur ``core.reporting.base``
# (keine Services, keine Models) und laedt das ``backends``-Package erst lazy
# beim ersten ``get_*``-Aufruf.
from core.reporting.registry import get_backend, get_backends
from core.services.dashboard.statistics import PERSONS_KEY, count_unique_clients, get_statistics

logger = logging.getLogger(__name__)

_STAGE_KEYS = ("anonym", "identifiziert", "qualifiziert")


def _merge_person_count(current: int | None, value: int | None) -> int | None:
    """Fuegt zwei Segment-Personenzahlen fail-safe zusammen (K0-b/K4b, Refs #1770).

    **Nie summieren:** Personen sind ueber Monatssegmente nicht additiv — genau
    dieser Fehler ist K4b (3 Personen x 6 Monate ergaben "18"). Das Maximum
    ueber die Segmente ist dagegen immer eine gueltige *Untergrenze* der wahren
    DISTINCT-Zahl (jede Segmentmenge ist Teilmenge der Gesamtmenge) und damit
    als Suppressionskriterium in der sicheren Richtung falsch: hoechstens wird
    zu viel unterdrueckt, nie zu wenig.

    ``None`` bedeutet "unbekannt" und dominiert: sobald ein Segment keine
    Personenzahl mitbringt (Alt-Snapshot vor der Payload-Erweiterung, oder
    Migration ``0128`` konnte sie nicht rekonstruieren), gilt der Bucket als
    unbekannt und wird spaeter fail-safe unterdrueckt.
    """
    if current is None or value is None:
        return None
    return max(current, value)


def create_or_update_snapshot(facility, year, month):
    """Create or update a statistics snapshot for a given facility and month."""
    date_from = date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    date_to = date(year, month, last_day)

    stats = get_statistics(facility, date_from, date_to)

    # M5 P3 (Refs #1061): Roh-Aggregate aller Snapshot-Backends aus der Registry
    # statt eines hart verdrahteten Sachbericht-Aufrufs. Bewusst ``build_dataset``
    # und NICHT ``prepare_export``: der Snapshot ist der interne Rohbestand, die
    # k-Anon-Suppression gehoert allein in den Export-/Panel-Pfad.
    payloads = {
        backend.slug: backend.build_dataset(facility, date_from, date_to).summary
        for backend in get_backends()
        if backend.snapshot_enabled
    }

    # Remove top_clients (not snapshot-safe due to anonymization)
    stats.pop("top_clients", None)

    # Enrich by_document_type with system_type and document_type_id.
    # Bulk-Lookup statt N+1-Loop (Refs #598): eine einzige Query
    # holt alle relevanten DocumentTypes, das Dict-Composite-Key (name,
    # category) stellt sicher, dass nur exakte Treffer zugeordnet werden.
    entries = stats.get("by_document_type", [])
    if entries:
        dt_lookup = {
            (dt.name, dt.category): dt
            for dt in DocumentType.objects.filter(
                facility=facility,
                name__in=[e["name"] for e in entries],
                category__in=[e["category"] for e in entries],
            )
        }
        for entry in entries:
            dt = dt_lookup.get((entry["name"], entry["category"]))
            entry["system_type"] = dt.system_type if dt and dt.system_type else ""
            entry["document_type_id"] = str(dt.id) if dt else ""

    # Force-convert lazy translation strings to plain strings for JSON storage
    stats = json.loads(json.dumps(stats, cls=DjangoJSONEncoder))
    payloads = json.loads(json.dumps(payloads, cls=DjangoJSONEncoder))

    StatisticsSnapshot.objects.update_or_create(
        facility=facility,
        year=year,
        month=month,
        # Refs #1740: Der Dual-Write ins Altfeld des Sachberichts ist beendet —
        # ``backend_data`` ist die einzige Quelle (Migrationen 0123/0124).
        defaults={
            "data": stats,
            "backend_data": payloads,
        },
    )
    logger.info(
        "Snapshot created/updated: facility=%s year=%d month=%d",
        facility.name,
        year,
        month,
    )


def ensure_snapshots_for_months(facility, event_queryset):
    """Create snapshots for all months covered by the given events (excluding current month)."""
    today = timezone.localdate()
    current = (today.year, today.month)

    months = event_queryset.values_list("occurred_at__year", "occurred_at__month").distinct()
    for year, month in months:
        if (year, month) < current:  # Only past, completed months
            create_or_update_snapshot(facility, year, month)


def get_snapshot(facility, year, month):
    """Return snapshot data dict or None if no snapshot exists."""
    snap = StatisticsSnapshot.objects.filter(facility=facility, year=year, month=month).first()
    return snap.data if snap else None


# ---------------------------------------------------------------------------
# Hybrid query helpers
# ---------------------------------------------------------------------------


def _split_into_segments(date_from, date_to):
    """Split a date range into monthly segments.

    Returns a list of ``(seg_from, seg_to, use_snapshot)`` tuples.

    A segment gets ``use_snapshot=True`` when it covers a **full** calendar
    month **and** that month lies strictly before the current month.
    """
    today = timezone.localdate()
    current_ym = (today.year, today.month)

    segments = []
    cursor = date_from

    while cursor <= date_to:
        year, month = cursor.year, cursor.month
        _, last_day = calendar.monthrange(year, month)
        first_of_month = date(year, month, 1)
        last_of_month = date(year, month, last_day)

        seg_from = cursor
        seg_to = min(last_of_month, date_to)

        is_full_month = seg_from == first_of_month and seg_to == last_of_month
        is_past_month = (year, month) < current_ym

        segments.append((seg_from, seg_to, is_full_month and is_past_month))

        # Advance cursor to the first day of the next month
        cursor = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    return segments


def _empty_stats():
    """Return an empty statistics dict matching the ``get_statistics`` shape."""
    return {
        "total_contacts": 0,
        "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
        "persons_by_contact_stage": dict.fromkeys(_STAGE_KEYS),
        "by_document_type": [],
        "by_age_cluster": [],
        # K4b (Refs #1770): der Merge liefert strukturell KEINE Personenzahl —
        # sie ist nicht additiv. Den Wert setzt allein der Hybrid-Pfad als
        # eigenen Live-Term ueber den Gesamtzeitraum.
        "unique_clients": None,
    }


def _merge_stats(stats_list):
    """Merge multiple statistics dicts into one.

    Merge logic per field:
    - ``total_contacts`` – sum
    - ``by_contact_stage`` – sum per key
    - ``by_document_type`` – merge by ``(name, category)`` composite key, sum ``count``
    - ``by_age_cluster`` – merge by ``cluster`` key, sum ``count``, keep labels
    - ``persons`` je Cluster und ``persons_by_contact_stage`` – Maximum, nie
      Summe; unbekannt sobald ein Segment die Zahl nicht mitbringt (K0-b)
    - ``unique_clients`` – **immer** ``None`` (K4b, Refs #1770): Personen sind
      nicht additiv, eine summierte Personenzahl waere ein Suppressions-Bypass.
      Der Hybrid-Pfad setzt stattdessen einen eigenen Live-Term.
    """
    if not stats_list:
        return _empty_stats()

    merged = _empty_stats()

    # Accumulators for list-based fields
    doc_type_map = {}  # (name, category) → count
    age_cluster_map = {}  # cluster → {label, count}
    age_persons = {}  # cluster → int | None (Maximum, None = unbekannt)
    stage_persons = dict.fromkeys(_STAGE_KEYS, 0)

    for stats in stats_list:
        merged["total_contacts"] += stats.get("total_contacts", 0)

        # by_contact_stage
        segment_stage_persons = stats.get("persons_by_contact_stage") or {}
        for key in _STAGE_KEYS:
            merged["by_contact_stage"][key] += stats.get("by_contact_stage", {}).get(key, 0)
            stage_persons[key] = _merge_person_count(stage_persons[key], segment_stage_persons.get(key))

        # by_document_type – merge by (name, category)
        for entry in stats.get("by_document_type", []):
            composite = (entry["name"], entry["category"])
            if composite in doc_type_map:
                doc_type_map[composite]["count"] += entry["count"]
            else:
                doc_type_map[composite] = {**entry}

        # by_age_cluster – merge by cluster
        for entry in stats.get("by_age_cluster", []):
            cluster = entry["cluster"]
            age_persons[cluster] = _merge_person_count(age_persons.get(cluster, 0), entry.get(PERSONS_KEY))
            if cluster in age_cluster_map:
                age_cluster_map[cluster]["count"] += entry["count"]
            else:
                age_cluster_map[cluster] = {**entry}

    merged["persons_by_contact_stage"] = stage_persons
    merged["by_document_type"] = sorted(doc_type_map.values(), key=lambda e: e["count"], reverse=True)
    merged["by_age_cluster"] = _with_merged_persons(age_cluster_map, age_persons)

    return merged


def _with_merged_persons(cluster_map, persons):
    """Altersgruppen-Zeilen mit der zusammengefuehrten Personenzahl (K0-b, Refs #1770).

    Setzt ``persons`` auf **jeder** Zeile — auch wenn kein Segment eine Zahl
    lieferte (dann ``None`` = unbekannt). Die Zeile darf den Merge nie ohne das
    Feld verlassen, sonst faellt der Suppressionspfad still auf das alte,
    ereignisbasierte Kriterium zurueck (fail-open).
    """
    rows = []
    for cluster, row in cluster_map.items():
        rows.append({**row, PERSONS_KEY: persons.get(cluster)})
    return sorted(rows, key=lambda e: e["count"], reverse=True)


def _empty_sample_report_1_stats():
    """Return an empty sample report statistics dict."""
    return {
        "total": 0,
        "by_category": [],
        "by_age_cluster": [],
        # K4b (Refs #1770): siehe ``_empty_stats`` — nie eine Summe.
        "unique_clients": None,
    }


def _merge_sample_report_1_stats(stats_list):
    """Merge multiple sample report statistics dicts.

    Merge logic:
    - ``total`` – sum
    - ``by_category`` – merge by category name, sum count.
      Normalizes both tuples and lists from JSON to output tuples.
    - ``by_age_cluster`` – merge by ``cluster`` key, sum ``count``;
      ``persons`` per Maximum (K0-b, Refs #1770)
    - ``unique_clients`` – **immer** ``None`` (K4b, Refs #1770), der Hybrid-Pfad
      setzt einen eigenen Live-Term ueber den Gesamtzeitraum
    """
    if not stats_list:
        return _empty_sample_report_1_stats()

    merged = _empty_sample_report_1_stats()
    category_map = {}  # name → count
    age_cluster_map = {}  # cluster → {label, count}
    age_persons = {}  # cluster → int | None

    for stats in stats_list:
        merged["total"] += stats.get("total", 0)

        # by_category – normalize tuples/lists from JSON snapshots
        for entry in stats.get("by_category", []):
            name, count = entry[0], entry[1]
            category_map[name] = category_map.get(name, 0) + count

        # by_age_cluster
        for entry in stats.get("by_age_cluster", []):
            cluster = entry["cluster"]
            age_persons[cluster] = _merge_person_count(age_persons.get(cluster, 0), entry.get(PERSONS_KEY))
            if cluster in age_cluster_map:
                age_cluster_map[cluster]["count"] += entry["count"]
            else:
                age_cluster_map[cluster] = {**entry}

    merged["by_category"] = [(name, count) for name, count in category_map.items()]
    merged["by_age_cluster"] = _with_merged_persons(age_cluster_map, age_persons)

    return merged


def get_statistics_hybrid(facility, date_from, date_to):
    """Return statistics for a date range, using snapshots for full past months.

    Segments that cover a complete past calendar month are served from
    pre-computed snapshots (if available), all others fall back to a live
    database query.  ``top_clients`` und ``unique_clients`` werden **immer**
    live ueber den Gesamtzeitraum berechnet — beide lassen sich aus
    Monats-Aggregaten nicht sinnvoll zusammenfuehren.
    """
    segments = _split_into_segments(date_from, date_to)
    segment_stats = []

    for seg_from, seg_to, use_snapshot in segments:
        stats = None
        if use_snapshot:
            stats = get_snapshot(facility, seg_from.year, seg_from.month)
        if stats is None:
            stats = get_statistics(facility, seg_from, seg_to)
            # Remove top_clients from segment stats (merged separately)
            stats.pop("top_clients", None)
        segment_stats.append(stats)

    merged = _merge_stats(segment_stats)

    # top_clients always from a live full-range query
    live_full = get_statistics(facility, date_from, date_to)
    merged["top_clients"] = live_full["top_clients"]

    # K4b (Refs #1770): ``unique_clients`` ist als einziges Feld nicht additiv
    # und wurde bisher ueber die Monatssegmente summiert — 3 Personen mit je
    # einem Kontakt in 6 Monaten ergaben "18", waehrend der Live-Export mit
    # distinct = 3 < k unterdrueckte. Die k-Pruefung lief damit gegen eine
    # aufgeblaehte Zahl (Personen-Monate): ein Suppressions-Bypass. Statt der
    # Summe steht hier ein eigener Live-Term ueber den **Gesamtzeitraum**, und
    # das Artefakt weist genau fuer diesen Term seine abweichende Quelle aus
    # (#1743, Option c: Lesepfade bleiben, Quellen werden ausgewiesen).
    #
    # Bewusst kein Rueckfall auf die Snapshot-Summe fuer retention-bereinigte
    # Alt-Zeitraeume: dort faellt der Live-Wert niedriger aus (bis auf 0), was
    # der Quellen-Ausweis kenntlich macht. Fail-safe-Richtung.
    merged["unique_clients"] = live_full["unique_clients"]
    merged["unique_clients_data_source"] = LIVE

    return merged


def get_statistics_trend(facility, date_from, date_to):
    """Return per-segment statistics for trend visualisation.

    Unlike :func:`get_statistics_hybrid` which merges all segments into one
    aggregated result, this function returns the individual monthly data
    points so they can be rendered as a time-series chart.

    Each entry contains:
    - ``label`` – ``"YYYY-MM"`` month identifier
    - ``source`` – ``"snapshot"`` or ``"live"``
    - The standard statistics fields (``total_contacts``, etc.)
    """
    segments = _split_into_segments(date_from, date_to)
    result = []

    for seg_from, seg_to, use_snapshot in segments:
        stats = None
        source = "live"
        if use_snapshot:
            stats = get_snapshot(facility, seg_from.year, seg_from.month)
            if stats is not None:
                source = "snapshot"
        if stats is None:
            stats = get_statistics(facility, seg_from, seg_to)
            stats.pop("top_clients", None)
        result.append(
            {
                "label": f"{seg_from.year}-{seg_from.month:02d}",
                "source": source,
                **stats,
            }
        )

    return result


def get_sample_report_1_statistics_hybrid(facility, date_from, date_to):
    """Return sample report statistics for a date range, using snapshots where possible.

    Same hybrid pattern as :func:`get_statistics_hybrid` but for the
    ``sample-report-1`` backend data.
    """
    segments = _split_into_segments(date_from, date_to)
    segment_stats = []

    for seg_from, seg_to, use_snapshot in segments:
        stats = None
        if use_snapshot:
            snap = StatisticsSnapshot.objects.filter(
                facility=facility,
                year=seg_from.year,
                month=seg_from.month,
            ).first()
            if snap:
                # M5 P3 (Refs #1061) / #1740: ``backend_data`` ist die einzige
                # Quelle — der Fallback auf das Altfeld ist mit dem Ende der
                # Uebergangsphase entfallen. ``or None`` normalisiert das leere
                # Dict, damit der Live-Zweig unten wirklich greift.
                stats = snap.backend_data.get("sample-report-1") or None
        if stats is None:
            stats = get_backend("sample-report-1").build_dataset(facility, seg_from, seg_to).summary
        segment_stats.append(stats)

    merged = _merge_sample_report_1_stats(segment_stats)
    # K4b (Refs #1770): identisch zu ``get_statistics_hybrid`` — eigener
    # Live-Term ueber den Gesamtzeitraum statt der Summe der Monatswerte. Die
    # Grundgesamtheit ist dieselbe wie im Backend (sichtbare Ereignisse der
    # Einrichtung im Zeitraum mit Personenbezug), daher derselbe Zaehler.
    merged["unique_clients"] = count_unique_clients(facility, date_from, date_to)
    merged["unique_clients_data_source"] = LIVE
    return merged
