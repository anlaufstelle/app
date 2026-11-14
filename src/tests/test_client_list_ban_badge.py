"""Ban badge in the person list: same visibility as the Zeitstrom banner, one query per page."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.services.system import get_active_bans
from tests import test_bans as _bans

doc_type_hausverbot = _bans.doc_type_hausverbot
active_ban_event = _bans.active_ban_event
expired_ban_event = _bans.expired_ban_event

BADGE = 'data-testid="client-row-ban-badge"'


def _list(client, user, params=None, **extra):
    client.force_login(user)
    response = client.get(reverse("core:client_list"), params or {}, **extra)
    assert response.status_code == 200
    return response.content.decode()


def _row(html, pseudonym):
    """The markup of the list row of *pseudonym*."""
    marker = f'data-pseudonym="{pseudonym}"'
    start = html.index(marker)
    end = html.find('data-testid="client-row"', start + 1)
    return html[start : end if end != -1 else len(html)]


@pytest.mark.django_db
class TestBadgeVisibility:
    @pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user"])
    def test_active_ban_gets_a_badge_for_every_role(self, client, request, role, client_identified, active_ban_event):
        html = _list(client, request.getfixturevalue(role))

        row = _row(html, client_identified.pseudonym)
        assert BADGE in row
        assert "bg-ban text-white" in row
        assert ">Hausverbot<" in row.replace("\n", "").replace("  ", "")

    def test_badge_names_the_ban_in_text_not_only_in_colour(
        self, client, staff_user, client_identified, active_ban_event
    ):
        row = _row(_list(client, staff_user), client_identified.pseudonym)

        assert "Hausverbot" in row

    def test_person_without_ban_has_no_badge(self, client, staff_user, client_identified):
        assert BADGE not in _list(client, staff_user)

    def test_expired_ban_has_no_badge(self, client, staff_user, client_identified, expired_ban_event):
        assert BADGE not in _list(client, staff_user)

    def test_inactive_ban_has_no_badge(self, client, staff_user, client_identified, active_ban_event):
        active_ban_event.data_json = {**active_ban_event.data_json, "aktiv": False}
        active_ban_event.save()

        assert BADGE not in _list(client, staff_user)

    def test_deleted_ban_has_no_badge(self, client, staff_user, client_identified, active_ban_event):
        Event.objects.filter(pk=active_ban_event.pk).update(is_deleted=True)

        assert BADGE not in _list(client, staff_user)

    def test_ban_without_expiry_date_still_counts(self, client, staff_user, client_identified, active_ban_event):
        active_ban_event.data_json = {"grund": "x", "aktiv": True}
        active_ban_event.save()

        assert BADGE in _list(client, staff_user)

    def test_only_the_banned_person_carries_the_badge(
        self, client, staff_user, facility, client_identified, active_ban_event
    ):
        other = Client.objects.create(
            facility=facility,
            pseudonym="Ohne Verbot",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )

        html = _list(client, staff_user)

        assert BADGE in _row(html, client_identified.pseudonym)
        assert BADGE not in _row(html, other.pseudonym)

    def test_reason_never_appears_in_the_list(self, client, staff_user, client_identified, active_ban_event):
        assert "Bedrohung" not in _list(client, staff_user)

    def test_ban_of_another_facility_gives_no_badge(
        self, client, staff_user, second_facility_user, second_facility, client_identified
    ):
        foreign_type = DocumentType.objects.create(
            facility=second_facility, name="Hausverbot", system_type=DocumentType.SystemType.BAN
        )
        foreign = Client.objects.create(
            facility=second_facility, pseudonym=client_identified.pseudonym, created_by=second_facility_user
        )
        Event.objects.create(
            facility=second_facility,
            client=foreign,
            document_type=foreign_type,
            occurred_at=timezone.now(),
            data_json={"aktiv": True},
            created_by=second_facility_user,
        )

        assert BADGE not in _list(client, staff_user)

    def test_badge_matches_the_zeitstrom_banner(
        self, client, staff_user, facility, client_identified, active_ban_event
    ):
        banned = {ban["client"].pk for ban in get_active_bans(facility, user=staff_user)}

        html = _list(client, staff_user)

        assert (BADGE in _row(html, client_identified.pseudonym)) == (client_identified.pk in banned)

    def test_htmx_partial_carries_the_badge(self, client, staff_user, client_identified, active_ban_event):
        html = _list(client, staff_user, headers={"HX-Request": "true"})

        assert BADGE in html

    def test_ban_of_a_person_outside_the_page_is_not_queried_for(self, client, staff_user, facility, active_ban_event):
        for number in range(30):
            Client.objects.create(
                facility=facility,
                pseudonym=f"Zz Person {number:02d}",
                contact_stage=Client.ContactStage.IDENTIFIED,
                created_by=staff_user,
            )
        html = _list(client, staff_user, {"q": "Zz Person"})

        assert BADGE not in html


@pytest.mark.django_db
class TestQueryCount:
    def _count(self, client, user, params=None):
        client.force_login(user)
        client.get(reverse("core:client_list"), params or {})
        with CaptureQueriesContext(connection) as queries:
            assert client.get(reverse("core:client_list"), params or {}).status_code == 200
        return len(queries)

    def test_bans_cost_at_most_one_query_and_do_not_grow_with_the_page(
        self, client, staff_user, facility, doc_type_hausverbot
    ):
        for number in range(3):
            Client.objects.create(
                facility=facility,
                pseudonym=f"Basis {number}",
                contact_stage=Client.ContactStage.IDENTIFIED,
                created_by=staff_user,
            )
        before = self._count(client, staff_user)

        for person in Client.objects.filter(facility=facility):
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc_type_hausverbot,
                occurred_at=timezone.now(),
                data_json={"aktiv": True, "bis": (date.today() + timedelta(days=9)).isoformat()},
                created_by=staff_user,
            )
        with_bans = self._count(client, staff_user)

        assert with_bans - before <= 1, (before, with_bans)

    def test_query_count_is_independent_of_the_number_of_banned_rows(
        self, client, staff_user, facility, doc_type_hausverbot
    ):
        def make(number):
            person = Client.objects.create(
                facility=facility,
                pseudonym=f"P{number:02d}",
                contact_stage=Client.ContactStage.IDENTIFIED,
                created_by=staff_user,
            )
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc_type_hausverbot,
                occurred_at=timezone.now(),
                data_json={"aktiv": True},
                created_by=staff_user,
            )

        make(0)
        one = self._count(client, staff_user)
        for number in range(1, 6):
            make(number)

        assert self._count(client, staff_user) == one
