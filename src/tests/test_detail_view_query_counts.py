"""Refs #824 (C-57): Query-Count-Schutz fuer Detail-Views + Handover.

Pattern aus :file:`test_zeitstrom_perf.py`: Anzahl Events/Episoden/etc.
hochsetzen und assertiv pruefen, dass die Query-Anzahl **konstant**
bleibt. Wachsende Queries = N+1, fehlt ``select_related`` /
``prefetch_related``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Episode, Event


def _force_count(response):
    """Render Lazy-Querysets im Template komplett — ohne `.content`-Zugriff
    bleiben prefetches teils unausgewertet, was den Vergleich verzerrt."""
    response.content  # noqa: B018


@pytest.mark.django_db
class TestClientDetailViewQueryCount:
    def test_query_count_is_constant_when_event_count_grows(
        self,
        client,
        facility,
        client_identified,
        doc_type_contact,
        admin_user,
    ):
        client.force_login(admin_user)

        url = reverse("core:client_detail", args=[client_identified.pk])

        # Baseline: 2 Events.
        for i in range(2):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=timezone.now() - timedelta(days=i),
                data_json={"notiz": f"baseline-{i}"},
                created_by=admin_user,
            )

        with CaptureQueriesContext(connection) as small_ctx:
            response = client.get(url)
        assert response.status_code == 200
        _force_count(response)
        small_queries = len(small_ctx.captured_queries)

        # 20 Events.
        for i in range(18):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=timezone.now() - timedelta(days=i + 2),
                data_json={"notiz": f"large-{i}"},
                created_by=admin_user,
            )

        with CaptureQueriesContext(connection) as large_ctx:
            response = client.get(url)
        assert response.status_code == 200
        _force_count(response)
        large_queries = len(large_ctx.captured_queries)

        assert large_queries <= small_queries + 5, (
            f"ClientDetailView skaliert nicht-konstant: "
            f"2 Events => {small_queries} Queries, 20 Events => {large_queries}. "
            f"Differenz {large_queries - small_queries} > 5 deutet auf N+1 hin."
        )


@pytest.mark.django_db
class TestEventDetailViewQueryCount:
    def test_query_count_is_constant_when_history_grows(
        self,
        client,
        facility,
        client_identified,
        doc_type_contact,
        admin_user,
    ):
        from core.models import EventHistory

        client.force_login(admin_user)

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Initial"},
            created_by=admin_user,
        )
        url = reverse("core:event_detail", args=[event.pk])

        # Baseline: 2 History-Eintraege.
        for i in range(2):
            EventHistory.objects.create(
                event=event,
                changed_by=admin_user,
                action=EventHistory.Action.UPDATE,
                data_after={"notiz": f"v{i}"},
            )

        with CaptureQueriesContext(connection) as small_ctx:
            response = client.get(url)
        assert response.status_code == 200
        _force_count(response)
        small_queries = len(small_ctx.captured_queries)

        # 12 History-Eintraege.
        for i in range(10):
            EventHistory.objects.create(
                event=event,
                changed_by=admin_user,
                action=EventHistory.Action.UPDATE,
                data_after={"notiz": f"vlarge-{i}"},
            )

        with CaptureQueriesContext(connection) as large_ctx:
            response = client.get(url)
        assert response.status_code == 200
        _force_count(response)
        large_queries = len(large_ctx.captured_queries)

        assert large_queries <= small_queries + 5, (
            f"EventDetailView skaliert nicht-konstant mit History: "
            f"2 => {small_queries} Queries, 12 => {large_queries}. "
            f"Differenz {large_queries - small_queries} > 5 deutet auf N+1 hin."
        )


@pytest.mark.django_db
class TestCaseDetailViewQueryCount:
    def test_query_count_is_constant_when_episode_and_event_count_grow(
        self,
        client,
        facility,
        client_identified,
        doc_type_contact,
        staff_user,
    ):
        client.force_login(staff_user)

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Sprint-Test",
            lead_user=staff_user,
        )
        url = reverse("core:case_detail", args=[case.pk])

        # Baseline: 2 Episoden + 2 zugeordnete Events.
        for i in range(2):
            Episode.objects.create(
                case=case,
                title=f"Episode {i}",
                started_at=timezone.now().date(),
                created_by=staff_user,
            )
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                case=case,
                occurred_at=timezone.now() - timedelta(days=i),
                data_json={"notiz": f"case-event-{i}"},
                created_by=staff_user,
            )

        with CaptureQueriesContext(connection) as small_ctx:
            response = client.get(url)
        assert response.status_code == 200
        _force_count(response)
        small_queries = len(small_ctx.captured_queries)

        # +6 Episoden, +6 Events.
        for i in range(2, 8):
            Episode.objects.create(
                case=case,
                title=f"Episode {i}",
                started_at=timezone.now().date(),
                created_by=staff_user,
            )
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                case=case,
                occurred_at=timezone.now() - timedelta(days=i + 5),
                data_json={"notiz": f"case-event-large-{i}"},
                created_by=staff_user,
            )

        with CaptureQueriesContext(connection) as large_ctx:
            response = client.get(url)
        assert response.status_code == 200
        _force_count(response)
        large_queries = len(large_ctx.captured_queries)

        assert large_queries <= small_queries + 5, (
            f"CaseDetailView skaliert nicht-konstant: "
            f"2/2 => {small_queries} Queries, 8/8 => {large_queries}. "
            f"Differenz {large_queries - small_queries} > 5 deutet auf N+1 hin."
        )


@pytest.mark.django_db
class TestHandoverSummaryQueryCount:
    def test_build_handover_summary_query_count_is_constant(
        self,
        facility,
        client_identified,
        doc_type_contact,
        admin_user,
    ):
        from core.services.handover import build_handover_summary

        target = timezone.localdate()

        # Baseline: 2 Events am Tag.
        for i in range(2):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=timezone.now() - timedelta(hours=i),
                data_json={"notiz": f"baseline-{i}"},
                created_by=admin_user,
            )

        with CaptureQueriesContext(connection) as small_ctx:
            build_handover_summary(facility, target, None, admin_user)
        small_queries = len(small_ctx.captured_queries)

        # 20 Events.
        for i in range(18):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=timezone.now() - timedelta(hours=i + 2),
                data_json={"notiz": f"large-{i}"},
                created_by=admin_user,
            )

        with CaptureQueriesContext(connection) as large_ctx:
            build_handover_summary(facility, target, None, admin_user)
        large_queries = len(large_ctx.captured_queries)

        assert large_queries <= small_queries + 5, (
            f"build_handover_summary skaliert nicht-konstant: "
            f"2 Events => {small_queries} Queries, 20 Events => {large_queries}. "
            f"Differenz {large_queries - small_queries} > 5 deutet auf N+1 hin."
        )
