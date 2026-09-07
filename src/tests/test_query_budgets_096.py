"""Obergrenzen für query-sensitive Listen- und Exportpfade (Refs #2158)."""

from types import SimpleNamespace

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, Case, Client, Event, WorkItem
from core.services.client.export import _gather_cases, _gather_deletion_requests, _gather_events, _gather_legal_holds
from core.services.dashboard.search import search_clients_and_events
from core.services.deletion import annotate_deletion_targets
from core.services.events import build_feed_items
from tests._factories import make_event

pytestmark = pytest.mark.django_db


def _assert_query_budget(captured, maximum):
    assert len(captured) <= maximum, [query["sql"] for query in captured.captured_queries]


def _warm_content_types():
    ContentType.objects.get_for_models(Activity, Case, Client, Event, WorkItem)


def test_dashboard_search_stays_at_two_evaluated_querysets(facility, staff_user, client_identified):
    # Ein Query je Ergebnisgruppe; zusätzliche Treffer dürfen keine N+1-Reads
    # auslösen, weil der Event-Zweig seine Kartenrelationen select_related lädt.
    with CaptureQueriesContext(connection) as captured:
        clients, events = search_clients_and_events(facility, staff_user, client_identified.pseudonym)
        list(clients)
        list(events)

    _assert_query_budget(captured, 2)


def test_export_gatherers_share_a_bounded_relation_budget(facility, staff_user, client_identified, doc_type_contact):
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Budget-Fall",
        created_by=staff_user,
    )
    event = make_event(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
        case=case,
    )
    _warm_content_types()

    # Vier beziehungsreiche Export-Sammler gemeinsam: Events/Cases je eine
    # Hauptquery, Anträge/Holds zusätzlich nur ihre gebündelten Ziel-ID-Reads.
    with CaptureQueriesContext(connection) as captured:
        _gather_events(client_identified, staff_user, attachment_index={})
        _gather_cases(client_identified, attachment_index={})
        _gather_deletion_requests(facility, client_identified, [event.pk])
        _gather_legal_holds(facility, client_identified, [event.pk])

    _assert_query_budget(captured, 10)


def test_deletion_target_annotation_is_three_bulk_queries(facility, staff_user, client_identified, doc_type_contact):
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Budget-Fall",
        created_by=staff_user,
    )
    event = make_event(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    requests = [
        SimpleNamespace(target_type="Client", target_id=client_identified.pk, merge_into_id=None),
        SimpleNamespace(target_type="Case", target_id=case.pk, merge_into_id=None),
        SimpleNamespace(target_type="Event", target_id=event.pk, merge_into_id=None),
    ]

    # Die Kosten wachsen nach Zieltypen (Client/Case/Event), nicht nach Zeilen.
    with CaptureQueriesContext(connection) as captured:
        annotate_deletion_targets(requests, facility, staff_user)

    _assert_query_budget(captured, 3)


def test_event_feed_uses_one_joined_query(facility, staff_user, client_identified, doc_type_contact):
    make_event(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )

    with CaptureQueriesContext(connection) as captured:
        build_feed_items(facility, timezone.localdate(), "events", user=staff_user)

    _assert_query_budget(captured, 1)


def test_client_list_query_count_is_bounded_per_page(client, facility, staff_user, client_identified):
    Client.objects.bulk_create(
        [Client(facility=facility, pseudonym=f"Budget-{index}", created_by=staff_user) for index in range(5)]
    )
    client.force_login(staff_user)

    # Auth-/MFA-Middleware, Facility-Kontext, Paginator, Seitenquery und
    # gebündelte Badge-Queries brauchen derzeit 18 Reads. 20 lässt zwei Queries
    # Spielraum, aber keine Query je zusätzlicher Person zu.
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("core:client_list"))

    assert response.status_code == 200
    _assert_query_budget(captured, 20)
