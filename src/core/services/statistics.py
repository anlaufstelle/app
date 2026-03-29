"""Statistics service: aggregations over events."""

import logging

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from core.models import Event

logger = logging.getLogger(__name__)


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
