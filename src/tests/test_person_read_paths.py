"""Normal record readers share the live-person boundary, including response fragments."""

from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Event, User
from core.services.case.cases import close_case, create_case
from core.services.case.workitems import create_workitem
from core.services.client import approve_client_deletion, request_client_deletion, restore_client
from core.services.zeitstrom_watch import build_zeitstrom_page_scope
from tests._factories import make_client, make_document_type, make_event
from tests.conftest import _make_attachment

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["assistant", "staff", "lead", "facility_admin"])
def reader(request, facility):
    return User.objects.create_user(username=f"record-reader-{request.param}", role=request.param, facility=facility)


@pytest.fixture(params=["trash", "inactive"])
def person_boundary(request, facility, staff_user, lead_user):
    person = make_client(facility=facility, pseudonym="PRIVATE_PERSON_MARKER", created_by=staff_user)

    def revoke():
        if request.param == "trash":
            pending = request_client_deletion(person, staff_user, "Consent withdrawn")
            approve_client_deletion(pending, lead_user)
        else:
            person.is_active = False
            person.save(update_fields=["is_active"])

    def restore():
        if request.param == "trash":
            restore_client(person, lead_user)
        else:
            person.is_active = True
            person.save(update_fields=["is_active"])

    return person, revoke, restore


@pytest.mark.parametrize("feed_type", ["activities", ""])
def test_case_watch_ignores_revoked_targets(client, facility, staff_user, lead_user, person_boundary, feed_type):
    person, revoke, _ = person_boundary
    case = create_case(facility, staff_user, person, title="Private case")
    close_case(case, staff_user)
    revoke()
    params = {"type": feed_type, "date": timezone.localdate().isoformat(), "time_filter": "all"}
    for user in [staff_user, lead_user]:
        client.force_login(user)
        headers = {"X-Zeitstrom-Page-Scope": build_zeitstrom_page_scope(user, facility)}
        url = reverse("core:zeitstrom_watch")
        first = client.get(url, params, headers=headers)
        assert first.status_code == 200
        case.title = f"Changed private case {user.pk}"
        case.save(update_fields=["title"])
        second = client.get(url, params, headers=headers)
        assert second.status_code == 200
        assert second.json()["validators"]["feed"] == first.json()["validators"]["feed"]


@pytest.mark.parametrize("feed_type", ["activities", ""])
def test_case_activity_read_and_watch_boundary(client, facility, staff_user, lead_user, person_boundary, feed_type):
    person, revoke, restore = person_boundary
    hidden = create_case(facility, staff_user, person, title="PRIVATE_CASE_MARKER")
    close_case(hidden, staff_user)
    peer = make_client(facility=facility, pseudonym="Live peer", created_by=staff_user)
    visible = create_case(facility, staff_user, peer, title="Visible case")
    close_case(visible, staff_user)
    params = {"type": feed_type, "date": timezone.localdate().isoformat(), "time_filter": "all"}
    for user in [staff_user, lead_user]:
        client.force_login(user)
        response = client.get(reverse("core:zeitstrom"), params)
        assert hidden.title in response.content.decode()
    revoke()
    for user in [staff_user, lead_user]:
        client.force_login(user)
        response = client.get(reverse("core:zeitstrom"), params)
        assert response.status_code == 200
        ids = {entry["object"].target_id for entry in response.context["feed_items"] if entry["type"] == "activity"}
        assert hidden.pk not in ids
        assert visible.pk in ids
        assert hidden.title not in response.content.decode()
        headers = {"X-Zeitstrom-Page-Scope": build_zeitstrom_page_scope(user, facility)}
        url = reverse("core:zeitstrom_watch")
        first = client.get(url, params, headers=headers)
        assert first.status_code == 200
        Case.objects.for_facility(facility).filter(pk=hidden.pk).update(title=f"Changed hidden case {user.pk}")
        second = client.get(url, params, headers=headers)
        assert second.status_code == 200
        assert second.json()["validators"]["feed"] == first.json()["validators"]["feed"]
    restore()
    response = client.get(reverse("core:zeitstrom"), params)
    assert hidden.pk in {
        entry["object"].target_id for entry in response.context["feed_items"] if entry["type"] == "activity"
    }


