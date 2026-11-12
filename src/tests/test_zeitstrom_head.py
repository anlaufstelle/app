"""Zeitstrom header: one date navigation, one shift selection, shift named once."""

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from core.models import TimeFilter


@pytest.fixture
def shifts(facility):
    early = TimeFilter.objects.create(
        facility=facility, label="Frühdienst", start_time="08:00", end_time="16:00", sort_order=0
    )
    night = TimeFilter.objects.create(
        facility=facility, label="Nachtdienst", start_time="22:00", end_time="06:00", sort_order=1
    )
    return early, night


def _get(client, user, params=None, **extra):
    client.force_login(user)
    response = client.get(reverse("core:zeitstrom"), params or {}, **extra)
    assert response.status_code == 200
    return response.content.decode()


def _count_testid(html, testid):
    return len(re.findall(rf'data-testid="{re.escape(testid)}"', html))


@pytest.mark.django_db
class TestSingleHeader:
    @pytest.mark.parametrize("params", [{}, {"view": "uebergabe"}])
    def test_exactly_one_date_navigation_and_one_shift_select(self, client, staff_user, shifts, params):
        html = _get(client, staff_user, params)

        assert _count_testid(html, "zeitstrom-date-nav") == 1
        assert len(re.findall(r"<select[^>]*\bid=\"zeitstrom-shift-select\"", html)) == 1
        assert html.count("Vorheriger Tag") == 1
        assert html.count("Nächster Tag") == 1

    @pytest.mark.parametrize("params", [{}, {"view": "uebergabe"}])
    def test_segment_switches_between_feed_and_handover(self, client, staff_user, shifts, params):
        html = _get(client, staff_user, params)

        assert _count_testid(html, "zeitstrom-tab-feed") == 1
        assert _count_testid(html, "zeitstrom-tab-uebergabe") == 1
        pressed = "uebergabe" if params else "feed"
        match = re.search(rf'<a[^>]*data-testid="zeitstrom-tab-{pressed}"[^>]*>', html)
        assert match and 'aria-current="page"' in match.group(0)

    @pytest.mark.parametrize("role", ["staff_user", "assistant_user"])
    def test_secondary_actions_are_grouped_under_new(self, client, request, role, shifts):
        html = _get(client, request.getfixturevalue(role))

        assert _count_testid(html, "zeitstrom-create-event") == 1
        if role == "staff_user":
            menu = re.search(r'<details[^>]*data-testid="zeitstrom-create-menu".*?</details>', html, re.DOTALL)
            assert menu is not None
            assert re.search(r"<summary[^>]*>.*?Neu.*?</summary>", menu.group(0), re.DOTALL)
            assert _count_testid(menu.group(0), "zeitstrom-create-workitem") == 1
            assert _count_testid(menu.group(0), "zeitstrom-create-client") == 1
        else:
            assert _count_testid(html, "zeitstrom-create-menu") == 0
            assert _count_testid(html, "zeitstrom-create-workitem") == 0
            assert _count_testid(html, "zeitstrom-create-client") == 0

    def test_shift_select_lists_all_shifts_with_window_and_marks_the_selected_one(self, client, staff_user, shifts):
        early, night = shifts
        html = _get(client, staff_user, {"time_filter": str(early.pk)})

        select = re.search(r'<select[^>]*id="zeitstrom-shift-select".*?</select>', html, re.DOTALL).group(0)
        assert len(re.findall(r"<option", select)) == 3  # all + two shifts
        assert re.search(rf'<option value="{early.pk}"[^>]*selected', select)
        assert "08:00–16:00" in select
        # The night shift crosses midnight: the end carries the day marker.
        assert "22:00–06:00 (+1)" in select
        assert re.search(r'<label[^>]*for="zeitstrom-shift-select"', html)

    def test_next_day_is_disabled_today_and_not_a_link_target(self, client, staff_user, shifts):
        html = _get(client, staff_user)

        nav = html[html.index('data-testid="zeitstrom-date-nav"') :]
        nxt = re.search(r"<(?:a|span|button)[^>]*Nächster Tag[^>]*>", nav).group(0)
        assert 'aria-disabled="true"' in nxt
        assert "href=" not in nxt

    def test_today_link_only_when_not_today(self, client, staff_user, shifts):
        assert _count_testid(_get(client, staff_user), "zeitstrom-date-today") == 0
        past = (timezone.localdate() - timedelta(days=3)).isoformat()
        assert _count_testid(_get(client, staff_user, {"date": past}), "zeitstrom-date-today") == 1

    def test_mobile_selection_target_is_at_least_44px(self, client, staff_user, shifts):
        html = _get(client, staff_user)

        trigger = re.search(r'<button[^>]*data-testid="zeitstrom-selection-trigger"[^>]*>', html).group(0)
        assert "min-h-[44px]" in trigger
        assert 'aria-controls="zeitstrom-selection"' in trigger
        assert 'aria-expanded="false"' in trigger
        assert 'id="zeitstrom-selection"' in html

    def test_mobile_view_segment_follows_the_date_and_shift_target(self, client, staff_user, shifts):
        html = _get(client, staff_user)
        segment = re.search(r'<div[^>]*aria-label="Ansicht"[^>]*>', html).group(0)

        assert "order-last" in segment
        assert "w-full" in segment
        assert "md:order-none" in segment

    def test_old_tab_strip_and_scroll_hint_are_gone(self, client, staff_user, shifts):
        html = _get(client, staff_user)

        assert 'data-testid="time-filter-tabs"' not in html
        assert "time-filter-scroll-hint" not in html


