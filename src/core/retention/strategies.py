"""Shared expiry queries for preview, proposal and enforcement (Refs #2524)."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import DateTimeField, ExpressionWrapper, F, Q, QuerySet, Value
from django.db.models.functions import Coalesce

from core.models import Case, Client, DocumentType, Event


@dataclass(frozen=True)
class RetentionStrategy:
    """Expired events with their individual deadline and audit category."""

    category: str
    cutoff: datetime
    queryset: QuerySet


def _with_deadline(queryset, anchor, days):
    return queryset.annotate(
        retention_due_at=ExpressionWrapper(anchor + Value(timedelta(days=days)), output_field=DateTimeField())
    )


def iter_strategies(facility, settings_obj, now: datetime) -> Iterator[RetentionStrategy]:
    """Yield all enabled deadlines; the earliest expiry wins, including type caps."""
    events = Event.objects.filter(facility=facility, is_deleted=False)
    if settings_obj.retention_anonymous_days >= 1:
        days = settings_obj.retention_anonymous_days
        cutoff = now - timedelta(days=days)
        yield RetentionStrategy(
            "anonymous",
            cutoff,
            _with_deadline(
                events.filter(Q(is_anonymous=True) | Q(client__isnull=True), occurred_at__lt=cutoff),
                F("occurred_at"),
                days,
            ),
        )
    if settings_obj.retention_identified_days >= 1:
        days = settings_obj.retention_identified_days
        cutoff = now - timedelta(days=days)
        yield RetentionStrategy(
            "identified",
            cutoff,
            _with_deadline(
                events.filter(client__contact_stage=Client.ContactStage.IDENTIFIED, occurred_at__lt=cutoff),
                F("occurred_at"),
                days,
            ),
        )
    if settings_obj.retention_qualified_days >= 1:
        days = settings_obj.retention_qualified_days
        cutoff = now - timedelta(days=days)
        # A missing closing timestamp falls back to business activity/creation.
        from django.db.models import Case as ConditionalCase
        from django.db.models import When

        anchor = ConditionalCase(
            When(case__isnull=True, then=F("occurred_at")),
            When(
                case__status=Case.Status.CLOSED,
                then=Coalesce("case__closed_at", "case__last_activity_at", "case__created_at"),
            ),
            default=Coalesce("case__last_activity_at", "case__created_at"),
            output_field=DateTimeField(),
        )
        qualified = events.filter(client__contact_stage=Client.ContactStage.QUALIFIED).annotate(retention_anchor=anchor)
        yield RetentionStrategy(
            "qualified", cutoff, _with_deadline(qualified.filter(retention_anchor__lt=cutoff), anchor, days)
        )
    for document_type in DocumentType.objects.filter(
        facility=facility,
        entity_type=DocumentType.EntityType.EVENT,
        retention_days__gte=1,
    ):
        days = document_type.retention_days
        assert days is not None  # noqa: S101 — the database filter excludes NULL.
        cutoff = now - timedelta(days=days)
        yield RetentionStrategy(
            "document_type",
            cutoff,
            _with_deadline(events.filter(document_type=document_type, occurred_at__lt=cutoff), F("occurred_at"), days),
        )