@pytest.mark.parametrize("surface", ["list", "list_partial", "detail", "edit", "attachment", "search", "csv"])
def test_event_read_boundary(client, facility, reader, person_boundary, surface, foreign_event):
    person, revoke, restore = person_boundary
    doc = make_document_type(facility=facility, name="Contact", sensitivity="normal")
    events = {}
    for name, subject in [
        ("hidden", person),
        ("team", None),
        ("live", make_client(facility=facility, pseudonym="Live peer", created_by=reader)),
    ]:
        events[name] = make_event(
            facility=facility,
            client=subject,
            document_type=doc,
            occurred_at=timezone.now(),
            data_json={},
            created_by=reader,
            search_text="Searchable contact",
        )
        Event.objects.for_facility(facility).filter(pk=events[name].pk).update(search_text="Searchable contact")
    event = events["hidden"]
    attachment = _make_attachment(facility, event, reader, ft_name="Contact file") if surface == "attachment" else None
    client.force_login(reader)

    def read(target=event):
        if surface in ["list", "list_partial"]:
            return client.get(
                reverse("core:event_list"), HTTP_HX_REQUEST="true" if surface == "list_partial" else "false"
            )
        if surface == "search":
            return client.get(reverse("core:search"), {"q": "Searchable"})
        if surface == "csv":
            day = timezone.localdate().isoformat()
            return client.get(reverse("core:statistics_csv_export"), {"date_from": day, "date_to": day})
        if surface == "attachment":
            return client.get(
                reverse("core:attachment_download", kwargs={"pk": target.pk, "attachment_pk": attachment.pk})
            )
        name = "event_update" if surface == "edit" else "event_detail"
        return client.get(reverse(f"core:{name}", kwargs={"pk": target.pk}))

    before = read()
    if surface == "csv" and not reader.is_lead_or_admin:
        assert before.status_code == 403
        revoke()
        assert read().status_code == 403
        return
    assert before.status_code == 200
    if surface == "search":
        assert {item.pk for item in before.context["events"]} == {item.pk for item in events.values()}
    if surface in ["detail", "edit", "attachment"]:
        assert read(foreign_event).status_code == 404
    revoke()
    response = read()
    if surface in ["detail", "edit", "attachment"]:
        assert response.status_code == 404
        assert person.pseudonym not in response.content.decode()
        if surface != "attachment":
            for name in ["team", "live"]:
                assert read(events[name]).status_code == 200
    else:
        assert response.status_code == 200
        body = b"".join(response.streaming_content) if response.streaming else response.content
        assert person.pseudonym not in body.decode()
        if surface in ["list", "list_partial"] or surface == "search":
            assert {item.pk for item in response.context["events"]} == {events["team"].pk, events["live"].pk}
        else:
            assert "Live peer" in body.decode()
            assert body.decode().count("Contact") == 2
    restore()
    restored = read()
    assert restored.status_code == 200
    if restored.streaming:
        body = b"".join(restored.streaming_content)
    else:
        body = restored.content
    if surface != "attachment":
        assert person.pseudonym in body.decode()
    else:
        assert body.startswith(b"%PDF")


@pytest.mark.parametrize("replay", [False, True])
def test_status_responses_hide_revoked_task(client, facility, reader, person_boundary, replay):
    person, revoke, restore = person_boundary
    task = create_workitem(
        facility, reader, client=person, title="PRIVATE_TASK_MARKER", description="PRIVATE_DESCRIPTION_MARKER"
    )
    url = reverse("core:workitem_status_update", kwargs={"pk": task.pk})
    headers = (
        {"HTTP_HX_REQUEST": "true", "HTTP_X_IDEMPOTENCY_KEY": str(uuid4())}
        if replay
        else {"HTTP_ACCEPT": "application/json"}
    )
    client.force_login(reader)
    if replay:
        response = client.post(url, {"status": "done"}, **headers)
        assert response.status_code == 200
        assert task.title in response.content.decode()
    task.refresh_from_db()
    data = {"status": "done", "expected_updated_at": task.updated_at.isoformat()}
    revoke()
    response = client.post(url, data if not replay else {"status": "done"}, **headers)
    assert response.status_code == 404
    assert task.title not in response.content.decode()
    assert task.description not in response.content.decode()
    task.refresh_from_db()
    assert task.status == ("done" if replay else "open")
    restore()
    assert client.post(url, data if not replay else {"status": "done"}, **headers).status_code in [200, 302]


def test_recent_done_fragment_matches_readable_inbox(client, facility, reader, person_boundary):
    person, revoke, _ = person_boundary
    hidden = create_workitem(facility, reader, client=person, title="Hidden done task")
    team = create_workitem(facility, reader, title="Team task")
    client.force_login(reader)
    url = reverse("core:workitem_status_update", kwargs={"pk": hidden.pk})
    assert client.post(url, {"status": "done"}, HTTP_HX_REQUEST="true").status_code == 200
    revoke()
    response = client.post(
        reverse("core:workitem_status_update", kwargs={"pk": team.pk}),
        {"status": "done", "hide": "1", "recent_done_target": "1"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert 'aria-live="polite">(1)</span>' in response.content.decode()
    assert hidden.title not in response.content.decode()
    inbox = client.get(reverse("core:workitem_inbox"))
    assert inbox.status_code == 200
    assert {item.pk for item in inbox.context["done_items"]} == {team.pk}
    assert "(1)</span>" in inbox.content.decode()


def test_pending_request_does_not_block_event_reads(client, facility, staff_user):
    person = make_client(facility=facility, pseudonym="Pending person", created_by=staff_user)
    doc = make_document_type(facility=facility, name="Contact")
    event = make_event(
        facility=facility,
        client=person,
        document_type=doc,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    request_client_deletion(person, staff_user, "Consent withdrawn")
    client.force_login(staff_user)
    assert client.get(reverse("core:event_detail", kwargs={"pk": event.pk})).status_code == 200
    assert event.pk in {item.pk for item in client.get(reverse("core:event_list")).context["events"]}