@pytest.mark.django_db
class TestDeepLinks:
    def test_all_five_parameters_survive_in_state_and_navigation(self, client, staff_user, shifts, doc_type_contact):
        early, _ = shifts
        day = (timezone.localdate() - timedelta(days=2)).isoformat()
        html = _get(
            client,
            staff_user,
            {"date": day, "time_filter": str(early.pk), "type": "events", "doc_type": str(doc_type_contact.pk)},
        )

        for name, value in (
            ("date", day),
            ("time_filter", str(early.pk)),
            ("type", "events"),
            ("doc_type", str(doc_type_contact.pk)),
            ("view", ""),
        ):
            assert re.search(rf'<input type="hidden" name="{name}" value="{re.escape(value)}">', html)
        nav = html[html.index('data-testid="zeitstrom-date-nav"') :]
        prev = re.search(r'<a[^>]*href="([^"]*)"[^>]*Vorheriger Tag|<a[^>]*Vorheriger Tag[^>]*href="([^"]*)"', nav)
        href = (prev.group(1) or prev.group(2)).replace("&amp;", "&")
        assert f"date={(timezone.localdate() - timedelta(days=3)).isoformat()}" in href
        assert f"time_filter={early.pk}" in href
        assert "type=events" in href
        assert f"doc_type={doc_type_contact.pk}" in href

    def test_whole_day_selection_survives_date_navigation(self, client, staff_user, shifts):
        from html import unescape
        from urllib.parse import parse_qs, urlparse

        day = (timezone.localdate() - timedelta(days=1)).isoformat()
        html = _get(client, staff_user, {"date": day, "time_filter": "all"})
        links = re.findall(r'<a[^>]*href="([^"]+)"[^>]*data-date-nav-link', html)
        assert links
        for link in links:
            assert parse_qs(urlparse(unescape(link)).query)["time_filter"] == ["all"]

    def test_handover_navigation_keeps_the_view(self, client, staff_user, shifts):
        early, _ = shifts
        html = _get(client, staff_user, {"view": "uebergabe", "time_filter": str(early.pk)})

        nav = html[html.index('data-testid="zeitstrom-date-nav"') :]
        assert "view=uebergabe" in nav.replace("&amp;", "&")


@pytest.mark.django_db
class TestDateLocale:
    def test_german_date_uses_german_format(self, client, staff_user, shifts):
        day = timezone.localdate() - timedelta(days=1)
        html = _get(client, staff_user, {"date": day.isoformat(), "lang": "de"})

        text = re.search(r'data-testid="zeitstrom-date-text"[^>]*>([^<]*)<', html).group(1).strip()
        assert re.search(r"\d{1,2}\. ", text), text

    def test_english_date_has_no_german_day_dot(self, client, staff_user, shifts):
        day = timezone.localdate() - timedelta(days=1)
        html = _get(client, staff_user, {"date": day.isoformat(), "lang": "en"})

        text = re.search(r'data-testid="zeitstrom-date-text"[^>]*>([^<]*)<', html).group(1).strip()
        assert not re.search(r"\d{1,2}\. ", text), text


