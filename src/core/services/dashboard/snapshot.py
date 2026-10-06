"""Snapshot service: create, ensure and retrieve monthly statistics snapshots."""

import calendar
import json
import logging
from datetime import date

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from core.models import DocumentType, StatisticsSnapshot
from core.reporting.data_source import LIVE

# Zyklusfrei: ``core.reporting.registry`` importiert nur ``core.reporting.base``
# (keine Services, keine Models) und laedt das ``backends``-Package erst lazy
# beim ersten ``get_*``-Aufruf.
from core.reporting.db_guard import reporting_statement_timeout
from core.reporting.registry import get_backend, get_backends
from core.services.dashboard.statistics import (
    PERSONS_KEY,
    count_unique_clients,
    get_statistics,
    get_statistics_for_events,
)

logger = logging.getLogger(__name__)

_STAGE_KEYS = ("anonym", "identifiziert", "qualifiziert")

# Refs #2070: Reservierter Schluessel in ``StatisticsSnapshot.backend_data`` fuer
# uebersprungene Backends (``{slug: Exception-Klassenname}``). Kein Slug kann ihn
# treffen — ``registry._SLUG_RE`` verlangt ein fuehrendes ``[a-z0-9]``. Kein
# Modellfeld, keine Migration: ``backend_data`` ist ein freies ``JSONField``.
SNAPSHOT_BACKEND_ERRORS_KEY = "_errors"

# Refs #2425: reserved key in ``StatisticsSnapshot.backend_data`` for the counts
# of the month's events that retention runs have deleted:
# ``{"_statistics": <get_statistics shape>, <slug>: <backend summary>}``. Every
# snapshot write adds it to the live counts, so the month keeps counting deleted
# events across later deletion runs and recomputations. Same collision argument
# as ``_errors``; no model field, no migration.
SNAPSHOT_DELETED_KEY = "_deleted"
_DELETED_STATISTICS_KEY = "_statistics"


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


def create_or_update_snapshot(facility, year, month, deleted_events=None):
    """Create or update a statistics snapshot for a given facility and month.

    Refs #2425: the snapshot counts the month's live events plus the events
    retention runs deleted before (``SNAPSHOT_DELETED_KEY``). ``deleted_events``
    are events of this month the calling run has just deleted; they are added
    to that carry-over first.
    """
    date_from = date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    date_to = date(year, month, last_day)

    stats = get_statistics(facility, date_from, date_to)

    # M5 P3 (Refs #1061): Roh-Aggregate aller Snapshot-Backends aus der Registry
    # statt eines hart verdrahteten Sachbericht-Aufrufs. Bewusst ``build_dataset``
    # und NICHT ``prepare_export``: der Snapshot ist der interne Rohbestand, die
    # k-Anon-Suppression gehoert allein in den Export-/Panel-Pfad.
    #
    # Refs #2070: je Backend isoliert und mit Query-Deckel. Bis hierher riss ein
    # einziges werfendes Backend den kompletten Lauf ab — inklusive der bereits
    # berechneten ``stats`` dieser Einrichtung, aller Folgemonate
    # (``record_deleted_events``) und aller Folge-Einrichtungen im
    # Cron-Command. Der Preis dafuer war ein Datenverlust-Fenster: der Cron
    # laeuft monatlich, die Event-Retention laeuft weiter.
    payloads = {}
    backend_errors = {}
    for backend in get_backends():
        if not backend.snapshot_enabled:
            continue
        try:
            with reporting_statement_timeout():
                payloads[backend.slug] = backend.build_dataset(facility, date_from, date_to).summary
        except Exception as exc:
            # Nur der Klassenname, nie ``str(exc)``: ``backend_data`` ist im
            # PII-Residue-Sweep pauschal als non-PII klassifiziert
            # (``src/tests/_residue_expectations.py``) — eine roh uebernommene
            # Fehlermeldung koennte Query-Parameter und damit Personenbezug
            # tragen. Der vollstaendige Traceback steht im Log (und via
            # Sentry-LoggingIntegration im Fehler-Tracking).
            backend_errors[backend.slug] = type(exc).__name__
            logger.exception(
                "create_or_update_snapshot: Backend %s uebersprungen (facility=%s year=%d month=%d)",
                backend.slug,
                facility.name,
                year,
                month,
            )

    # Remove top_clients (not snapshot-safe due to anonymization)
    stats.pop("top_clients", None)

    # Refs #2425: read, extend and write the carry-over under a row lock, so a
    # concurrent snapshot run cannot write back an older carry-over.
    with transaction.atomic():
        existing = (
            StatisticsSnapshot.objects.select_for_update().filter(facility=facility, year=year, month=month).first()
        )
        deleted = dict(existing.backend_data.get(SNAPSHOT_DELETED_KEY) or {}) if existing else {}
        if deleted_events is not None:
            deleted = _add_deleted_events(deleted, deleted_events, backend_errors)
        if deleted:
            stats = _merge_stats([stats, deleted[_DELETED_STATISTICS_KEY]])
            for slug, summary in payloads.items():
                if slug in deleted:
                    payloads[slug] = _merge_backend_summaries(slug, [summary, deleted[slug]])
            payloads[SNAPSHOT_DELETED_KEY] = deleted
        _write_snapshot(facility, year, month, stats, payloads, backend_errors)
    logger.info(
        "Snapshot created/updated: facility=%s year=%d month=%d",
        facility.name,
        year,
        month,
    )


