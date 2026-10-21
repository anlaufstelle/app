"""Read surfaces exclude revoked people while retaining personless tasks."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import RecentClientVisit, TimeFilter, User, WorkItem
from core.services.client import approve_client_deletion, request_client_deletion, restore_client
from core.services.zeitstrom_watch import build_zeitstrom_page_scope
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db


ROLES = ["assistant", "staff", "lead", "facility_admin"]
# The cockpit is only rendered for assistant and staff roles.
COCKPIT_ROLES = {"assistant", "staff"}


def _role_surfaces(surfaces):
    return [(role, surface) for role in ROLES for surface in surfaces if surface != "cockpit" or role in COCKPIT_ROLES]


@pytest.mark.parametrize(
    ("role", "surface"),
    _role_surfaces(["inbox", "badges", "detail", "profile", "focus", "cockpit", "handover", "feed"]),
)
@pytest.mark.parametrize("revocation", ["trash", "inactive"])
def test_work_surfaces_follow_person_revocation_and_restoration(
    client, facility, second_facility, client_identified, staff_user, lead_user, role, surface, revocation
):
    reader = User.objects.create_user(username=f"surface-{role}", role=role, facility=facility)
    live = make_client(facility=facility, pseudonym="Live person", created_by=staff_user)
    foreign = make_client(facility=second_facility, pseudonym="Foreign person", created_by=None)
    items = {}
    for person in [client_identified, live, None, foreign]:
        key = person.pk if person else None
        items[key] = []
        statuses = ["open", "in_progress", "done"] if person is None else ["open", "in_progress", "done", "dismissed"]
        for status in statuses:
            items[key].append(
                WorkItem.objects.create(
                    facility=person.facility if person else facility,
                    client=person,
                    created_by=reader if person != foreign else None,
                    assigned_to=reader if person != foreign else None,
                    title=f"{person.pseudonym if person else 'Team'} {status}",
                    priority="urgent" if status in ["open", "in_progress"] else "normal",
                    status=status,
                    completed_at=timezone.now() if status in ["done", "dismissed"] else None,
                    due_date=timezone.localdate() - timedelta(days=1),
                )
            )
    for favorite, person in enumerate([client_identified, live]):
        RecentClientVisit.objects.create(user=reader, facility=facility, client=person, is_favorite=bool(favorite))
        doc_type = make_document_type(facility=facility, name=f"Contact {favorite}", sensitivity="normal")
        make_event(
            facility=facility,
            client=person,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=reader,
        )
    client.force_login(reader)

    def check_visible(include_target, favorite=False):
        if surface == "profile":
            RecentClientVisit.objects.filter(user=reader, client=client_identified).update(is_favorite=favorite)
        people = [live, None] + ([client_identified] if include_target else [])
        allowed = [item for person in people for item in items[person.pk if person else None]]
        open_ids = {item.pk for item in allowed if item.status in ["open", "in_progress"]}
        done_ids = {item.pk for item in allowed if item.status in ["done", "dismissed"]}
        if surface == "badges":
            response = client.get(reverse("core:workitem_inbox"))
            assert response.status_code == 200
            assert response.context["open_workitems_count"] == len(open_ids)
            assert response.context["overdue_workitems_count"] == len(open_ids)
        elif surface == "inbox":
            response = client.get(reverse("core:workitem_inbox"))
            assert response.status_code == 200
            assert {
                item.pk for item in response.context["open_items"] + response.context["in_progress_items"]
            } == open_ids
            assert {item.pk for item in response.context["done_items"]} == done_ids
            assert response.context["open_workitems_count"] == len(open_ids)
            assert response.context["overdue_workitems_count"] == len(open_ids)
            response = client.get(reverse("core:workitem_inbox"), {"status": "done", "done_period": "all"})
            assert {item.pk for item in response.context["done_full_items"]} == done_ids
        elif surface == "detail":
            for person in [live, None, client_identified, foreign]:
                item = items[person.pk if person else None][0]
                response = client.get(reverse("core:workitem_detail", kwargs={"pk": item.pk}))
                expected = 404 if person == foreign or (person == client_identified and not include_target) else 200
                assert response.status_code == expected
        elif surface == "profile":
            response = client.get(reverse("core:account_profile"))
            assert response.status_code == 200
            assert {item.pk for item in response.context["open_workitems"]} == open_ids
            assert {item.pk for item in response.context["done_workitems"]} == done_ids
            assert {visit.client_id for visit in response.context["recent_clients"]} == {
                person.pk for person in people if person
            }
            assert {event.client_id for event in response.context["recent_events"]} == {
                person.pk for person in people if person
            }
            assert response.context["stats"]["my_open_tasks"] == len(open_ids)
            assert response.context["stats"]["total_open_tasks"] == len(open_ids)
            assert response.context["stats"]["events_today"] == (2 if include_target else 1)
        elif surface == "focus":
            response = client.get(reverse("core:zeitstrom_focus_box_partial"))
            assert response.status_code == 200
            box = response.context["focus_box"]
            assert {item.pk for group in box["groups"] for item in group["items"]} == open_ids
            assert box["total_open_count"] == len(open_ids)
        elif surface == "cockpit":
            response = client.get(reverse("core:zeitstrom"))
            assert response.status_code == 200
            cockpit = response.context["cockpit"]
            assert {item.pk for item in cockpit["my_open_workitems"]}.issubset(open_ids)
            assert cockpit["my_open_workitems_count"] == len(open_ids)
            assert cockpit["my_overdue_count"] == len(open_ids)
            assert cockpit["today_events_count"] == (2 if include_target else 1)
        elif surface == "feed":
            response = client.get(reverse("core:zeitstrom"), {"type": "workitems"})
            assert response.status_code == 200
            assert {entry["object"].pk for entry in response.context["feed_items"] if entry["type"] == "workitem"} == {
                item.pk for item in allowed
            }
        else:
            response = client.get(
                reverse("core:zeitstrom"),
                {"view": "uebergabe", "date": timezone.localdate().isoformat(), "time_filter": "all"},
            )
            assert response.status_code == 200
            summary = response.context["uebergabe_summary"]
            assert {
                highlight["object"].pk for highlight in summary["highlights"] if highlight["type"] == "task"
            } == open_ids
            assert summary["stats"]["workitems_new"] == len(allowed)
            assert summary["stats"]["workitems_completed"] == sum(item.status == "done" for item in allowed)
            assert summary["stats"]["events_total"] == (2 if include_target else 1)

    check_visible(True)
    if surface == "profile":
        check_visible(True, favorite=True)
    if revocation == "trash":
        pending = request_client_deletion(client_identified, staff_user, "Consent withdrawn")
        approve_client_deletion(pending, lead_user)
    else:
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
    check_visible(False)
    if surface == "profile":
        check_visible(False, favorite=True)
    if revocation == "trash":
        restore_client(client_identified, lead_user)
    else:
        client_identified.is_active = True
        client_identified.save(update_fields=["is_active"])
    check_visible(True)
    if surface == "profile":
        check_visible(True, favorite=True)


@pytest.mark.parametrize(("role", "surface"), _role_surfaces(["profile", "cockpit", "handover"]))
def test_widgets_exclude_role_hidden_documentation(client, facility, client_identified, role, surface):
    reader = User.objects.create_user(username=f"hidden-{role}", role=role, facility=facility)
    visible = {"assistant": {"normal"}, "staff": {"normal", "elevated"}}.get(role, {"normal", "elevated", "high"})
    for sensitivity in ["normal", "elevated", "high"]:
        doc_type = make_document_type(facility=facility, name=sensitivity, sensitivity=sensitivity)
        for deleted in [False, True]:
            make_event(
                facility=facility,
                client=client_identified,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                created_by=reader,
                is_deleted=deleted,
            )
    client.force_login(reader)
    if surface == "profile":
        response = client.get(reverse("core:account_profile"))
        assert response.status_code == 200
        assert response.context["stats"]["events_today"] == len(visible)
        assert {event.document_type.sensitivity for event in response.context["recent_events"]} == visible
    elif surface == "cockpit":
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        assert response.context["cockpit"]["today_events_count"] == len(visible)
    else:
        response = client.get(
            reverse("core:zeitstrom"),
            {"view": "uebergabe", "date": timezone.localdate().isoformat(), "time_filter": "all"},
        )
        assert response.status_code == 200
        assert response.context["uebergabe_summary"]["stats"]["events_total"] == len(visible)


@pytest.mark.parametrize("component", ["focus_box", "current_shift", "feed"])
@pytest.mark.parametrize("revocation", ["trash", "inactive"])
@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
def test_watch_validators_ignore_tasks_of_revoked_people(
    client, facility, client_identified, staff_user, lead_user, component, revocation, role
):
    reader = User.objects.create_user(username=f"watch-{role}", role=role, facility=facility)
    TimeFilter.objects.create(facility=facility, label="Full day", start_time="00:00", end_time="23:59")
    item = WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        title="Hidden task",
        priority="urgent",
        due_date=timezone.localdate(),
    )
    if revocation == "trash":
        pending = request_client_deletion(client_identified, staff_user, "Consent withdrawn")
        approve_client_deletion(pending, lead_user)
    else:
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
    client.force_login(reader)
    params = {"type": "workitems" if component == "feed" else "events"}
    headers = {"X-Zeitstrom-Page-Scope": build_zeitstrom_page_scope(reader, facility)}
    url = reverse("core:zeitstrom_watch")
    first = client.get(url, params, headers=headers)
    assert first.status_code == 200
    item.title = "Changed hidden task"
    item.save(update_fields=["title", "updated_at"])
    second = client.get(url, params, headers=headers)
    assert second.status_code == 200
    assert second.json()["validators"][component] == first.json()["validators"][component]


@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
def test_current_shift_validator_ignores_documentation_of_trashed_people(
    client, facility, client_identified, staff_user, lead_user, role
):
    reader = User.objects.create_user(username=f"watch-event-{role}", role=role, facility=facility)
    TimeFilter.objects.create(facility=facility, label="Full day", start_time="00:00", end_time="23:59")
    doc_type = make_document_type(facility=facility, name="Crisis", sensitivity="normal", system_type="crisis")
    event = make_event(
        facility=facility,
        client=client_identified,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    pending = request_client_deletion(client_identified, staff_user, "Consent withdrawn")
    approve_client_deletion(pending, lead_user)
    client.force_login(reader)
    headers = {"X-Zeitstrom-Page-Scope": build_zeitstrom_page_scope(reader, facility)}
    url = reverse("core:zeitstrom_watch")
    first = client.get(url, {"type": "events"}, headers=headers)
    assert first.status_code == 200
    event.occurred_at -= timedelta(minutes=1)
    event.save(update_fields=["occurred_at"])
    second = client.get(url, {"type": "events"}, headers=headers)
    assert second.status_code == 200
    assert second.json()["validators"]["current_shift"] == first.json()["validators"]["current_shift"]
