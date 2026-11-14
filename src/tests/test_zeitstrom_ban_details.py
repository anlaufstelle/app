"""Hiding the ban details in the Zeitstrom and showing them again through the figure.

The choice is stored per user and facility, as a keyed fingerprint of the list the
role sees. Any change of that list shows the details again.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event, TimeFilter, User
from core.services.system import get_active_bans
from core.services.system.ban_details import ban_list_fingerprint
from tests import test_bans as _bans

doc_type_hausverbot = _bans.doc_type_hausverbot
active_ban_event = _bans.active_ban_event

DETAILS_URL_NAME = "core:zeitstrom_ban_details"


@pytest.fixture(autouse=True)
def _full_day_shift(facility):
    return TimeFilter.objects.create(
        facility=facility, label="Day shift", start_time="00:00", end_time="23:59", sort_order=0
    )


def _page(client, user, params=None):
    client.force_login(user)
    response = client.get(reverse("core:zeitstrom"), params or {})
    assert response.status_code == 200
    return response.content.decode()


def _tag(html, needle):
    """The opening tag that carries *needle*."""
    match = re.search(rf"<[^<>]*{re.escape(needle)}[^<>]*>", html)
    assert match, f"{needle} not found"
    return match.group(0)


def _fingerprint(html):
    return re.search(r'id="zeitstrom-bans-slot"[^>]*data-fingerprint="([0-9a-f]+)"', html).group(1)


def _details_hidden(html):
    tag = _tag(html, 'id="zeitstrom-ban-details"')
    return re.search(r"(?<![\w:-])hidden(?![\w=-])", tag) is not None


def _stored(user):
    """The choice as stored in the database (not the possibly cached instance)."""
    return User.objects.values_list("ban_details_fingerprint", flat=True).get(pk=user.pk)


def _post(client, value, **extra):
    return client.post(reverse(DETAILS_URL_NAME), {"ban_details": value, **extra})


def _hide(client, html):
    return _post(client, f"hide:{_fingerprint(html)}", next=reverse("core:zeitstrom"))


def _second_ban(facility, user, doc_type, *, name="Zweite Person"):
    from core.models import Client

    person = Client.objects.create(
        facility=facility, pseudonym=name, contact_stage=Client.ContactStage.IDENTIFIED, created_by=user
    )
    return Event.objects.create(
        facility=facility,
        client=person,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={"grund": "Streit", "bis": (date.today() + timedelta(days=5)).isoformat(), "aktiv": True},
        created_by=user,
    )


@pytest.mark.django_db
class TestMarkup:
    def test_figure_is_a_switch_for_the_detail_block(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        tag = _tag(html, 'data-testid="zeitstrom-kpi-bans"')
        assert tag.startswith("<button")
        assert 'aria-expanded="true"' in tag
        assert 'aria-controls="zeitstrom-ban-details"' in tag
        assert 'id="zeitstrom-ban-details"' in html
        assert "min-h-[44px]" in tag

    def test_close_button_has_a_name_and_a_44px_target(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        tag = _tag(html, 'aria-label="Hausverbot-Details ausblenden"')
        assert tag.startswith("<button")
        assert "min-h-[44px]" in tag and "min-w-[44px]" in tag
        assert html.count('aria-label="Hausverbot-Details ausblenden"') == 1

    def test_figure_leads_the_row(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        assert html.index("zeitstrom-kpi-bans") < html.index("zeitstrom-contacts-list")

    def test_details_are_visible_by_default(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        assert not _details_hidden(html)
        assert "Bedrohung" in html

    def test_without_active_ban_there_is_neither_figure_nor_block(self, client, staff_user):
        html = _page(client, staff_user)

        assert "zeitstrom-kpi-bans" not in html
        assert 'id="zeitstrom-ban-details"' not in html

    def test_without_script_the_switch_is_a_form_post(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        form = _tag(html, 'id="zeitstrom-ban-details-form"')
        assert 'method="post"' in form
        assert reverse(DETAILS_URL_NAME) in form
        assert "csrfmiddlewaretoken" in html
        toggle = _tag(html, 'data-testid="zeitstrom-kpi-bans"')
        assert 'form="zeitstrom-ban-details-form"' in toggle
        assert 'type="submit"' in toggle
        assert re.search(r'name="ban_details" value="hide:[0-9a-f]{64}"', toggle)


@pytest.mark.django_db
class TestPersistence:
    def test_hiding_is_stored_and_survives_a_new_session(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)
        assert _hide(client, html).status_code == 302

        client.logout()
        html = _page(client, staff_user)

        assert _details_hidden(html)
        tag = _tag(html, 'data-testid="zeitstrom-kpi-bans"')
        assert 'aria-expanded="false"' in tag
        assert re.search(r'name="ban_details" value="show"', tag)

    def test_showing_again_clears_the_stored_choice(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))
        assert _stored(staff_user) != ""

        _post(client, "show", next=reverse("core:zeitstrom"))

        assert _stored(staff_user) == ""
        assert not _details_hidden(_page(client, staff_user))

    def test_other_users_are_not_affected(self, client, staff_user, lead_user, active_ban_event):
        _hide(client, _page(client, staff_user))

        assert not _details_hidden(_page(client, lead_user))

    def test_a_choice_made_for_another_facility_does_not_apply(
        self, client, staff_user, facility, second_facility, active_ban_event
    ):
        bans = get_active_bans(facility, user=staff_user)
        User.objects.filter(pk=staff_user.pk).update(
            ban_details_fingerprint=ban_list_fingerprint(staff_user, second_facility, bans)
        )

        assert not _details_hidden(_page(client, staff_user))

    def test_handover_mode_never_hides_and_has_no_close_button(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))

        html = _page(client, staff_user, {"view": "uebergabe", "date": (date.today() - timedelta(days=1)).isoformat()})

        assert 'id="zeitstrom-ban-details"' in html
        assert not _details_hidden(html)
        assert "Hausverbot-Details ausblenden" not in html
        assert "zeitstrom-kpi-bans" not in html
        assert 'id="zeitstrom-ban-details-form"' not in html

    def test_kpi_partial_follows_the_stored_choice(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))

        response = client.get(reverse("core:zeitstrom_kpi_partial"))

        tag = _tag(response.content.decode(), 'data-testid="zeitstrom-kpi-bans"')
        assert 'aria-expanded="false"' in tag

    def test_bans_partial_follows_the_stored_choice(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))

        response = client.get(reverse("core:zeitstrom_bans_partial"))

        assert _details_hidden(response.content.decode())

    def test_feed_refresh_carries_the_stored_choice(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))

        response = client.get(
            reverse("core:zeitstrom_feed_partial"), {"feed_only": "1"}, headers={"HX-Request": "true"}
        )

        html = response.content.decode()
        assert 'hx-swap-oob="outerHTML"' in html
        assert 'aria-expanded="false"' in _tag(html, 'data-testid="zeitstrom-kpi-bans"')


@pytest.mark.django_db
class TestChangeShowsTheDetailsAgain:
    def test_new_ban_shows_the_details(self, client, staff_user, facility, active_ban_event, doc_type_hausverbot):
        _hide(client, _page(client, staff_user))
        _second_ban(facility, staff_user, doc_type_hausverbot)

        html = _page(client, staff_user)

        assert not _details_hidden(html)
        assert 'aria-expanded="true"' in _tag(html, 'data-testid="zeitstrom-kpi-bans"')

    def test_expired_ban_shows_the_details(self, client, staff_user, facility, active_ban_event, doc_type_hausverbot):
        _second_ban(facility, staff_user, doc_type_hausverbot)
        _hide(client, _page(client, staff_user))
        active_ban_event.data_json = {**active_ban_event.data_json, "aktiv": False}
        active_ban_event.save()

        assert not _details_hidden(_page(client, staff_user))

    def test_deleted_ban_shows_the_details(self, client, staff_user, facility, active_ban_event, doc_type_hausverbot):
        second = _second_ban(facility, staff_user, doc_type_hausverbot)
        _hide(client, _page(client, staff_user))
        Event.objects.filter(pk=second.pk).update(is_deleted=True)

        assert not _details_hidden(_page(client, staff_user))

    def test_changed_expiry_shows_the_details(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))
        active_ban_event.data_json = {
            **active_ban_event.data_json,
            "bis": (date.today() + timedelta(days=3)).isoformat(),
        }
        active_ban_event.save()

        assert not _details_hidden(_page(client, staff_user))

    def test_live_swap_of_both_slots_shows_the_details(
        self, client, staff_user, facility, active_ban_event, doc_type_hausverbot
    ):
        _hide(client, _page(client, staff_user))
        _second_ban(facility, staff_user, doc_type_hausverbot)

        bans = client.get(reverse("core:zeitstrom_bans_partial")).content.decode()
        kpi = client.get(reverse("core:zeitstrom_kpi_partial")).content.decode()

        assert not _details_hidden(bans)
        assert 'aria-expanded="true"' in _tag(kpi, 'data-testid="zeitstrom-kpi-bans"')

    def test_returning_to_the_old_list_does_not_hide_again(
        self, client, staff_user, facility, active_ban_event, doc_type_hausverbot
    ):
        _hide(client, _page(client, staff_user))
        second = _second_ban(facility, staff_user, doc_type_hausverbot)
        assert not _details_hidden(_page(client, staff_user))

        Event.objects.filter(pk=second.pk).update(is_deleted=True)

        assert not _details_hidden(_page(client, staff_user))
        assert _stored(staff_user) == ""

    def test_returning_after_all_bans_were_lifted_does_not_hide_again(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))
        Event.objects.filter(pk=active_ban_event.pk).update(is_deleted=True)
        assert 'id="zeitstrom-ban-details"' not in _page(client, staff_user)
        assert _stored(staff_user) == ""

        Event.objects.filter(pk=active_ban_event.pk).update(is_deleted=False)

        assert not _details_hidden(_page(client, staff_user))

    def test_live_partial_without_bans_drops_the_choice(self, client, staff_user, active_ban_event):
        _hide(client, _page(client, staff_user))
        Event.objects.filter(pk=active_ban_event.pk).update(is_deleted=True)

        client.get(reverse("core:zeitstrom_bans_partial"))
        Event.objects.filter(pk=active_ban_event.pk).update(is_deleted=False)

        assert not _details_hidden(client.get(reverse("core:zeitstrom_bans_partial")).content.decode())

    def test_stale_fingerprint_does_not_hide(self, client, staff_user, facility, active_ban_event, doc_type_hausverbot):
        html = _page(client, staff_user)
        stale = _fingerprint(html)
        _second_ban(facility, staff_user, doc_type_hausverbot)

        _post(client, f"hide:{stale}", next=reverse("core:zeitstrom"))

        assert _stored(staff_user) == ""
        assert not _details_hidden(_page(client, staff_user))


@pytest.mark.django_db
class TestNoSideChannel:
    def test_reason_change_is_invisible_to_the_assistant_and_shows_again_for_staff(
        self, client, staff_user, assistant_user, active_ban_event
    ):
        assistant_html = _page(client, assistant_user)
        _hide(client, assistant_html)
        staff_html = _page(client, staff_user)
        _hide(client, staff_html)

        active_ban_event.data_json = {**active_ban_event.data_json, "grund": "Etwas ganz anderes"}
        active_ban_event.save()

        assert _details_hidden(_page(client, assistant_user))
        assert not _details_hidden(_page(client, staff_user))

    def test_fingerprint_is_keyed_and_holds_no_clear_text(
        self, client, staff_user, client_identified, active_ban_event
    ):
        html = _page(client, staff_user)
        _hide(client, html)

        stored = _stored(staff_user)
        assert re.fullmatch(r"[0-9a-f]{64}", stored)
        for clear in (client_identified.pseudonym, "Bedrohung", str(active_ban_event.pk), str(client_identified.pk)):
            assert clear not in stored

    def test_fingerprint_differs_between_users_for_the_same_list(self, client, staff_user, lead_user, active_ban_event):
        assert _fingerprint(_page(client, staff_user)) != _fingerprint(_page(client, lead_user))


@pytest.mark.django_db
class TestPostEndpoint:
    def test_json_answer_for_scripted_calls(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        response = client.post(
            reverse(DETAILS_URL_NAME),
            {"ban_details": f"hide:{_fingerprint(html)}"},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        assert response.json() == {"hidden": True, "fingerprint": _fingerprint(html)}

    def test_stale_answer_reports_the_current_state(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        response = client.post(
            reverse(DETAILS_URL_NAME), {"ban_details": "hide:" + "0" * 64}, headers={"Accept": "application/json"}
        )

        assert response.json() == {"hidden": False, "fingerprint": _fingerprint(html)}

    def test_garbage_value_changes_nothing(self, client, staff_user, active_ban_event):
        client.force_login(staff_user)

        response = _post(client, "hide:not-a-fingerprint", next=reverse("core:zeitstrom"))

        assert response.status_code == 302
        assert _stored(staff_user) == ""

    def test_without_active_ban_nothing_is_stored(self, client, staff_user):
        client.force_login(staff_user)

        _post(client, "hide:" + "a" * 64)

        assert _stored(staff_user) == ""

    def test_redirect_ignores_foreign_targets(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)

        response = _post(client, f"hide:{_fingerprint(html)}", next="https://evil.example/x")

        assert response.status_code == 302
        assert response["Location"] == reverse("core:zeitstrom")

    def test_redirect_keeps_the_filter_state(self, client, staff_user, active_ban_event):
        html = _page(client, staff_user)
        target = reverse("core:zeitstrom") + "?type=events"

        response = _post(client, f"hide:{_fingerprint(html)}", next=target)

        assert response["Location"] == target

    def test_post_needs_the_csrf_token(self, staff_user, active_ban_event):
        from django.test import Client as TestClient

        strict = TestClient(enforce_csrf_checks=True)
        strict.force_login(staff_user)

        response = strict.post(reverse(DETAILS_URL_NAME), {"ban_details": "show"})

        assert response.status_code == 403

    def test_get_is_not_allowed(self, client, staff_user):
        client.force_login(staff_user)

        assert client.get(reverse(DETAILS_URL_NAME)).status_code == 405

    def test_anonymous_is_sent_to_the_login(self, client):
        response = _post(client, "show")

        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_facility_less_account_is_forbidden(self, client, facility_less_admin):
        client.force_login(facility_less_admin)

        assert _post(client, "show").status_code == 403


@pytest.mark.django_db
class TestQueryBudget:
    def _count(self, client, url_name="core:zeitstrom"):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client.get(reverse(url_name))
        with CaptureQueriesContext(connection) as queries:
            assert client.get(reverse(url_name)).status_code == 200
        return len(queries)

    @pytest.mark.parametrize(
        "url_name", ["core:zeitstrom", "core:zeitstrom_kpi_partial", "core:zeitstrom_bans_partial"]
    )
    def test_reading_the_stored_choice_costs_no_query(self, client, staff_user, active_ban_event, url_name):
        client.force_login(staff_user)
        shown = self._count(client, url_name)

        _hide(client, _page(client, staff_user))

        assert self._count(client, url_name) == shown

    def test_page_without_active_ban_costs_no_extra_query(self, client, staff_user, lead_user):
        client.force_login(staff_user)
        with_user_field = self._count(client)

        assert with_user_field == self._count(client)
        assert not User.objects.filter(ban_details_fingerprint__gt="").exists()
