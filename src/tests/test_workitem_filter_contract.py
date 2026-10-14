"""Rendered inbox filter and selection contracts."""

from html.parser import HTMLParser

import pytest
from django.urls import reverse


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.select_values = {}
        self.current_select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "select":
            self.current_select = attrs.get("id")
        elif tag == "option" and self.current_select:
            if self.current_select not in self.select_values or "selected" in attrs:
                self.select_values[self.current_select] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select":
            self.current_select = None


@pytest.mark.django_db
def test_each_filter_includes_exactly_the_other_five(client, staff_user):
    client.force_login(staff_user)
    controls = Controls(client.get(reverse("core:workitem_inbox")).content.decode())
    ids = {"filter-" + name for name in ("item-type", "priority", "assigned-to", "due", "status", "done-period")}
    for control_id in ids:
        matches = [attrs for _, attrs in controls.elements if attrs.get("id") == control_id]
        assert len(matches) == 1
        assert matches[0]["hx-replace-url"] == "true"
        include = matches[0]["hx-include"].split(",")
        assert len(include) == 5
        assert set(include) == {"#" + other for other in ids - {control_id}}


@pytest.mark.django_db
def test_selection_root_resynchronizes_after_swaps_and_details(client, staff_user):
    client.force_login(staff_user)
    controls = Controls(client.get(reverse("core:workitem_inbox")).content.decode())
    root = next(attrs for _, attrs in controls.elements if attrs.get("x-data") == "workitemBulkSelect")
    assert root.get("@htmx:after-settle.window") == "syncFromDom"
    assert root.get("@toggle.capture") == "syncFromDom"


@pytest.mark.django_db
def test_combined_filters_keep_partial_url_and_reload_consistent(client, staff_user, facility):
    from urllib.parse import parse_qs, urlsplit

    from django.utils import timezone

    from core.models import WorkItem

    wanted = WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        assigned_to=staff_user,
        title="Combined match",
        status="done",
        completed_at=timezone.now(),
        item_type="task",
        priority="urgent",
    )
    WorkItem.objects.create(facility=facility, created_by=staff_user, title="Excluded open task", priority="urgent")
    client.force_login(staff_user)
    params = {
        "status": "done",
        "done_period": "30",
        "item_type": "task",
        "priority": "urgent",
        "assigned_to": str(staff_user.pk),
        "due": "",
    }
    url = reverse("core:workitem_inbox")
    initial = client.get(url, {**params, "priority": "normal"})
    assert list(initial.context["done_full_items"]) == []
    controls = Controls(initial.content.decode())
    selects = {attrs["id"]: attrs for tag, attrs in controls.elements if tag == "select" and "hx-get" in attrs}
    changed = selects["filter-priority"]
    assert changed["hx-trigger"] == "change"
    assert changed["hx-target"] == "#inbox-content"
    assert changed["hx-replace-url"] == "true"
    included_ids = [selector.removeprefix("#") for selector in changed["hx-include"].split(",")]
    request_params = {selects[key]["name"]: controls.select_values[key] for key in included_ids}
    request_params[changed["name"]] = "urgent"
    partial = client.get(
        changed["hx-get"],
        request_params,
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=initial.wsgi_request.build_absolute_uri(),
    )
    assert partial.status_code == 200
    assert "core/workitems/partials/inbox_content.html" in [template.name for template in partial.templates]
    assert "core/workitems/inbox.html" not in [template.name for template in partial.templates]
    assert wanted.title in partial.content.decode()
    assert "Excluded open task" not in partial.content.decode()
    observed_url = partial.wsgi_request.get_full_path()
    assert parse_qs(urlsplit(observed_url).query, keep_blank_values=True) == {
        key: [value] for key, value in params.items()
    }
    full = client.get(observed_url)
    for response in [partial, full]:
        assert list(response.context["done_full_items"]) == [wanted]
        assert response.context["show_open"] is False
        assert response.context["selected_status"] == "done"
        assert response.context["selected_done_period"] == "30"
        for key, value in params.items():
            assert response.context["selected_" + key] == value


@pytest.mark.django_db
def test_row_action_does_not_insert_widget_hidden_by_status_filter(client, staff_user, facility):
    import json

    from core.models import WorkItem

    item = WorkItem.objects.create(facility=facility, created_by=staff_user, title="Open-filter task")
    client.force_login(staff_user)
    controls = Controls(client.get(reverse("core:workitem_inbox"), {"status": "open"}).content.decode())
    payloads = [
        json.loads(attrs["hx-vals"]) for tag, attrs in controls.elements if tag == "button" and "hx-vals" in attrs
    ]
    recent_payloads = [payload for payload in payloads if payload.get("recent_done_target") == "1"]
    assert recent_payloads
    assert all(payload["filter_status"] == "open" for payload in recent_payloads)
    response = client.post(
        reverse("core:workitem_status_update", args=[item.pk]),
        {
            "status": "done",
            "hide": "1",
            "recent_done_target": "1",
            "item_type": "task",
            "priority": "normal",
            "assigned_to": "",
            "due": "",
            "filter_status": "open",
        },
        HTTP_HX_REQUEST="true",
    )
    assert "afterbegin:#recent-done-items" not in response.content.decode()
