"""Fixed undo bar of the tally: out-of-band slot, latest tap wins, countdown hooks."""

import re

import pytest
from django.urls import reverse
from django.utils.html import escape

from core.models import Event
from tests._tally_helpers import post_tally
from tests.test_tally_tiles import _parse, _render_panel


def _increment(client, document_type, **extra):
    return post_tally(client, {"document_type": str(document_type.pk)}, HTTP_HX_REQUEST="true", **extra)


def _slot(html):
    return _parse(html).find_by_id("tally-undo-slot")


@pytest.mark.django_db
class TestUndoSlotInPanel:
    def test_panel_reserves_a_polite_live_slot_of_at_least_44px(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        slot = tree.find_testid("tally-undo-slot")
        assert slot is not None
        assert slot.attrs["id"] == "tally-undo-slot"
        assert "min-h-[44px]" in slot.classes or "md:min-h-[44px]" in slot.classes
        assert slot.attrs["aria-live"] == "polite"
        # Outside of the tile grid, so tiles never move when the bar appears.
        tile = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        assert not tile.parent.contains(slot)
        assert not [n for n in slot.descendants() if n.tag == "form"]


@pytest.mark.django_db
class TestUndoBarOutOfBand:
    def test_increment_response_carries_the_undo_form_in_an_oob_slot_not_in_the_row(
        self, client, staff_user, doc_type_contact
    ):
        client.force_login(staff_user)

        response = _increment(client, doc_type_contact)

        html = response.content.decode()
        tree = _parse(html)
        row = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        slot = tree.find_by_id("tally-undo-slot")
        assert response.status_code == 200
        assert slot.attrs["hx-swap-oob"] == "innerHTML"
        assert not [n for n in row.descendants() if n.tag == "form" and "tally-undo" in n.attrs.get("data-testid", "")]
        button = slot.find_testid("tally-undo")
        assert button is not None and button.tag == "button"
        form = button.parent
        while form.tag != "form":
            form = form.parent
        assert form.attrs["hx-target"] == f"#tally-row-{doc_type_contact.pk}"
        assert form.attrs["hx-swap"] == "outerHTML"
        assert "data-tally-undo-expires-at" in form.attrs
        assert escape(response.context["tally_undo_token"]) in html
        assert html.count('data-testid="tally-undo"') == 1

    def test_bar_names_the_counted_type_and_shows_the_remaining_seconds(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)

        slot = _slot(_increment(client, doc_type_contact).content.decode())

        text = " ".join(slot.all_text().split())
        assert f"+1 {doc_type_contact.name} gezählt" in text
        remaining = slot.find_by_attr("data-tally-undo-remaining")
        assert remaining.parent.attrs["aria-hidden"] == "true"
        assert 1 <= int(remaining.text.strip()) <= 60
        button = slot.find_testid("tally-undo")
        assert "aria-hidden" not in button.attrs

    def test_screen_reader_gets_one_announcement_with_the_undo_hint(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)

        slot = _slot(_increment(client, doc_type_contact).content.decode())

        announcement = slot.find_testid("tally-undo-announcement")
        assert "Rückgängig möglich" in " ".join(announcement.all_text().split())
        assert f"{doc_type_contact.name}" in announcement.all_text()
        # Focus is never moved by the bar.
        assert not [n for n in slot.descendants() if "autofocus" in n.attrs]

    def test_bar_is_fixed_above_the_bottom_nav_on_mobile_and_in_flow_on_desktop(
        self, client, staff_user, doc_type_contact
    ):
        client.force_login(staff_user)

        slot = _slot(_increment(client, doc_type_contact).content.decode())

        bar = slot.find_by_attr("data-tally-undo-bar")
        assert {"fixed", "bottom-[calc(4.5rem+env(safe-area-inset-bottom))]", "md:static"} <= bar.classes

    def test_replayed_increment_offers_no_undo_and_leaves_the_bar_alone(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        key = "11111111-1111-4111-8111-111111111111"
        post_tally(client, {"document_type": str(doc_type_contact.pk)}, idempotency_key=key, HTTP_HX_REQUEST="true")

        replay = post_tally(
            client, {"document_type": str(doc_type_contact.pk)}, idempotency_key=key, HTTP_HX_REQUEST="true"
        )

        assert "tally-undo-slot" not in replay.content.decode()

    def test_a_newer_tap_replaces_the_bar_while_the_earlier_token_stays_valid_on_the_server(
        self, client, staff_user, doc_type_contact
    ):
        client.force_login(staff_user)
        first = _increment(client, doc_type_contact)
        second = _increment(client, doc_type_contact)
        first_token = first.context["tally_undo_token"]
        second_token = second.context["tally_undo_token"]

        assert first_token != second_token
        assert escape(second_token) in second.content.decode()
        assert escape(first_token) not in second.content.decode()
        undone = client.post(reverse("core:tally_undo"), {"token": first_token}, HTTP_HX_REQUEST="true")
        assert undone.status_code == 200
        assert Event.objects.filter(document_type=doc_type_contact, is_deleted=True).count() == 1

    def test_undo_response_refreshes_the_tile_and_empties_the_slot(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        token = _increment(client, doc_type_contact).context["tally_undo_token"]

        undone = client.post(reverse("core:tally_undo"), {"token": token}, HTTP_HX_REQUEST="true")

        html = undone.content.decode()
        tree = _parse(html)
        slot = tree.find_by_id("tally-undo-slot")
        assert undone["HX-Retarget"] == f"#tally-row-{doc_type_contact.pk}"
        assert slot.attrs["hx-swap-oob"] == "innerHTML"
        assert slot.children == [] and slot.text.strip() == ""
        assert tree.find_testid(f"tally-row-{doc_type_contact.pk}") is not None
        count = tree.find_testid(f"tally-count-{doc_type_contact.pk}")
        assert count.text.strip() == "0"
        assert not re.search(r"data-tally-pulse", html)
