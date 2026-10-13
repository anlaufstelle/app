"""Edit and timeline reads exclude revoked people across every feed branch."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, User
from core.services.case.workitems import create_workitem, update_workitem
from core.services.client import approve_client_deletion, request_client_deletion, restore_client
from core.services.dashboard.activity import log_activity
from core.services.zeitstrom_watch import build_zeitstrom_page_scope
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["staff", "lead"])
def reader(request, facility):
    return User.objects.create_user(username=f"read-boundary-{request.param}", role=request.param, facility=facility)


@pytest.fixture(params=["trash", "inactive"])
def revoked_person(request, facility, staff_user, lead_user):
    person = make_client(facility=facility, pseudonym="REVOKED_PERSON_MARKER", created_by=staff_user)

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


@pytest.mark.parametrize("method", ["get", "post"])
def test_edit_reads_follow_person_revocation(client, facility, reader, revoked_person, method):
    person, revoke, restore = revoked_person
    item = create_workitem(facility, reader, client=person, title="REVOKED_TASK_MARKER", description="Private detail")
    team = create_workitem(facility, reader, title="Team task")
    client.force_login(reader)
    request = getattr(client, method)
    url = reverse("core:workitem_update", kwargs={"pk": item.pk})
    team_url = reverse("core:workitem_update", kwargs={"pk": team.pk})
    assert request(url).status_code == 200
    revoke()
    response = request(url)
    assert response.status_code == 404
    assert "REVOKED_TASK_MARKER" not in response.content.decode()
    assert request(team_url).status_code == 200
    item.refresh_from_db()
    assert item.title == "REVOKED_TASK_MARKER"
    restore()
    assert request(url).status_code == 200


@pytest.fixture
def timeline_data(facility, reader, revoked_person):
    person, revoke, restore = revoked_person
    live = make_client(facility=facility, pseudonym="Live person", created_by=reader)
    document_type = make_document_type(facility=facility, name="Contact", sensitivity="normal")
    ban_type = make_document_type(facility=facility, name="Ban", sensitivity="normal", system_type="ban")
    targets = {}
    for label, subject in [("hidden", person), ("live", live), ("team", None)]:
        task = create_workitem(facility, reader, client=subject, title=f"{label} task")
        update_workitem(task, reader, title=f"{label} updated task")
        event = make_event(
            facility=facility,
            client=subject,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=reader,
        )
        ban = make_event(
            facility=facility,
            client=subject,
            document_type=ban_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=reader,
        )
        log_activity(
            facility=facility,
            actor=reader,
            verb=Activity.Verb.UPDATED,
            target=event,
            action=Activity.Action.EVENT_CREATED,
        )
        if subject:
            log_activity(
                facility=facility,
                actor=reader,
                verb=Activity.Verb.UPDATED,
                target=subject,
                action=Activity.Action.CLIENT_UPDATED,
            )
        targets[label] = {task.pk, event.pk, ban.pk} | ({subject.pk} if subject else set())
    revoke()
    return person, targets, restore


@pytest.mark.parametrize("feed_type", ["events", "activities", "bans", "workitems", ""])
def test_timeline_branches_hide_revoked_targets(client, facility, reader, timeline_data, feed_type):
    person, targets, restore = timeline_data
    client.force_login(reader)
    params = {"type": feed_type, "time_filter": "all", "date": timezone.localdate().isoformat()}

    def read_targets():
        response = client.get(reverse("core:zeitstrom"), params)
        assert response.status_code == 200
        entries = response.context["feed_items"]
        ids = {entry["object"].target_id if entry["type"] == "activity" else entry["object"].pk for entry in entries}
        return response, ids

    response, ids = read_targets()
    assert ids.isdisjoint(targets["hidden"])
    assert "REVOKED_PERSON_MARKER" not in response.content.decode()
    assert "hidden updated task" not in response.content.decode()
    assert ids & targets["live"]
    assert ids & targets["team"]
    restore()
    _, ids = read_targets()
    assert ids & targets["hidden"]


@pytest.mark.parametrize("feed_type", ["events", "activities", "bans", ""])
def test_timeline_watch_ignores_revoked_targets(client, facility, reader, timeline_data, feed_type):
    person, targets, _ = timeline_data
    client.force_login(reader)
    params = {"type": feed_type, "time_filter": "all", "date": timezone.localdate().isoformat()}
    headers = {"X-Zeitstrom-Page-Scope": build_zeitstrom_page_scope(reader, facility)}
    url = reverse("core:zeitstrom_watch")
    first = client.get(url, params, headers=headers)
    assert first.status_code == 200
    from core.models import Event, WorkItem

    person.pseudonym = "CHANGED_REVOKED_PERSON_MARKER"
    person.save(update_fields=["pseudonym"])
    WorkItem.objects.for_facility(facility).filter(pk__in=targets["hidden"]).update(title="Changed hidden title")
    for event in Event.objects.for_facility(facility).filter(pk__in=targets["hidden"]):
        event.occurred_at -= timedelta(minutes=1)
        event.save(update_fields=["occurred_at"])
    for activity in Activity.objects.for_facility(facility).filter(target_id__in=targets["hidden"]):
        activity.occurred_at -= timedelta(minutes=1)
        activity.save(update_fields=["occurred_at"])
    second = client.get(url, params, headers=headers)
    assert second.status_code == 200
    assert second.json()["validators"]["feed"] == first.json()["validators"]["feed"]
