"""The review list is paginated in the database; work is bounded by the page, not by the table."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Client, DocumentTypeField, Event, FieldTemplate, FreeTextReview
from core.services import free_text_review as review_service

pytestmark = pytest.mark.django_db

PAGE_SIZE = 50


def make_clients(facility, user, count, notes="Neutral note {}", prefix="Bulk"):
    now = timezone.now()
    rows = [
        Client(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=f"{prefix}-{index:03d}",
            notes=notes.format(index),
            created_by=user,
        )
        for index in range(count)
    ]
    created = Client.objects.bulk_create(rows)
    # Distinct, strictly ordered change times: index 0 is the oldest row.
    for index, person in enumerate(created):
        Client.objects.filter(pk=person.pk).update(updated_at=now - timedelta(minutes=count - index))
    return created


def make_events(facility, user, document_type, person, count):
    return Event.objects.bulk_create(
        Event(
            facility=facility,
            client=person,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={"notiz": f"Event note {index}"},
            created_by=user,
        )
        for index in range(count)
    )


def count_queries(client, url, params=None):
    client.get(url, params or {})  # warm per-session caches so only list work is counted
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url, params or {})
    assert response.status_code == 200
    return len(queries), response


def test_event_list_query_count_is_independent_of_row_count(
    client, facility, staff_user, client_identified, doc_type_contact
):
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    make_events(facility, staff_user, doc_type_contact, client_identified, 3)
    few, response = count_queries(client, url, {"model": "Event"})
    assert len(response.context["entries"]) == 3
    make_events(facility, staff_user, doc_type_contact, client_identified, 40)
    many, response = count_queries(client, url, {"model": "Event"})
    assert len(response.context["entries"]) == 43
    assert many == few


@pytest.mark.parametrize("model", ["Client", "Event"])
def test_list_query_count_is_constant_across_pages(
    client, facility, staff_user, client_identified, doc_type_contact, model
):
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    if model == "Client":
        make_clients(facility, staff_user, 2 * PAGE_SIZE + 5)
    else:
        make_events(facility, staff_user, doc_type_contact, client_identified, 2 * PAGE_SIZE + 5)
    counts = {}
    for page in (1, 2, 3):
        counts[page], response = count_queries(client, url, {"model": model, "page": page})
    assert len(response.context["entries"]) == 5
    assert counts[1] == counts[2] == counts[3]


def test_only_the_displayed_page_is_processed(client, facility, staff_user, monkeypatch):
    make_clients(facility, staff_user, PAGE_SIZE + 10)
    built = []
    original = review_service.salted_hmac

    def counting_hmac(*args, **kwargs):
        built.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(review_service, "salted_hmac", counting_hmac)
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    first = client.get(url).context
    assert len(first["entries"]) == PAGE_SIZE
    assert len(built) == PAGE_SIZE
    assert first["page_obj"].has_next()
    built.clear()
    second = client.get(url, {"page": 2}).context
    assert len(second["entries"]) == 10
    assert len(built) == 10
    assert second["page_obj"].has_previous()
    assert not second["page_obj"].has_next()


def test_pages_are_ordered_newest_first_without_gaps(client, facility, staff_user):
    people = make_clients(facility, staff_user, PAGE_SIZE + 3)
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    seen = [entry.target_id for page in (1, 2) for entry in client.get(url, {"page": page}).context["entries"]]
    assert seen == [person.pk for person in reversed(people)]


def test_risk_filter_pages_across_the_whole_table(client, facility, staff_user):
    make_clients(facility, staff_user, PAGE_SIZE + 5, notes="Mail me at person{}@example.org")
    make_clients(facility, staff_user, 7, notes="Nothing special {}", prefix="Other")
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    first = client.get(url, {"risk": "email"}).context
    second = client.get(url, {"risk": "email", "page": 2}).context
    assert len(first["entries"]) == PAGE_SIZE
    assert first["page_obj"].has_next()
    assert len(second["entries"]) == 5
    assert not second["page_obj"].has_next()
    assert {entry.target_id for entry in first["entries"]}.isdisjoint(entry.target_id for entry in second["entries"])


def test_changed_before_and_editor_filters_apply_before_paging(client, facility, staff_user, lead_user):
    people = make_clients(facility, staff_user, PAGE_SIZE + 4)
    Client.objects.filter(pk=people[0].pk).update(created_by=lead_user)
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    only_lead = client.get(url, {"editor": str(lead_user.pk)}).context
    assert [entry.target_id for entry in only_lead["entries"]] == [people[0].pk]
    assert not only_lead["page_obj"].has_next()
    future = (timezone.localdate() - timedelta(days=1)).isoformat()
    assert not client.get(url, {"changed_before": future}).context["entries"]


def test_get_entry_ignores_marks_of_other_targets(facility, staff_user, lead_user, client_identified):
    client_identified.notes = "Target text"
    client_identified.save(update_fields=["notes"])
    with CaptureQueriesContext(connection) as baseline:
        review_service.get_entry(facility, staff_user, "Client", client_identified.pk, "notes")
    others = make_clients(facility, staff_user, 25)
    FreeTextReview.objects.bulk_create(
        FreeTextReview(
            facility=facility,
            target_type="Client",
            target_id=person.pk,
            field_key="notes",
            reviewed_until=timezone.localdate(),
            reviewed_by=lead_user,
            content_fingerprint="x" * 64,
        )
        for person in others
    )
    with CaptureQueriesContext(connection) as loaded:
        entry = review_service.get_entry(facility, staff_user, "Client", client_identified.pk, "notes")
    assert entry.review is None
    assert len(loaded) == len(baseline)
    reviews_sql = [q["sql"] for q in loaded if "core_freetextreview" in q["sql"]]
    assert reviews_sql
    assert all(str(client_identified.pk).replace("-", "") in sql.replace("-", "") for sql in reviews_sql)


def test_editor_filter_ignores_marks_of_hidden_event_fields_before_paging(
    client, facility, assistant_user, lead_user, staff_user, client_identified, doc_type_contact
):
    restricted = FieldTemplate.objects.create(
        facility=facility, name="Restricted review", field_type="textarea", sensitivity="elevated"
    )
    DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=restricted)
    events = make_events(facility, staff_user, doc_type_contact, client_identified, PAGE_SIZE + 1)
    Event.objects.filter(pk__in=[e.pk for e in events]).update(
        data_json={"notiz": "Visible ordinary text", restricted.slug: "Restricted text"}
    )
    oldest = events[0]
    Event.objects.filter(pk=oldest.pk).update(updated_at=timezone.now() - timedelta(days=1))
    # The lead only marks the elevated field of the 50 newer events, and the visible one of the oldest.
    for event in events:
        key = "notiz" if event.pk == oldest.pk else restricted.slug
        entry = review_service.get_entry(facility, lead_user, "Event", event.pk, key)
        review_service.mark_review(facility, lead_user, "Event", event.pk, key, timezone.localdate(), entry.fingerprint)
    client.force_login(assistant_user)
    url = reverse("core:free_text_review_list")
    response = client.get(url, {"model": "Event", "editor": str(lead_user.pk)})
    assert response.status_code == 200
    assert [entry.target_id for entry in response.context["entries"]] == [oldest.pk]
    assert not response.context["page_obj"].has_next()


def test_event_pages_skip_events_without_a_nonblank_text_value(
    client, facility, staff_user, client_identified, doc_type_contact
):
    events = make_events(facility, staff_user, doc_type_contact, client_identified, PAGE_SIZE + 5)
    unusable = [{"notiz": ""}, {"notiz": "   "}, {"notiz": None}, {"notiz": 15}, {"notiz": {"marker": 1}}]
    for index, event in enumerate(events[1:]):
        Event.objects.filter(pk=event.pk).update(data_json=unusable[index % len(unusable)])
    Event.objects.filter(pk=events[0].pk).update(updated_at=timezone.now() - timedelta(days=1))
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"), {"model": "Event"})
    assert response.status_code == 200
    assert [entry.target_id for entry in response.context["entries"]] == [events[0].pk]
    assert not response.context["page_obj"].has_next()
