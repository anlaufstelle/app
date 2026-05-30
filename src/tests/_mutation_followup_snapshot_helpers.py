"""Helper-Factory für ``test_mutation_followup_snapshot_*`` (Refs #930).

Wird von den Sub-Files ``test_mutation_followup_snapshot_branches.py`` und
``test_mutation_followup_snapshot_merge.py`` geteilt. Kapselt das
Event-Factory mit ``tz-aware``-Normalisierung.
"""

from __future__ import annotations

from django.utils import timezone

from core.models import Event


def _make_event(facility, client, doc_type, user, dt, *, anonymous=False):
    aware_dt = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=aware_dt,
        data_json={},
        is_anonymous=anonymous,
        created_by=user,
    )
