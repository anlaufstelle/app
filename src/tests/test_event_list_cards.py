"""Content visibility of contacts cards matches the public timeline path."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.events.feed import build_feed_items, enrich_events_with_preview
from core.services.events.list_filters import event_list_queryset
from tests.test_event_list import list_response
from tests.test_event_list_templates import Elements

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user", "admin_user"])
@pytest.mark.parametrize("sensitivity", ["normal", "high"])
def test_card_values_match_timeline_and_never_leak_hidden_fields(
    client, request, role, sensitivity, facility, staff_user
):
    user = request.getfixturevalue(role)
    doc_type = DocumentType.objects.create(facility=facility, name="Card visibility", sensitivity=sensitivity)
    markers = {}
    for index, level in enumerate(["normal", "elevated", "high"]):
        field = FieldTemplate.objects.create(
            facility=facility,
            name=f"Field {level}",
            slug=f"card-{level}",
            field_type="textarea",
            sensitivity=level,
            is_encrypted=True,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field, sort_order=index)
        markers[field.slug] = f"VISIBLE_MARKER_{level}_{sensitivity}"
    file_field = FieldTemplate.objects.create(
        facility=facility, name="File", slug="card-file", field_type="file", sensitivity="normal"
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=file_field, sort_order=3)
    file_id = str(uuid4())
    event = Event.objects.create(
        facility=facility,
        document_type=doc_type,
        created_by=staff_user,
        occurred_at=timezone.now(),
        data_json={**markers, "card-file": {"__file__": True, "id": file_id}},
    )
    event.refresh_from_db()
    assert all(event.data_json[slug].get("__encrypted__") is True for slug in markers)
    feed = build_feed_items(facility, timezone.localdate(), "events", user=user)
    enrich_events_with_preview(feed, user)
    expected_events = {item["object"].pk: item["object"] for item in feed if item["type"] == "event"}
    expected = {f["value"] for f in expected_events[event.pk].expanded_fields} if event.pk in expected_events else set()
    client.force_login(user)
    before = AuditLog.objects.count()
    response = list_response(client, user)
    body = response.content.decode()
    assert AuditLog.objects.count() == before
    actual = {value for value in markers.values() if value in body}
    visible_levels = {
        "assistant_user": ["normal"],
        "staff_user": ["normal", "elevated"],
        "lead_user": ["normal", "elevated", "high"],
        "admin_user": ["normal", "elevated", "high"],
    }[role]
    permitted = {markers[f"card-{level}"] for level in visible_levels}
    if sensitivity == "high" and role in {"assistant_user", "staff_user"}:
        permitted = set()
    assert actual == permitted
    assert actual == set(markers.values()) & expected
    for value in set(markers.values()) - expected:
        assert value not in body
    assert file_id not in body
    assert "__file__" not in body
    for stored in event.data_json.values():
        if isinstance(stored, dict) and stored.get("__encrypted__"):
            assert stored["value"] not in body
    assert "gAAAAA" not in body
    if event.pk in expected_events:
        html = Elements(response.content)
        assert html.find("button", **{"aria-controls": f"event-detail-{event.pk}", "aria-expanded": "false"})
        assert html.find("a", href=reverse("core:event_detail", args=[event.pk]))
        assert "Zum Eintrag" in body
        assert "[Datei]" in body
        card = next(e for e in response.context["events"] if e.pk == event.pk)
        assert card.expanded_fields == expected_events[event.pk].expanded_fields
        assert card.preview_fields == expected_events[event.pk].preview_fields


def test_contents_are_loaded_only_for_the_page_and_metadata_query_stays_deferred(
    client, staff_user, sample_event, monkeypatch
):
    from core.views.event_list import EventListView

    for _ in range(6):
        Event.objects.create(
            facility=sample_event.facility,
            document_type=sample_event.document_type,
            created_by=staff_user,
            occurred_at=sample_event.occurred_at,
        )
    metadata = event_list_queryset(sample_event.facility, staff_user, {})
    assert all("data_json" in e.get_deferred_fields() for e in metadata)
    monkeypatch.setattr(EventListView, "page_size", 5)
    client.force_login(staff_user)
    with CaptureQueriesContext(connection) as captured:
        response = list_response(client, staff_user)
    page = response.context["events"]
    assert len(page) == 5 and page.paginator.count == 7
    assert [e.pk for e in page] == list(metadata.values_list("pk", flat=True)[:5])
    assert all(hasattr(e, "expanded_fields") and "data_json" not in e.get_deferred_fields() for e in page)
    content_queries = [q["sql"] for q in captured if '"core_event"."data_json"' in q["sql"]]
    assert len(content_queries) == 1
    assert all(e.pk.hex in content_queries[0].replace("-", "") for e in page)
    assert all(
        e.pk.hex not in content_queries[0].replace("-", "") for e in metadata if e.pk not in {row.pk for row in page}
    )


@pytest.mark.parametrize(
    "headers", [{}, {"HTTP_HX_REQUEST": "true"}, {"HTTP_HX_REQUEST": "true", "HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}]
)
def test_filtered_pages_use_the_same_cards(client, staff_user, sample_event, monkeypatch, headers):
    from core.views.event_list import EventListView

    monkeypatch.setattr(EventListView, "page_size", 1)
    Event.objects.create(
        facility=sample_event.facility,
        document_type=sample_event.document_type,
        occurred_at=sample_event.occurred_at,
        data_json={"dauer": 15, "notiz": "Testnotiz"},
    )
    response = list_response(
        client, staff_user, {"doc_type": str(sample_event.document_type_id), "page": "2"}, **headers
    )
    html = Elements(response.content)
    assert response.context["events"].number == 2
    event = response.context["events"][0]
    assert len(html.find("button", **{"aria-expanded": "false", "aria-controls": f"event-detail-{event.pk}"})) == 1
    assert bool(html.find("html")) is (not headers or "HTTP_HX_HISTORY_RESTORE_REQUEST" in headers)
    assert b"Testnotiz" in response.content
    assert not html.find("table")
    assert b"zeitstrom-collapse-all" not in response.content
