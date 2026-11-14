"""Zeitstrom KPI row: every linked number equals the hit count of its target page."""

from __future__ import annotations

import re
from datetime import date, timedelta
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, Event, TimeFilter, WorkItem
from tests import test_bans as _bans

# Reuse the ban fixtures of the ban tests.
doc_type_hausverbot = _bans.doc_type_hausverbot
active_ban_event = _bans.active_ban_event


def _get(client, user, params=None):
    client.force_login(user)
    response = client.get(reverse("core:zeitstrom"), params or {})
    assert response.status_code == 200
    return response


def _chip(html, testid):
    """Return ``(count, href)`` of the KPI chip with *testid*, or ``None`` when absent."""
    match = re.search(rf'<(?:a|span|button)[^>]*data-testid="{re.escape(testid)}"[^>]*>', html)
    if match is None:
        return None
    tag = match.group(0)
    count = int(re.search(r'data-count="(\d+)"', tag).group(1))
    href = re.search(r'href="([^"]+)"', tag)
    return count, (unescape(href.group(1)) if href else None)


@pytest.fixture
def full_day_shift(facility):
    return TimeFilter.objects.create(
        facility=facility, label="Day shift", start_time="00:00", end_time="23:59", sort_order=0
    )


@pytest.fixture
def tally_type(facility):
    return DocumentType.objects.create(facility=facility, name="Tally count", tally_enabled=True)


def _event(facility, user, doc_type, *, client=None, via=Event.CreatedVia.STANDARD, anonymous=False, days_ago=0):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=days_ago),
        data_json={},
        created_by=user,
        created_via=via,
        is_anonymous=anonymous,
    )


def _list_total(client, href):
    response = client.get(href)
    assert response.status_code == 200
    return response.context["events"].paginator.count


@pytest.mark.django_db
class TestStructure:
    @pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user", "admin_user"])
    def test_every_role_gets_the_row_and_none_of_the_old_blocks(self, client, request, role, full_day_shift):
        html = _get(client, request.getfixturevalue(role)).content.decode()

        assert 'id="zeitstrom-kpi-slot"' in html
        for gone in ('data-testid="zeitstrom-cockpit"', 'data-testid="current-shift"', 'id="contacts-navigation"'):
            assert gone not in html
        assert "Aktueller Dienst" not in html
        assert "zeitstrom-current-shift-slot" not in html

    def test_handover_mode_keeps_the_full_summary_and_has_no_row(self, client, staff_user, full_day_shift):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        html = _get(client, staff_user, {"view": "uebergabe", "date": yesterday}).content.decode()

        assert 'id="zeitstrom-kpi-slot"' not in html
        assert "Neue Aufgaben" in html  # _summary_full

    def test_numbers_do_not_use_the_monospace_face(self, client, staff_user, full_day_shift):
        html = _get(client, staff_user).content.decode()
        slot = html[html.index('id="zeitstrom-kpi-slot"') :]
        slot = slot[: slot.index("</div>")]

        assert "font-mono" not in slot
        assert "tabular-nums" in slot

    def test_handover_stats_use_tabular_digits_not_mono(self):
        from django.template.loader import render_to_string

        html = render_to_string(
            "core/handover/partials/_stats_grid.html",
            {"summary": {"stats": {"events_total": 3, "workitems_new": 0, "clients_new": 1, "events_by_type": []}}},
        )

        assert "font-mono" not in html
        assert html.count("tabular-nums") >= 3


