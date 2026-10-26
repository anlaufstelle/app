"""The review list is paginated in the database; work is bounded by the page, not by the table."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, FreeTextReview
from core.services import free_text_review as review_service

pytestmark = pytest.mark.django_db

PAGE_SIZE = 50


def make_review_events(facility, user, count, text="Neutral note {}", prefix="Bulk"):
    document_type = DocumentType.objects.create(facility=facility, name=f"{prefix} review")
    field, _ = FieldTemplate.objects.get_or_create(
        facility=facility, slug="notiz", defaults={"name": "Notiz", "field_type": "textarea"}
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    now = timezone.now()
    created = Event.objects.bulk_create(
        Event(
            facility=facility,
            document_type=document_type,
            occurred_at=now,
            data_json={"notiz": text.format(index)},
            created_by=user,
        )
        for index in range(count)
    )
    # Distinct, strictly ordered change times: index 0 is the oldest row.
    for index, event in enumerate(created):
        Event.objects.filter(pk=event.pk).update(updated_at=now - timedelta(minutes=count - index))
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


@pytest.mark.parametrize("model", ["Event"])
def test_list_query_count_is_constant_across_pages(
    client, facility, staff_user, client_identified, doc_type_contact, model
):
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    make_events(facility, staff_user, doc_type_contact, client_identified, 2 * PAGE_SIZE + 5)
    counts = {}
    for page in (1, 2, 3):
        counts[page], response = count_queries(client, url, {"model": model, "page": page})
    assert len(response.context["entries"]) == 5
    assert counts[1] == counts[2] == counts[3]


def test_only_the_displayed_page_is_processed(client, facility, staff_user, monkeypatch):
    make_review_events(facility, staff_user, PAGE_SIZE + 10)
    built = []
    original = review_service.salted_hmac

    def counting_hmac(*args, **kwargs):
        built.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(review_service, "salted_hmac", counting_hmac)
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    first = client.get(url, {"model": "Event"}).context
    assert len(first["entries"]) == PAGE_SIZE
    assert len(built) == PAGE_SIZE
    assert first["page_obj"].has_next()
    built.clear()
    second = client.get(url, {"model": "Event", "page": 2}).context
    assert len(second["entries"]) == 10
    assert len(built) == 10
    assert second["page_obj"].has_previous()
    assert not second["page_obj"].has_next()


def test_pages_are_ordered_newest_first_without_gaps(client, facility, staff_user):
    people = make_review_events(facility, staff_user, PAGE_SIZE + 3)
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    seen = [
        entry.target_id
        for page in (1, 2)
        for entry in client.get(url, {"model": "Event", "page": page}).context["entries"]
    ]
    assert seen == [person.pk for person in reversed(people)]


def test_risk_filter_pages_across_the_whole_table(client, facility, staff_user):
    make_review_events(facility, staff_user, PAGE_SIZE + 5, text="Mail me at person{}@example.org")
    make_review_events(facility, staff_user, 7, text="Nothing special {}", prefix="Other")
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    first = client.get(url, {"model": "Event", "risk": "email"}).context
    second = client.get(url, {"model": "Event", "risk": "email", "page": 2}).context
    assert len(first["entries"]) == PAGE_SIZE
    assert first["page_obj"].has_next()
    assert len(second["entries"]) == 5
    assert not second["page_obj"].has_next()
    assert {entry.target_id for entry in first["entries"]}.isdisjoint(entry.target_id for entry in second["entries"])


def test_changed_before_and_editor_filters_apply_before_paging(client, facility, staff_user, lead_user):
    people = make_review_events(facility, staff_user, PAGE_SIZE + 4)
    Event.objects.filter(pk=people[0].pk).update(created_by=lead_user)
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    only_lead = client.get(url, {"model": "Event", "editor": str(lead_user.pk)}).context
    assert [entry.target_id for entry in only_lead["entries"]] == [people[0].pk]
    assert not only_lead["page_obj"].has_next()
    future = (timezone.localdate() - timedelta(days=1)).isoformat()
    assert not client.get(url, {"model": "Event", "changed_before": future}).context["entries"]


def test_get_entry_ignores_marks_of_other_targets(facility, staff_user, lead_user, sample_event):
    sample_event.data_json = {"notiz": "Target text"}
    sample_event.save(update_fields=["data_json"])
    with CaptureQueriesContext(connection) as baseline:
        review_service.get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    others = make_review_events(facility, staff_user, 25)
    FreeTextReview.objects.bulk_create(
        FreeTextReview(
            facility=facility,
            target_type="Event",
            target_id=person.pk,
            field_key="notiz",
            reviewed_until=timezone.localdate(),
            reviewed_by=lead_user,
            content_fingerprint="x" * 64,
        )
        for person in others
    )
    with CaptureQueriesContext(connection) as loaded:
        entry = review_service.get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    assert entry.review is None
    assert len(loaded) == len(baseline)
    reviews_sql = [q["sql"] for q in loaded if "core_freetextreview" in q["sql"]]
    assert reviews_sql
    assert all(str(sample_event.pk).replace("-", "") in sql.replace("-", "") for sql in reviews_sql)


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


@pytest.mark.parametrize("raw", ["-3", "0", "abc", "1.5", "", "9" * 5000], ids=lambda raw: raw[:12] or "empty")
def test_malformed_page_numbers_fall_back_to_the_first_page(client, facility, staff_user, raw):
    make_review_events(facility, staff_user, 3)
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"), {"model": "Event", "page": raw})
    assert response.status_code == 200
    assert response.context["page_obj"].number == 1
    assert len(response.context["entries"]) == 3


@pytest.mark.parametrize(
    "raw", [str(2**63), "999999999999999999999999", "1" + "0" * 400], ids=["bigint", "24-digits", "401-digits"]
)
@pytest.mark.parametrize("risk", ["", "email"])
def test_page_numbers_beyond_the_supported_range_are_not_found(client, facility, staff_user, raw, risk):
    make_review_events(facility, staff_user, 1)
    client.force_login(staff_user)
    client.raise_request_exception = False
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("core:free_text_review_list"), {"model": "Event", "page": raw, "risk": risk})
    assert response.status_code == 404
    # No review query ran (the error page's own navigation counters are unrelated).
    assert not [q for q in queries if '"core_event"."data_json"' in q["sql"] or "core_freetextreview" in q["sql"]]


def test_last_supported_page_is_an_empty_page_not_an_error(client, facility, staff_user):
    make_review_events(facility, staff_user, 1)
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"), {"model": "Event", "page": review_service.MAX_PAGE})
    assert response.status_code == 200
    assert response.context["entries"] == []
    assert not response.context["page_obj"].has_next()


@pytest.mark.parametrize("change", ["unassign", "empty"])
def test_editor_choices_exclude_reviewers_without_visible_entries(
    client, facility, assistant_user, staff_user, lead_user, client_identified, doc_type_contact, change
):
    field = FieldTemplate.objects.create(facility=facility, name="Extra text", field_type="textarea")
    assignment = DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=field)
    other_type = DocumentType.objects.create(facility=facility, name="Other visible type")
    DocumentTypeField.objects.create(document_type=other_type, field_template=field)
    event = Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        client=client_identified,
        created_by=staff_user,
        occurred_at=timezone.now(),
        data_json={"notiz": "Still visible", field.slug: "Previously reviewed"},
    )
    entry = review_service.get_entry(facility, lead_user, "Event", event.pk, field.slug)
    review_service.mark_review(
        facility, lead_user, "Event", event.pk, field.slug, timezone.localdate(), entry.fingerprint
    )
    if change == "unassign":
        assignment.delete()
    else:
        event.data_json[field.slug] = ""
        event.save(update_fields=["data_json"])
    client.force_login(assistant_user)
    response = client.get(reverse("core:free_text_review_list"), {"model": "Event"})
    assert response.status_code == 200
    choices = dict(response.context["filter_form"].fields["editor"].choices)
    assert str(lead_user.pk) not in choices
    assert str(staff_user.pk) in choices


def test_editor_choices_keep_reviewers_of_currently_visible_fields(
    client, facility, assistant_user, staff_user, lead_user, client_identified, doc_type_contact
):
    event = make_events(facility, staff_user, doc_type_contact, client_identified, 1)[0]
    entry = review_service.get_entry(facility, lead_user, "Event", event.pk, "notiz")
    review_service.mark_review(facility, lead_user, "Event", event.pk, "notiz", timezone.localdate(), entry.fingerprint)
    client.force_login(assistant_user)
    response = client.get(reverse("core:free_text_review_list"), {"model": "Event"})
    assert response.status_code == 200
    assert str(lead_user.pk) in dict(response.context["filter_form"].fields["editor"].choices)


def test_numeric_field_slug_is_listed_and_filterable(client, facility, staff_user, client_identified, doc_type_contact):
    field = FieldTemplate.objects.create(facility=facility, name="123", field_type="textarea")
    field.full_clean()
    assert field.slug == "123"
    DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=field)
    event = Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        client=client_identified,
        created_by=staff_user,
        occurred_at=timezone.now(),
        data_json={field.slug: "Visible text"},
    )
    assert review_service.get_entry(facility, staff_user, "Event", event.pk, field.slug).text == "Visible text"
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    for params in ({"model": "Event"}, {"model": "Event", "editor": str(staff_user.pk)}):
        response = client.get(url, params)
        assert response.status_code == 200
        assert [(e.target_id, e.field_key) for e in response.context["entries"]] == [(event.pk, field.slug)]