def _write_snapshot(facility, year, month, stats, payloads, backend_errors):
    """Enrich, serialise and store one monthly snapshot row."""
    # Enrich by_document_type with system_type and document_type_id.
    # Bulk-Lookup statt N+1-Loop (Refs #598): eine einzige Query holt alle
    # relevanten DocumentTypes. Refs #1749 (ADR-040): Schluessel ist der blosse
    # ``name`` — er ist je Einrichtung eindeutig, seit ``category`` als
    # Zweitschluessel entfallen ist.
    entries = stats.get("by_document_type", [])
    if entries:
        dt_lookup = {
            dt.name: dt
            for dt in DocumentType.objects.for_facility(facility).filter(name__in=[e["name"] for e in entries])
        }
        for entry in entries:
            dt = dt_lookup.get(entry["name"])
            entry["system_type"] = dt.system_type if dt and dt.system_type else ""
            entry["document_type_id"] = str(dt.id) if dt else ""

    # Force-convert lazy translation strings to plain strings for JSON storage
    stats = json.loads(json.dumps(stats, cls=DjangoJSONEncoder))
    payloads = json.loads(json.dumps(payloads, cls=DjangoJSONEncoder))

    # Refs #2070: der Ausfall wird **laut** markiert, nicht verschwiegen — genau
    # das war die Sorge hinter der frueheren Fail-loud-Politik (ein leises
    # Datenloch, das erst Monate spaeter auffaellt). Der Schluessel kann mit
    # keinem Slug kollidieren: ``registry._SLUG_RE`` verbietet den fuehrenden
    # Unterstrich. Er entsteht nur im Fehlerfall — ein immer vorhandener,
    # meist leerer Marker waere Rauschen in jeder Snapshot-Zeile.
    if backend_errors:
        payloads[SNAPSHOT_BACKEND_ERRORS_KEY] = backend_errors

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


def _add_deleted_events(deleted, events, backend_errors):
    """Return the carry-over ``deleted`` extended by the counts of ``events``.

    Persons merge by maximum like every other segment merge (K0-b): the
    result stays a lower bound of the distinct persons, never a sum.
    """
    statistics = [get_statistics_for_events(events)]
    if _DELETED_STATISTICS_KEY in deleted:
        statistics.insert(0, deleted[_DELETED_STATISTICS_KEY])
    extended = {_DELETED_STATISTICS_KEY: _merge_stats(statistics)}
    for backend in get_backends():
        if not backend.snapshot_enabled:
            continue
        previous = deleted.get(backend.slug)
        try:
            with reporting_statement_timeout():
                summary = backend.summarize_events(events)
        except Exception as exc:
            # Same isolation as the live payload above: the deleted counts of
            # this backend are lost for this run, and the snapshot says so.
            backend_errors[backend.slug] = type(exc).__name__
            logger.exception("create_or_update_snapshot: deleted events for backend %s skipped", backend.slug)
            summary = None
        if summary is None:
            if previous is not None:
                extended[backend.slug] = previous
            continue
        extended[backend.slug] = (
            summary if previous is None else _merge_backend_summaries(backend.slug, [previous, summary])
        )
    return extended


def _merge_backend_summaries(slug, summaries):
    """Merge monthly summaries of one snapshot backend (Refs #2425)."""
    mergers = {"sample-report-1": _merge_sample_report_1_stats}
    return mergers[slug](summaries)


def record_deleted_events(facility, events):
    """Carry events a retention run has just deleted into their months' snapshots.

    Refs #2425: the monthly snapshot is the anonymous remainder of deleted
    events. Recomputing it from the surviving events alone made each further
    deletion run in the same month drop the counts of the earlier ones. The
    caller passes the events AFTER deleting them, restricted to those really
    deleted — a held or failed event stays live and must not count twice. The
    running month is included: its row holds the carry-over until the
    month-end snapshot run recomputes it.
    """
    # ``order_by()`` clears the model ordering; with it, DISTINCT would yield
    # one row per timestamp and carry the same events more than once.
    months = sorted(set(events.order_by().values_list("occurred_at__year", "occurred_at__month").distinct()))
    for year, month in months:
        month_events = events.filter(occurred_at__year=year, occurred_at__month=month)
        create_or_update_snapshot(facility, year, month, deleted_events=month_events)


