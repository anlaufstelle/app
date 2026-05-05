"""Statistics service: aggregations over events."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.utils.formatting import parse_date

logger = logging.getLogger(__name__)

STATISTICS_MV_NAME = "core_statistics_event_flat"


@dataclass(frozen=True)
class PeriodState:
    """Refs #816 (C-49): Aufgeloeste Period-Konfiguration fuer Statistik-Sichten.

    ``period`` ist der eingehende Schluessel (``month`` | ``quarter`` | ``half`` |
    ``year`` | ``custom``); ``selected_year`` ist gesetzt, wenn ``period == "year"``,
    sonst ``None``.
    """

    period: str
    date_from: date
    date_to: date
    selected_year: int | None = None


def _parse_year(value, default: int) -> int:
    if not value:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def parse_statistics_period(query_params, today: date) -> PeriodState:
    """Refs #816 (C-49): Single Source of Truth fuer Period-Parsing.

    Frueher interpretierten ``StatisticsView`` und ``ChartDataView`` dieselben
    Query-Parameter separat — Drift-Risiko zwischen Dashboard und Chart-API.
    """
    period = query_params.get("period", "month")
    selected_year: int | None = None

    if period == "custom":
        date_from = parse_date(query_params.get("date_from"), today - timedelta(days=30))
        date_to = parse_date(query_params.get("date_to"), today)
    elif period == "year":
        selected_year = _parse_year(query_params.get("year"), today.year)
        date_from = date(selected_year, 1, 1)
        date_to = today if selected_year == today.year else date(selected_year, 12, 31)
    elif period == "quarter":
        date_from = today - timedelta(days=90)
        date_to = today
    elif period == "half":
        date_from = today - timedelta(days=182)
        date_to = today
    else:  # month (default)
        date_from = today - timedelta(days=30)
        date_to = today

    return PeriodState(period=period, date_from=date_from, date_to=date_to, selected_year=selected_year)


def get_statistics(facility, date_from, date_to):
    """Statistics data for a facility in the given period.

    Returns dict with: total_contacts, by_contact_stage, by_document_type,
    by_age_cluster, top_clients, unique_clients.
    """
    base_qs = Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )

    total_contacts = base_qs.count()

    # Contact stages: anonymous = is_anonymous OR client IS NULL
    stage_qs = base_qs.aggregate(
        anonym=Count("id", filter=Q(is_anonymous=True) | Q(client__isnull=True)),
        identifiziert=Count(
            "id",
            filter=~Q(is_anonymous=True) & Q(client__isnull=False) & Q(client__contact_stage="identified"),
        ),
        qualifiziert=Count(
            "id",
            filter=~Q(is_anonymous=True) & Q(client__isnull=False) & Q(client__contact_stage="qualified"),
        ),
    )
    by_contact_stage = {
        "anonym": stage_qs["anonym"],
        "identifiziert": stage_qs["identifiziert"],
        "qualifiziert": stage_qs["qualifiziert"],
    }

    # By document type
    by_document_type = list(
        base_qs.values("document_type__name", "document_type__category").annotate(count=Count("id")).order_by("-count")
    )
    by_document_type = [
        {
            "name": row["document_type__name"],
            "category": row["document_type__category"],
            "count": row["count"],
        }
        for row in by_document_type
    ]

    # By age cluster (only events with a client)
    age_labels = {
        "u18": _("Unter 18"),
        "18_26": _("18–26"),
        "27_plus": _("27+"),
        "unknown": _("Unbekannt"),
    }
    by_age_cluster_qs = (
        base_qs.exclude(client__isnull=True)
        .values("client__age_cluster")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    by_age_cluster = [
        {
            "cluster": row["client__age_cluster"],
            "label": age_labels.get(row["client__age_cluster"], row["client__age_cluster"]),
            "count": row["count"],
        }
        for row in by_age_cluster_qs
    ]

    # Top 5 Clients
    top_clients = list(
        base_qs.exclude(client__isnull=True)
        .values("client__pseudonym")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )
    top_clients = [{"pseudonym": row["client__pseudonym"], "count": row["count"]} for row in top_clients]

    # Unique clients
    unique_clients = base_qs.exclude(client__isnull=True).values("client").distinct().count()

    return {
        "total_contacts": total_contacts,
        "by_contact_stage": by_contact_stage,
        "by_document_type": by_document_type,
        "by_age_cluster": by_age_cluster,
        "top_clients": top_clients,
        "unique_clients": unique_clients,
    }


# ---------------------------------------------------------------------------
# Materialized View Pfad (Refs #544)
# ---------------------------------------------------------------------------


def _flat_view_enabled() -> bool:
    """Return True wenn die Materialized View genutzt werden darf.

    Aktiviert wird der Pfad über ``settings.STATISTICS_USE_FLAT_VIEW = True``.
    Zusätzlich muss das Backend Postgres sein — SQLite hat keine
    Materialized Views (siehe Migration 0049).
    """
    return bool(getattr(settings, "STATISTICS_USE_FLAT_VIEW", False)) and connection.vendor == "postgresql"


def get_event_counts_by_month(facility, year):
    """Gesamte Event-Anzahl pro Monat für ein Kalenderjahr.

    Wählt je nach Feature-Flag den Pfad:
    - ``STATISTICS_USE_FLAT_VIEW=True`` + Postgres → Materialized View
      ``core_statistics_event_flat`` (dramatisch schneller bei vielen Events).
    - sonst → klassisches ``Event``-Queryset als Fallback.

    Rückgabe: Liste von Dicts ``{"month": 1..12, "count": int}``, lückenlos
    von Januar bis Dezember.
    """
    counts = {month: 0 for month in range(1, 13)}

    if _flat_view_enabled():
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT EXTRACT(MONTH FROM month)::int AS m, COUNT(*)::bigint
                FROM {STATISTICS_MV_NAME}
                WHERE facility_id = %s
                  AND EXTRACT(YEAR FROM month)::int = %s
                GROUP BY m
                ORDER BY m
                """,
                [facility.pk, year],
            )
            for month, count in cursor.fetchall():
                counts[int(month)] = int(count)
    else:
        rows = (
            Event.objects.filter(
                facility=facility,
                is_deleted=False,
                occurred_at__year=year,
            )
            .values_list("occurred_at__month")
            .annotate(count=Count("id"))
        )
        for month, count in rows:
            counts[int(month)] = int(count)

    return [{"month": month, "count": counts[month]} for month in range(1, 13)]
