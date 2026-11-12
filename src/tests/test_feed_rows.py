"""Shared timeline columns, badges and expansion across public read paths."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, DocumentType, Event, WorkItem
from tests.test_a11y_board_navigation import TreeParser
from tests.test_zeitstrom_watch import _watch

pytestmark = pytest.mark.django_db
COLUMNS = ["edge", "time", "type", "person", "details", "by"]


def _rows(response):
    assert response.status_code == 200
    root = TreeParser(response.content.decode()).root
    containers = [n for n in root.iter() if "feed-rows" in n.get("class", "").split()]
    assert len(containers) == 1
    rows = [n for n in containers[0].iter() if "feed-row-header" in n.get("class", "").split()]
    assert rows
    for row in rows:
        assert [child.get("data-feed-column") for child in row] == COLUMNS
    return root, rows


@pytest.mark.parametrize("path", ["timeline", "contacts", "search"])
def test_contact_columns_and_toggle_are_shared(client, staff_user, sample_event, path):
    Event.objects.filter(pk=sample_event.pk).update(search_text="COLUMN_NEEDLE")
    client.force_login(staff_user)
    if path == "timeline":
        response = client.get(reverse("core:zeitstrom_feed_partial"), {"time_filter": "all", "type": "events"})
    elif path == "contacts":
        response = client.get(reverse("core:event_list"))
    else:
        response = client.get(reverse("core:search"), {"q": "COLUMN_NEEDLE", "type": "event"})
    root, rows = _rows(response)
    assert not any(n.get("data-testid") == "feed-kind-badge" for n in root.iter())
    toggle = next(n for n in rows if n.get("data-testid") == "event-card-toggle")
    assert toggle.tag == "button"
    assert toggle.get("type") == "button"
    assert toggle.get("aria-expanded") == "false"
    assert toggle.get("aria-controls") == f"event-detail-{sample_event.pk}"
    assert list(toggle)[2].text == sample_event.document_type.name
    assert sample_event.client.pseudonym in "".join(list(toggle)[3].itertext())
    detail = next(n for n in root.iter() if n.get("id") == toggle.get("aria-controls"))
    assert detail.get("x-show") == "expanded"
    assert any(n.get("href") == reverse("core:event_detail", args=[sample_event.pk]) for n in detail.iter("a"))


def test_mixed_feed_uses_the_same_columns_for_all_card_kinds(client, staff_user, facility, sample_event):
    doc_type_ban = DocumentType.objects.create(facility=facility, name="Ban", system_type="ban")
    ban = Event.objects.create(
        facility=facility, document_type=doc_type_ban, occurred_at=timezone.now(), created_by=staff_user
    )
    task = WorkItem.objects.create(facility=facility, title="Column task", created_by=staff_user)
    Activity.objects.create(
        facility=facility,
        actor=staff_user,
        verb=Activity.Verb.UPDATED,
        target_type=ContentType.objects.get_for_model(Event),
        target_id=sample_event.pk,
    )
    client.force_login(staff_user)
    root, rows = _rows(client.get(reverse("core:zeitstrom_feed_partial"), {"time_filter": "all"}))
    badges = [n.text for n in root.iter() if n.get("data-testid") == "feed-kind-badge"]
    assert sorted(badges) == ["Aktivität", "Aufgabe", "Hausverbot"]
    assert len(rows) == 4
    links = {n.get("href") for n in rows if n.tag == "a"}
    assert reverse("core:event_detail", args=[ban.pk]) in links
    assert reverse("core:workitem_detail", args=[task.pk]) in links


def test_activity_person_column_change_moves_feed_validator(client, staff_user, facility, client_identified):
    Activity.objects.create(
        facility=facility,
        actor=staff_user,
        verb=Activity.Verb.UPDATED,
        target_type=ContentType.objects.get_for_model(client_identified),
        target_id=client_identified.pk,
    )
    client.force_login(staff_user)
    params = {"type": "activities", "time_filter": "all"}
    first = _watch(client, params=params)
    client_identified.pseudonym = "Changed column person"
    client_identified.save(update_fields=["pseudonym"])
    second = _watch(client, params=params)
    assert first.json()["validators"]["feed"] != second.json()["validators"]["feed"]