def get_snapshot(facility, year, month):
    """Return snapshot data dict or None if no snapshot exists."""
    snap = StatisticsSnapshot.objects.for_facility(facility).filter(year=year, month=month).first()
    return snap.data if snap else None


# K4c (Refs #1770): Schluessel des Herkunfts-/Stand-Stempels. #1743 hat die
# **Quelle** jedes Artefakts ausgewiesen (live vs. Monats-Aggregate) — nicht
# aber deren **Stand**. Genau der fehlte: „Monats-Aggregate" beantwortet nicht,
# von wann sie sind, und die Differenz zwischen einem eingefrorenen Aggregat und
# einer Live-Abfrage desselben Zeitraums ist ein Fingerabdruck des Geloeschten.
# Ohne Datum kann eine Empfaengerin nicht einmal erkennen, dass sie zwei
# Artefakte verschiedenen Standes vergleicht.
SNAPSHOT_AS_OF_KEY = "snapshot_as_of"


def _newer(current, candidate):
    """Juengster Snapshot-Stand ueber alle beitragenden Monatssegmente."""
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)


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


def _snapshot_statistics(snapshot):
    """Return the standard statistics payload stored in a snapshot."""
    return snapshot.data


def _iter_segments(
    facility,
    date_from,
    date_to,
    *,
    live_loader,
    snapshot_loader=_snapshot_statistics,
    snapshots_enabled=True,
):
    """Yield monthly statistics with their source and contributing snapshot stand.

    ``snapshot_loader`` may return ``None`` when a stored snapshot does not
    contain the requested payload. In that case the segment falls back to
    ``live_loader`` and must not claim a snapshot stand.
    """
    for seg_from, seg_to, snapshot_eligible in _split_into_segments(date_from, date_to):
        stats = None
        source = "live"
        snapshot_as_of = None

        if snapshot_eligible and snapshots_enabled:
            snapshot = (
                StatisticsSnapshot.objects.for_facility(facility)
                .filter(year=seg_from.year, month=seg_from.month)
                .first()
            )
            if snapshot is not None:
                stats = snapshot_loader(snapshot)
                if stats is not None:
                    source = "snapshot"
                    snapshot_as_of = snapshot.updated_at

        if stats is None:
            stats = live_loader(seg_from, seg_to)

        yield seg_from, stats, source, snapshot_as_of


def _load_live_statistics(facility, date_from, date_to, document_type=None):
    """Load one live statistics segment without full-range-only top clients."""
    stats = get_statistics(facility, date_from, date_to, document_type)
    stats.pop("top_clients", None)
    return stats


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


def _accumulate_age_cluster(age_cluster_map, age_persons, entry):
    """Einen ``by_age_cluster``-Eintrag in die Merge-Akkumulatoren einsortieren.

    Refs #1827: zweifach kopiert (``_merge_stats``/``_merge_sample_report_1_stats``)
    — die K0-b-Regel „Personen nie summieren" (Maximum statt Summe, ueber
    :func:`_merge_person_count`) musste deshalb zweimal nachgezogen werden
    (Fehlerklasse von #1770). Mutiert beide Dicts in place.
    """
    cluster = entry["cluster"]
    age_persons[cluster] = _merge_person_count(age_persons.get(cluster, 0), entry.get(PERSONS_KEY))
    if cluster in age_cluster_map:
        age_cluster_map[cluster]["count"] += entry["count"]
    else:
        age_cluster_map[cluster] = {**entry}


