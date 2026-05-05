"""Perf-Tests fuer den Zeitstrom-Feed (Refs #740, Refs #713 Audit-Massnahme #31).

Verifiziert, dass die Feed-Funktionen ``build_feed_items`` und
``enrich_events_with_preview`` eine **konstante** Anzahl von Queries
ausfuehren — unabhaengig von der Anzahl der Events / WorkItems / etc.
Ohne ``select_related``/``prefetch_related`` wachsen die Queries
linear mit N (N+1-Klassiker).
"""

from datetime import timedelta

import pytest
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, Event, WorkItem
from core.services.feed import build_feed_items


def _create_events(facility, doc_type, user, count, when):
    """Bulk-create N events mit identischen Eckdaten — fuer Query-Count-Tests."""
    events = []
    for i in range(count):
        events.append(
            Event.objects.create(
                facility=facility,
                document_type=doc_type,
                occurred_at=when,
                data_json={"note": f"event-{i}"},
                created_by=user,
            )
        )
    return events


@pytest.fixture
def normal_doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )


@pytest.mark.django_db
class TestFeedQueryCount:
    """Anzahl Queries unabhaengig von N — bestaetigt Prefetch-Strategie."""

    def test_build_feed_items_query_count_constant(self, facility, normal_doc_type, admin_user):
        from django.db import connection

        when = timezone.now()
        # Baseline: 5 Events
        _create_events(facility, normal_doc_type, admin_user, 5, when)
        with CaptureQueriesContext(connection) as ctx_small:
            feed_small = build_feed_items(facility, when.date(), "events", user=admin_user)
        assert len(feed_small) == 5
        small_queries = len(ctx_small.captured_queries)

        # 50 Events
        _create_events(facility, normal_doc_type, admin_user, 45, when)
        with CaptureQueriesContext(connection) as ctx_large:
            feed_large = build_feed_items(facility, when.date(), "events", user=admin_user)
        assert len(feed_large) == 50
        large_queries = len(ctx_large.captured_queries)

        # Erlaubte Drift: kleine Anzahl konstanter Zusatz-Queries (z.B.
        # Sensitivity-Lookups), aber NICHT linear in N.
        assert large_queries <= small_queries + 5, (
            f"build_feed_items skaliert nicht-konstant: "
            f"5 Events => {small_queries} Queries, 50 Events => {large_queries}. "
            f"Differenz {large_queries - small_queries} > 5 deutet auf N+1 hin."
        )


@pytest.mark.django_db
class TestZeitstromViewQueryCount:
    """Volle Zeitstrom-View (inkl. Sidebar-WorkItems) — Sidebar war
    pre-Refs #740 ohne select_related, ist jetzt mit drin."""

    def test_zeitstrom_index_query_count_constant(
        self, client, facility, normal_doc_type, admin_user, client_identified
    ):
        from django.db import connection

        client.force_login(admin_user)
        # Baseline: 1 WorkItem im Sidebar
        WorkItem.objects.create(
            facility=facility,
            created_by=admin_user,
            client=client_identified,
            title="Sidebar-Aufgabe 1",
            status=WorkItem.Status.OPEN,
        )
        with CaptureQueriesContext(connection) as ctx_small:
            response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        small_queries = len(ctx_small.captured_queries)

        # 5 WorkItems (Sidebar zeigt max 5)
        for i in range(4):
            WorkItem.objects.create(
                facility=facility,
                created_by=admin_user,
                client=client_identified,
                title=f"Sidebar-Aufgabe {i + 2}",
                status=WorkItem.Status.OPEN,
                created_at=timezone.now() - timedelta(minutes=i),
            )
        with CaptureQueriesContext(connection) as ctx_large:
            response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        large_queries = len(ctx_large.captured_queries)

        assert large_queries <= small_queries + 3, (
            f"Zeitstrom-View Sidebar skaliert nicht-konstant: "
            f"1 WorkItem => {small_queries} Queries, 5 WorkItems => {large_queries}. "
            f"Differenz {large_queries - small_queries} > 3 deutet auf N+1 hin "
            f"(Sidebar muss select_related('client', 'assigned_to') haben)."
        )