@pytest.mark.django_db
class TestContactsChip:
    def test_whole_day_counts_standard_contacts_and_matches_the_list(
        self, client, staff_user, facility, doc_type_contact, client_identified, tally_type
    ):
        _event(facility, staff_user, doc_type_contact, client=client_identified)
        _event(facility, staff_user, doc_type_contact, client=client_identified)
        _event(facility, staff_user, tally_type, anonymous=True, via=Event.CreatedVia.TALLY)
        _event(facility, staff_user, doc_type_contact, client=client_identified, days_ago=3)

        html = _get(client, staff_user, {"time_filter": "all"}).content.decode()

        count, href = _chip(html, "zeitstrom-contacts-list")
        assert count == 2
        assert parse_qs(urlparse(href).query)["created_via"] == ["standard"]
        assert _list_total(client, href) == count
        assert "Kontakte heute" in html

    def test_selected_shift_is_named_and_the_count_matches_the_list(
        self, client, staff_user, facility, doc_type_contact, client_identified, full_day_shift
    ):
        _event(facility, staff_user, doc_type_contact, client=client_identified)
        _event(facility, staff_user, doc_type_contact, client=client_identified, days_ago=1)

        html = _get(client, staff_user, {"time_filter": str(full_day_shift.pk)}).content.decode()

        count, href = _chip(html, "zeitstrom-contacts-list")
        assert count == 1
        assert parse_qs(urlparse(href).query)["time_filter"] == [str(full_day_shift.pk)]
        assert _list_total(client, href) == count
        assert "Kontakt im Dienst" in html

    def test_document_type_filter_narrows_number_and_list_alike(
        self, client, staff_user, facility, doc_type_contact, client_identified, doc_type_crisis
    ):
        _event(facility, staff_user, doc_type_contact, client=client_identified)
        _event(facility, staff_user, doc_type_crisis, client=client_identified)

        html = _get(client, staff_user, {"time_filter": "all", "doc_type": str(doc_type_contact.pk)}).content.decode()

        count, href = _chip(html, "zeitstrom-contacts-list")
        assert count == 1
        assert parse_qs(urlparse(href).query)["doc_type"] == [str(doc_type_contact.pk)]
        assert _list_total(client, href) == count

    def test_past_day_without_shift_does_not_say_today(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        _event(facility, staff_user, doc_type_contact, client=client_identified, days_ago=2)
        day = (timezone.localdate() - timedelta(days=2)).isoformat()

        html = _get(client, staff_user, {"time_filter": "all", "date": day}).content.decode()

        count, href = _chip(html, "zeitstrom-contacts-list")
        assert count == 1 and _list_total(client, href) == 1
        assert "Kontakt an diesem Tag" in html
        assert "heute" not in html[html.index('id="zeitstrom-kpi-slot"') :].split("</ul>")[0]

    def test_people_who_left_are_not_counted_like_in_the_list(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        _event(facility, staff_user, doc_type_contact, client=client_identified)
        client_identified.is_deleted = True
        client_identified.save(update_fields=["is_deleted"])

        html = _get(client, staff_user, {"time_filter": "all"}).content.decode()

        count, href = _chip(html, "zeitstrom-contacts-list")
        assert count == _list_total(client, href)


@pytest.mark.django_db
class TestTallyChip:
    def test_tally_total_matches_the_list_for_tally_only_tiles(
        self, client, staff_user, facility, tally_type, full_day_shift
    ):
        for _ in range(3):
            _event(facility, staff_user, tally_type, anonymous=True, via=Event.CreatedVia.TALLY)

        response = _get(client, staff_user, {"time_filter": "all"})
        html = response.content.decode()

        count, href = _chip(html, "zeitstrom-kpi-tally")
        assert count == sum(row["count"] for row in response.context["tally_summary"]["rows"]) == 3
        assert parse_qs(urlparse(href).query)["created_via"] == ["tally"]
        assert _list_total(client, href) == count

    def test_anonymous_standard_contacts_do_not_break_tally_link_count(
        self, client, staff_user, facility, tally_type, full_day_shift
    ):
        _event(facility, staff_user, tally_type, anonymous=True)
        _event(facility, staff_user, tally_type, anonymous=True, via=Event.CreatedVia.TALLY)

        response = _get(client, staff_user, {"time_filter": "all"})
        count, href = _chip(response.content.decode(), "zeitstrom-kpi-tally")

        assert parse_qs(urlparse(href).query)["created_via"] == ["tally"]
        assert count == _list_total(client, href) == 1
        assert {row["document_type"].pk: row["count"] for row in response.context["tally_summary"]["rows"]} == {
            tally_type.pk: 2
        }

    def test_tally_link_count_matches_after_a_type_is_disabled(
        self, client, staff_user, facility, tally_type, full_day_shift
    ):
        disabled = DocumentType.objects.create(facility=facility, name="Disabled tally", tally_enabled=False)
        _event(facility, staff_user, disabled, anonymous=True, via=Event.CreatedVia.TALLY)
        _event(facility, staff_user, tally_type, anonymous=True, via=Event.CreatedVia.TALLY)

        response = _get(client, staff_user, {"time_filter": "all"})
        count, href = _chip(response.content.decode(), "zeitstrom-kpi-tally")

        assert parse_qs(urlparse(href).query)["created_via"] == ["tally"]
        assert count == _list_total(client, href) == 2
        assert {row["document_type"].pk: row["count"] for row in response.context["tally_summary"]["rows"]} == {
            tally_type.pk: 1
        }

    def test_chip_is_absent_when_the_panel_is_not_shown(self, client, staff_user, facility, tally_type):
        day = (timezone.localdate() - timedelta(days=2)).isoformat()

        html = _get(client, staff_user, {"time_filter": "all", "date": day}).content.decode()

        assert _chip(html, "zeitstrom-kpi-tally") is None

    def test_tally_events_are_not_counted_as_documented_contacts(
        self, client, staff_user, facility, tally_type, full_day_shift
    ):
        _event(facility, staff_user, tally_type, anonymous=True, via=Event.CreatedVia.TALLY)

        html = _get(client, staff_user, {"time_filter": "all"}).content.decode()

        assert _chip(html, "zeitstrom-contacts-list")[0] == 0
        assert _chip(html, "zeitstrom-kpi-tally")[0] == 1


@pytest.fixture
def my_tasks(facility, staff_user, assistant_user):
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    def make(title, assignee, due):
        return WorkItem.objects.create(
            facility=facility,
            title=title,
            item_type=WorkItem.ItemType.TASK,
            assigned_to=assignee,
            created_by=staff_user,
            due_date=due,
        )

    make("mine", staff_user, today)
    make("mine overdue", staff_user, yesterday)
    make("team", None, yesterday)
    make("colleague", assistant_user, yesterday)


@pytest.mark.django_db
class TestTaskChips:
    @pytest.mark.parametrize(
        "testid,expected_due", [("zeitstrom-kpi-mine", None), ("zeitstrom-kpi-overdue", "overdue")]
    )
    def test_number_equals_the_rows_of_the_linked_inbox(self, client, staff_user, my_tasks, testid, expected_due):
        html = _get(client, staff_user).content.decode()

        count, href = _chip(html, testid)
        query = parse_qs(urlparse(href).query)
        assert query["assigned_to"] == ["me"]
        assert query.get("due", [None])[0] == expected_due
        target = client.get(href)
        rows = list(target.context["open_items"]) + list(target.context["in_progress_items"])
        assert count == len(rows) > 0

    def test_linked_inbox_names_filters_and_returns_to_the_broader_default(self, client, staff_user, my_tasks):
        html = _get(client, staff_user).content.decode()
        _, href = _chip(html, "zeitstrom-kpi-overdue")
        target = client.get(href)
        default = client.get(reverse("core:workitem_inbox"))

        assert target.status_code == default.status_code == 200
        assert target.context["has_active_filters"] is True
        assert target.context["selected_due"] == "overdue"
        assert default.context["has_active_filters"] is False
        filtered = target.context["open_items"] + target.context["in_progress_items"]
        unfiltered = default.context["open_items"] + default.context["in_progress_items"]
        assert len(unfiltered) > len(filtered)

    def test_personal_target_excludes_team_and_other_peoples_tasks(self, client, staff_user, my_tasks):
        html = _get(client, staff_user).content.decode()
        _, href = _chip(html, "zeitstrom-kpi-mine")
        target = client.get(href)
        assert target.status_code == 200
        rows = target.context["open_items"] + target.context["in_progress_items"]
        assert {item.title for item in rows} == {"mine", "mine overdue"}

    def test_team_and_colleague_tasks_are_not_mine(self, client, staff_user, my_tasks):
        html = _get(client, staff_user).content.decode()

        assert _chip(html, "zeitstrom-kpi-mine")[0] == 2
        assert _chip(html, "zeitstrom-kpi-overdue")[0] == 1

    def test_overdue_chip_is_red_only_when_something_is_overdue(self, client, staff_user, my_tasks):
        html = _get(client, staff_user).content.decode()
        tag = re.search(r'<a[^>]*data-testid="zeitstrom-kpi-overdue"[^>]*>', html).group(0)
        assert "text-red-600" in tag

        WorkItem.objects.filter(title="mine overdue").delete()
        html = _get(client, staff_user).content.decode()
        tag = re.search(r'<a[^>]*data-testid="zeitstrom-kpi-overdue"[^>]*>', html).group(0)
        assert "text-red-600" not in tag


@pytest.mark.django_db
class TestBanSafety:
    def test_active_ban_is_counted_and_its_details_stay_directly_below(
        self, client, staff_user, active_ban_event, full_day_shift
    ):
        html = _get(client, staff_user).content.decode()

        count, _ = _chip(html, "zeitstrom-kpi-bans")
        assert count == 1
        assert html.index("zeitstrom-kpi-bans") < html.index('id="zeitstrom-bans-slot"')
        slot = html[html.index('id="zeitstrom-bans-slot"') :]
        assert "Hausverbot:" in slot and "Bedrohung" in slot

    def test_ban_figure_is_a_switch_for_the_details_and_leads_the_row(
        self, client, staff_user, active_ban_event, full_day_shift
    ):
        html = _get(client, staff_user).content.decode()

        tag = re.search(r'<[^>]*data-testid="zeitstrom-kpi-bans"[^>]*>', html).group(0)
        assert tag.startswith("<button")
        assert 'aria-controls="zeitstrom-ban-details"' in tag and 'aria-expanded="true"' in tag
        assert 'id="zeitstrom-ban-details"' in html
        assert html.index("zeitstrom-kpi-bans") < html.index("zeitstrom-contacts-list")

    def test_chip_is_absent_without_active_bans(self, client, staff_user, full_day_shift):
        html = _get(client, staff_user).content.decode()

        assert _chip(html, "zeitstrom-kpi-bans") is None

    def test_ban_notice_is_visible_at_every_width(self, client, staff_user, active_ban_event, full_day_shift):
        html = _get(client, staff_user).content.decode()
        chip = re.search(r'<[^>]*data-testid="zeitstrom-kpi-bans"[^>]*>', html).group(0)
        details = html[html.index('id="zeitstrom-bans-slot"') :][:900]

        for part in (chip, details):
            assert not re.search(r"(?<![\w:-])hidden(?![\w-])", part)
            assert not re.search(r"\b(sm|md|lg):hidden\b", part)


def _watch_kpi_validator(client, params):
    from tests.test_zeitstrom_watch import _watch

    response = _watch(client, params=params)
    assert response.status_code == 200
    return response.json()["validators"].get("kpi")


@pytest.mark.django_db
class TestLiveUpdates:
    """The KPI row follows +1, undo and other users' changes without a reload."""

    def _increment(self, client, page):
        from urllib.parse import parse_qsl

        from tests._tally_helpers import post_tally

        row = page.context["tally_summary"]["rows"][0]
        payload = dict(parse_qsl(urlparse(row["tally_increment_url"]).query))
        response = post_tally(client, payload, headers={"HX-Request": "true"})
        assert response.status_code == 200
        return response.context["tally_undo_token"]

    def _feed_refresh_chip(self, client, params):
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {**params, "feed_only": "1"},
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        html = response.content.decode()
        assert 'hx-swap-oob="outerHTML"' in html
        return _chip(html, "zeitstrom-kpi-tally")

    def test_tally_total_follows_plus_one_and_undo(self, client, staff_user, facility, tally_type, full_day_shift):
        page = _get(client, staff_user, {"time_filter": "all"})
        params = page.context["zeitstrom_filter_state"]
        assert self._feed_refresh_chip(client, params)[0] == 0

        undo_token = self._increment(client, page)
        assert self._feed_refresh_chip(client, params)[0] == 1

        undone = client.post(reverse("core:tally_undo"), {"token": undo_token}, headers={"HX-Request": "true"})
        assert undone.status_code == 200
        assert undone["HX-Trigger-After-Swap"] == "zeitstrom-changed"
        assert self._feed_refresh_chip(client, params)[0] == 0

    def test_tally_entry_of_a_disabled_type_moves_only_the_total_and_its_validator(
        self, client, staff_user, facility, tally_type, full_day_shift
    ):
        disabled = DocumentType.objects.create(facility=facility, name="Disabled", tally_enabled=False)
        page = _get(client, staff_user, {"time_filter": "all"})
        params = page.context["zeitstrom_filter_state"]
        before = _watch_kpi_validator(client, params)
        _event(facility, staff_user, disabled, anonymous=True, via=Event.CreatedVia.TALLY)

        assert _watch_kpi_validator(client, params) != before
        after = _get(client, staff_user, {"time_filter": "all"})
        assert _chip(after.content.decode(), "zeitstrom-kpi-tally")[0] == 1
        assert [row["count"] for row in after.context["tally_summary"]["rows"]] == [0]

    def test_watch_feed_refresh_does_not_repeat_the_row(self, client, staff_user, full_day_shift):
        from tests.test_zeitstrom_watch import _scope_for_client

        page = _get(client, staff_user, {"time_filter": "all"})
        params = page.context["zeitstrom_filter_state"]
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {**params, "feed_only": "1"},
            headers={"X-Zeitstrom-Watch": "1", "X-Zeitstrom-Page-Scope": _scope_for_client(client)},
        )

        assert 'id="zeitstrom-kpi-slot"' not in response.content.decode()

    def test_kpi_validator_moves_with_every_shown_number(
        self, client, staff_user, assistant_user, facility, doc_type_contact, client_identified, tally_type
    ):
        page = _get(client, staff_user, {"time_filter": "all"})
        params = page.context["zeitstrom_filter_state"]
        baseline = _watch_kpi_validator(client, params)
        assert baseline
        assert _watch_kpi_validator(client, params) == baseline  # stable without a change

        seen = {baseline}
        _event(facility, staff_user, doc_type_contact, client=client_identified)  # contact
        seen.add(_watch_kpi_validator(client, params))
        _event(facility, staff_user, tally_type, anonymous=True, via=Event.CreatedVia.TALLY)  # tally
        seen.add(_watch_kpi_validator(client, params))
        task = WorkItem.objects.create(
            facility=facility,
            title="new",
            item_type=WorkItem.ItemType.TASK,
            assigned_to=staff_user,
            created_by=staff_user,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        task_validator = _watch_kpi_validator(client, params)
        seen.add(task_validator)  # mine + overdue
        assert len(seen) == 4, "each change must produce a new validator"

        WorkItem.objects.filter(pk=task.pk).update(assigned_to=assistant_user)  # no longer mine
        assert _watch_kpi_validator(client, params) != task_validator

    def test_ban_change_moves_the_validator(self, client, staff_user, doc_type_hausverbot, client_identified, facility):
        from core.services.events import create_event

        page = _get(client, staff_user, {"time_filter": "all"})
        params = page.context["zeitstrom_filter_state"]
        before = _watch_kpi_validator(client, params)

        create_event(
            facility,
            staff_user,
            doc_type_hausverbot,
            timezone.now(),
            {"grund": "Bedrohung", "bis": (date.today() + timedelta(days=5)).isoformat(), "aktiv": True},
            client=client_identified,
        )

        assert _watch_kpi_validator(client, params) != before

    def test_other_users_task_does_not_move_the_validator(self, client, staff_user, assistant_user, facility):
        page = _get(client, staff_user, {"time_filter": "all"})
        params = page.context["zeitstrom_filter_state"]
        before = _watch_kpi_validator(client, params)

        WorkItem.objects.create(
            facility=facility,
            title="theirs",
            item_type=WorkItem.ItemType.TASK,
            assigned_to=assistant_user,
            created_by=staff_user,
        )

        assert _watch_kpi_validator(client, params) == before

    def test_partial_matches_the_advertised_snapshot(self, client, staff_user, full_day_shift):
        from tests.test_zeitstrom_watch import _scope_for_client

        page = _get(client, staff_user, {"time_filter": "all"})
        params = page.context["zeitstrom_filter_state"]
        validator = _watch_kpi_validator(client, params)
        headers = {"X-Zeitstrom-Watch": "1", "X-Zeitstrom-Page-Scope": _scope_for_client(client)}

        ok = client.get(reverse("core:zeitstrom_kpi_partial"), params, headers={**headers, "If-Match": validator})
        stale = client.get(reverse("core:zeitstrom_kpi_partial"), params, headers={**headers, "If-Match": '"x"'})

        assert ok.status_code == 200 and ok["ETag"] == validator
        assert 'id="zeitstrom-kpi-slot"' in ok.content.decode()
        assert stale.status_code == 412

    def test_partial_in_handover_mode_is_empty(self, client, staff_user, full_day_shift):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom_kpi_partial"), {"view": "uebergabe"})

        assert response.status_code == 204


@pytest.mark.django_db
class TestPageQueryBudget:
    """The new row costs less than the cockpit and the current-shift card it replaces."""

    #: Measured before the change with the same data (a running shift, three contacts):
    #: 41 queries for a specialist, 38 for a lead. The page must not exceed them.
    PREVIOUS_BUDGET = {"staff_user": 41, "lead_user": 38}

    @pytest.mark.parametrize("role", ["staff_user", "lead_user"])
    def test_full_page_does_not_issue_more_queries_than_before(
        self, client, request, facility, doc_type_contact, staff_user, client_identified, full_day_shift, role
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        user = request.getfixturevalue(role)
        for _ in range(3):
            _event(facility, staff_user, doc_type_contact, client=client_identified)
        client.force_login(user)
        client.get(reverse("core:zeitstrom"))  # warm caches that are not per-request

        with CaptureQueriesContext(connection) as queries:
            assert client.get(reverse("core:zeitstrom")).status_code == 200

        assert len(queries) <= self.PREVIOUS_BUDGET[role], len(queries)