def _merge_stats(stats_list):
    """Merge multiple statistics dicts into one.

    Merge logic per field:
    - ``total_contacts`` – sum
    - ``by_contact_stage`` – sum per key
    - ``by_document_type`` – merge by ``name``, sum ``count`` (Refs #1749)
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
    doc_type_map = {}  # name → entry
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

        # by_document_type – merge by name (Refs #1749, ADR-040)
        #
        # Alt-Snapshots tragen weiter einen ``category``-Schluessel: sie sind
        # persistierter JSON und werden nicht migriert (der Wert waere fuer die
        # Anzeige ohnehin bedeutungslos). Deshalb (a) ``.get`` statt
        # Index-Zugriff — ein fehlender Schluessel darf nie ein ``KeyError``
        # sein — und (b) ``category`` wird beim Merge **verworfen**: Segmente,
        # die sich nur darin unterschieden, kollabieren deterministisch auf
        # EINE Zeile mit summierten Werten statt als Doppelzeile im Bericht zu
        # erscheinen. Bei den Zusatzfeldern (``system_type``,
        # ``document_type_id``) gewinnt das erste beitragende Segment; sie
        # haengen am Namen, nicht an der Kategorie, und sind ueber die
        # Segmente eines Namens daher ohnehin gleich.
        for entry in stats.get("by_document_type", []):
            name = entry.get("name")
            existing = doc_type_map.get(name)
            if existing is not None:
                existing["count"] += entry["count"]
            else:
                doc_type_map[name] = {key: value for key, value in entry.items() if key != "category"}

        # by_age_cluster – merge by cluster
        for entry in stats.get("by_age_cluster", []):
            _accumulate_age_cluster(age_cluster_map, age_persons, entry)

    merged["persons_by_contact_stage"] = stage_persons
    merged["by_document_type"] = sorted(doc_type_map.values(), key=lambda e: e["count"], reverse=True)
    merged["by_age_cluster"] = _with_merged_persons(age_cluster_map, age_persons)

    return merged


def aggregate_trend_segments(segments):
    """Merge trend payloads while discarding their per-segment metadata."""
    segment_stats = [
        {key: value for key, value in segment.items() if key not in ("label", "source")} for segment in segments
    ]
    return _merge_stats(segment_stats)


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
            _accumulate_age_cluster(age_cluster_map, age_persons, entry)

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
    segment_stats = []
    as_of = None

    for _seg_from, stats, _source, snapshot_as_of in _iter_segments(
        facility,
        date_from,
        date_to,
        live_loader=lambda seg_from, seg_to: _load_live_statistics(facility, seg_from, seg_to),
    ):
        segment_stats.append(stats)
        # K4c (Refs #1770): Stand des juengsten beitragenden Aggregats.
        as_of = _newer(as_of, snapshot_as_of)

    merged = _merge_stats(segment_stats)
    # ``None`` = kein Segment kam aus einem Snapshot, das Artefakt ist trotz
    # hybridem Lesepfad vollstaendig live. Der Stempel bleibt dann weg statt
    # eine Herkunft zu behaupten, die es nicht gibt.
    merged[SNAPSHOT_AS_OF_KEY] = as_of

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


def get_statistics_trend(facility, date_from, date_to, document_type=None):
    """Return per-segment statistics for trend visualisation.

    Unlike :func:`get_statistics_hybrid` which merges all segments into one
    aggregated result, this function returns the individual monthly data
    points so they can be rendered as a time-series chart.

    Each entry contains:
    - ``label`` – ``"YYYY-MM"`` month identifier
    - ``source`` – ``"snapshot"`` or ``"live"``
    - The standard statistics fields (``total_contacts``, etc.)

    Refs #1804 (Entscheid F3): Ist ``document_type`` gesetzt, faellt der
    Snapshot-Zweig komplett weg — Monats-Aggregate speichern die Kontaktstufen-
    und Alters-Aufschluesselung, aber keine Zerlegung nach Dokumentationstyp;
    aus ihnen laesst sich der Filter nicht rekonstruieren. Das ist der im
    Konzept benannte Preis des Filters, nicht ein uebersehener Zweig.
    """
    result = []

    for seg_from, stats, source, _snapshot_as_of in _iter_segments(
        facility,
        date_from,
        date_to,
        live_loader=lambda segment_from, segment_to: _load_live_statistics(
            facility,
            segment_from,
            segment_to,
            document_type,
        ),
        snapshots_enabled=document_type is None,
    ):
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
    segment_stats = []
    as_of = None

    for _seg_from, stats, _source, snapshot_as_of in _iter_segments(
        facility,
        date_from,
        date_to,
        live_loader=lambda seg_from, seg_to: (
            get_backend("sample-report-1").build_dataset(facility, seg_from, seg_to).summary
        ),
        # M5 P3 (Refs #1061) / #1740: ``backend_data`` ist die einzige
        # Quelle — der Fallback auf das Altfeld ist mit dem Ende der
        # Uebergangsphase entfallen. ``or None`` normalisiert das leere
        # Dict, damit der Live-Zweig wirklich greift.
        snapshot_loader=lambda snapshot: snapshot.backend_data.get("sample-report-1") or None,
    ):
        segment_stats.append(stats)
        # K4c (Refs #1770): Stand nur fuer Segmente, die wirklich aus einem
        # Aggregat kamen — ein leerer Payload darf keinen Stand behaupten.
        as_of = _newer(as_of, snapshot_as_of)

    merged = _merge_sample_report_1_stats(segment_stats)
    merged[SNAPSHOT_AS_OF_KEY] = as_of
    # K4b (Refs #1770): identisch zu ``get_statistics_hybrid`` — eigener
    # Live-Term ueber den Gesamtzeitraum statt der Summe der Monatswerte. Die
    # Grundgesamtheit ist dieselbe wie im Backend (sichtbare Ereignisse der
    # Einrichtung im Zeitraum mit Personenbezug), daher derselbe Zaehler.
    merged["unique_clients"] = count_unique_clients(facility, date_from, date_to)
    merged["unique_clients_data_source"] = LIVE
    return merged