@pytest.mark.django_db
class TestShiftNamedOnce:
    def test_current_shift_card_does_not_repeat_the_shift_while_the_selection_is_live(self, client, staff_user, shifts):
        html = _get(client, staff_user, {"time_filter": "all"})

        assert 'data-testid="current-shift-label"' not in html

    def test_hint_shown_when_selection_differs_from_the_running_shift(self, client, staff_user, shifts):
        early, _ = shifts
        day = timezone.localdate() - timedelta(days=1)
        html = _get(client, staff_user, {"date": day.isoformat(), "time_filter": str(early.pk)})

        hint = re.search(r'<[^>]*data-testid="zeitstrom-selection-hint".*?</a>', html, re.DOTALL).group(0)
        assert "Frühdienst" in hint
        assert reverse("core:zeitstrom") in hint
        assert _count_testid(html, "zeitstrom-selection-hint") == 1

    def test_no_hint_for_the_running_selection(self, client, staff_user, shifts):
        html = _get(client, staff_user, {"time_filter": "all"})

        assert _count_testid(html, "zeitstrom-selection-hint") == 0
        assert 'id="zeitstrom-selection-hint-slot"' in html  # empty slot stays for the refresh

    def test_handover_mode_has_no_hint(self, client, staff_user, shifts):
        day = (timezone.localdate() - timedelta(days=1)).isoformat()
        html = _get(client, staff_user, {"date": day, "view": "uebergabe"})

        assert _count_testid(html, "zeitstrom-selection-hint") == 0


class TestTallyWindowText:
    def _render(self, start, end, label=None):
        summary = {
            "time_filter": SimpleNamespace(label=label) if label else None,
            "window_start": start,
            "window_end": end,
            "window_text": None,
            "rows": [],
        }
        return summary

    def test_window_text_without_date_for_day_shift(self):
        from core.services.zeitstrom_display import format_shift_window

        tz = timezone.get_current_timezone()
        start = timezone.make_aware(timezone.datetime(2026, 10, 23, 14, 0), tz)
        end = timezone.make_aware(timezone.datetime(2026, 10, 23, 22, 0), tz)

        assert format_shift_window(start, end) == "14:00–22:00"

    def test_window_text_marks_the_next_day_for_night_shift(self):
        from core.services.zeitstrom_display import format_shift_window

        tz = timezone.get_current_timezone()
        start = timezone.make_aware(timezone.datetime(2026, 10, 23, 22, 0), tz)
        end = timezone.make_aware(timezone.datetime(2026, 10, 24, 6, 0), tz)

        assert format_shift_window(start, end) == "22:00–06:00 (+1)"

    def test_window_text_is_exact_across_the_autumn_dst_change(self):
        from core.services.zeitstrom_display import format_shift_window

        tz = timezone.get_current_timezone()
        start = timezone.make_aware(timezone.datetime(2026, 10, 24, 22, 0), tz)
        end = timezone.make_aware(timezone.datetime(2026, 10, 25, 6, 0), tz)

        assert format_shift_window(start, end) == "22:00–06:00 (+1)"

    def test_tally_head_shows_only_the_window_text(self):
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(timezone.datetime(2026, 10, 23, 14, 0), tz)
        end = timezone.make_aware(timezone.datetime(2026, 10, 23, 22, 0), tz)
        row = {
            "document_type": SimpleNamespace(pk="x", name="Kontakt"),
            "count": 2,
            "requires_number_input": False,
            "idempotency_key": "k",
            "tally_increment_url": "/t/",
        }
        summary = self._render(start, end, label="Spätdienst")
        summary["window_text"] = "14:00–22:00"
        summary["rows"] = [row]

        html = render_to_string("core/zeitstrom/partials/tally_panel.html", {"tally_summary": summary})

        context = re.search(r'data-testid="tally-live-context"[^>]*>(.*?)</span>', html, re.DOTALL).group(1).strip()
        assert context == "14:00–22:00"
