"""Number entry of the tally as popover/bottom sheet in a panel-level slot."""

import pytest
from django.urls import reverse

from core.forms.tally import TallyNumberDataForm
from core.models import Event
from core.services.events.tally import evaluate_tally_eligibility
from core.views.events import TallyIncrementView
from tests._tally_helpers import post_tally
from tests.test_tally_number_inline import _number_tally_type
from tests.test_tally_tiles import _parse, _render_panel


def _open(client, document_type, **extra):
    return client.get(
        reverse("core:tally_increment"),
        {"document_type": str(document_type.pk), **extra},
        HTTP_HX_REQUEST="true",
    )


@pytest.mark.django_db
class TestNumberFormSlotInPanel:
    def test_panel_has_an_empty_slot_outside_the_tile_grid(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        slot = tree.find_testid("tally-number-form-slot")
        tile = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        assert slot.attrs["id"] == "tally-number-form-slot"
        assert "data-hx-no-focus" in slot.attrs
        assert slot.children == []
        assert not tile.parent.contains(slot)
        assert tree.find_testid("tally-panel").contains(slot)

    def test_number_tile_loads_the_form_into_the_slot_and_keeps_the_full_page_fallback(self, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)

        tree = _parse(_render_panel(facility, staff_user))

        link = tree.find_testid(f"tally-increment-{document_type.pk}")
        assert link.attrs["hx-target"] == "#tally-number-form-slot"
        assert link.attrs["hx-swap"] == "innerHTML"
        assert link.attrs["aria-haspopup"] == "dialog"
        assert link.attrs["href"] == link.attrs["hx-get"]
        assert link.attrs["href"].startswith(reverse("core:tally_increment"))


@pytest.mark.django_db
class TestNumberFormFragment:
    def test_fragment_is_a_labelled_dialog_with_close_controls_and_no_inline_row(self, client, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility, name="Spritzentausch")
        client.force_login(staff_user)

        response = _open(client, document_type)

        html = response.content.decode()
        tree = _parse(html)
        root = tree.find_testid(f"tally-inline-row-{document_type.pk}")
        assert response.status_code == 200
        assert root.tag == "dialog"
        assert "data-tally-number-sheet" in root.attrs
        assert root.attrs["data-document-type"] == str(document_type.pk)
        title = root.find_by_id(root.attrs["aria-labelledby"])
        assert "Spritzentausch" in title.all_text()
        # Not an inline row any more: nothing pushes the tiles around.
        assert "basis-full" not in html
        assert f'id="tally-action-{document_type.pk}"' not in html
        assert f'data-testid="tally-row-{document_type.pk}"' not in html
        # aria-modal is decided at open time (bottom sheet only), never baked in.
        assert "aria-modal" not in root.attrs
        assert "backdrop:bg-black/40" in " ".join(root.classes)

    def test_close_button_and_cancel_close_without_a_request(self, client, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        tree = _parse(_open(client, document_type).content.decode())

        root = tree.find_testid(f"tally-inline-row-{document_type.pk}")
        closers = [n for n in root.descendants() if "data-tally-number-close" in n.attrs]
        assert len(closers) == 2
        cancel = root.find_testid(f"tally-cancel-{document_type.pk}")
        assert cancel in closers
        assert cancel.tag == "a" and cancel.attrs["href"]  # without JavaScript it is a plain link
        assert "hx-get" not in cancel.attrs
        close_button = next(n for n in closers if n is not cancel)
        assert close_button.tag == "button" and close_button.attrs["type"] == "button"
        assert close_button.attrs["aria-label"]
        assert "md:hidden" in close_button.classes

    def test_form_contract_is_unchanged_but_posts_into_the_slot(self, client, facility, staff_user):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        tree = _parse(_open(client, document_type).content.decode())

        form = tree.find_testid(f"tally-inline-form-{document_type.pk}")
        assert form.tag == "form"
        assert form.attrs["hx-post"] == reverse("core:tally_increment")
        assert form.attrs["hx-target"] == "#tally-number-form-slot"
        assert form.attrs["hx-swap"] == "innerHTML"
        assert "data-offline-tally-form" in form.attrs
        assert "X-Idempotency-Key" in form.attrs["hx-headers"]
        names = {n.attrs.get("name") for n in form.descendants() if n.tag == "input"}
        assert {"document_type", "idempotency_key", "next", ausgabe.slug, rueckgabe.slug} <= names
        assert tree.find_testid(f"tally-submit-{document_type.pk}") is not None
        assert tree.find_testid(f"tally-form-errors-{document_type.pk}") is not None

    def test_full_page_renders_the_same_form_as_a_static_card(self, client, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        response = client.get(reverse("core:tally_increment"), {"document_type": str(document_type.pk)})

        tree = _parse(response.content.decode())
        root = tree.find_testid(f"tally-inline-row-{document_type.pk}")
        assert response.status_code == 200
        assert "open" in root.attrs  # visible without JavaScript
        assert not {"fixed", "absolute"} & root.classes
        assert tree.find_testid(f"tally-inline-form-{document_type.pk}") is not None

    def test_full_page_form_submits_natively_because_the_page_has_no_slot(self, client, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        response = client.get(reverse("core:tally_increment"), {"document_type": str(document_type.pk)})

        tree = _parse(response.content.decode())
        form = tree.find_testid(f"tally-inline-form-{document_type.pk}")
        assert tree.find_by_id("tally-number-form-slot") is None
        # No htmx target exists here, so htmx must not intercept the submit.
        assert "hx-disable" in form.attrs
        assert not {"hx-post", "hx-target", "hx-swap", "hx-headers"} & set(form.attrs)
        assert form.attrs["method"] == "post"
        assert form.attrs["action"] == reverse("core:tally_increment")

    def test_full_page_form_is_marked_for_the_offline_in_place_submit(self, client, facility, staff_user):
        # Offline, offline-tally.js sends this form in place and rotates its key per queue ACK;
        # it finds the form by this marker and must be loaded on the page.
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        response = client.get(reverse("core:tally_increment"), {"document_type": str(document_type.pk)})

        tree = _parse(response.content.decode())
        form = tree.find_testid(f"tally-inline-form-{document_type.pk}")
        assert "data-tally-page-form" in form.attrs
        assert "data-offline-tally-form" in form.attrs
        assert "js/offline-tally.js" in response.content.decode()

        fragment = _parse(_open(client, document_type).content.decode())
        assert "data-tally-page-form" not in fragment.find_testid(f"tally-inline-form-{document_type.pk}").attrs

    def test_full_page_validation_error_is_shown_on_the_page(self, client, facility, staff_user):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        response = post_tally(
            client,
            {"document_type": str(document_type.pk), ausgabe.slug: "keine-zahl", rueckgabe.slug: "2"},
        )

        tree = _parse(response.content.decode())
        form = tree.find_testid(f"tally-inline-form-{document_type.pk}")
        assert response.status_code == 200
        assert "hx-disable" in form.attrs
        assert [n for n in form.descendants() if n.attrs.get("role") == "alert"]
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_fragment_form_keeps_the_htmx_wiring(self, client, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        tree = _parse(_open(client, document_type).content.decode())

        form = tree.find_testid(f"tally-inline-form-{document_type.pk}")
        assert "hx-disable" not in form.attrs

    def test_number_fields_keep_a_44px_floor_below_md(self, client, facility, staff_user):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        tree = _parse(_open(client, document_type).content.decode())

        form = tree.find_testid(f"tally-inline-form-{document_type.pk}")
        fields = [
            n for n in form.descendants() if n.tag == "input" and n.attrs.get("name") in {ausgabe.slug, rueckgabe.slug}
        ]
        assert len(fields) == 2
        for field in fields:
            assert {"min-h-[44px]", "md:min-h-0"} <= field.classes

    def test_form_class_adds_the_floor_without_dropping_the_base_style(self, facility):
        document_type, ausgabe, _rueckgabe = _number_tally_type(facility)

        form = TallyNumberDataForm(eligibility=evaluate_tally_eligibility(document_type))

        css = form.fields[ausgabe.slug].widget.attrs["class"]
        assert "min-h-[44px]" in css and "md:min-h-0" in css
        assert "border-subtle" in css

    def test_validation_error_rerenders_the_dialog_in_place_not_the_list(self, client, facility, staff_user):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        response = post_tally(
            client,
            {"document_type": str(document_type.pk), ausgabe.slug: "keine-zahl", rueckgabe.slug: "2"},
            HTTP_HX_REQUEST="true",
        )

        tree = _parse(response.content.decode())
        root = tree.find_testid(f"tally-inline-row-{document_type.pk}")
        assert root.tag == "dialog"
        assert "HX-Retarget" not in response
        assert tree.find_testid(f"tally-row-{document_type.pk}") is None
        assert not Event.objects.filter(document_type=document_type).exists()


@pytest.mark.django_db
class TestNumberFormSuccess:
    def test_success_refreshes_the_tile_and_empties_the_slot(self, client, facility, staff_user):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        response = post_tally(
            client,
            {"document_type": str(document_type.pk), ausgabe.slug: "3", rueckgabe.slug: "2"},
            HTTP_HX_REQUEST="true",
        )

        tree = _parse(response.content.decode())
        slot = tree.find_by_id("tally-number-form-slot")
        assert response["HX-Retarget"] == f"#tally-row-{document_type.pk}"
        assert slot.attrs["hx-swap-oob"] == "innerHTML"
        assert slot.children == [] and slot.text.strip() == ""
        assert tree.find_testid(f"tally-count-{document_type.pk}").text.strip() == "1"

    def test_direct_tap_does_not_touch_an_open_number_slot(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)

        response = post_tally(client, {"document_type": str(doc_type_contact.pk)}, HTTP_HX_REQUEST="true")

        assert "tally-number-form-slot" not in response.content.decode()


@pytest.mark.django_db
class TestRowGoneAfterSuccess:
    def test_slot_is_emptied_even_when_the_row_vanished_in_the_meantime(
        self, client, facility, staff_user, monkeypatch
    ):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)
        monkeypatch.setattr(TallyIncrementView, "_row_context", classmethod(lambda cls, *a, **kw: None))

        response = post_tally(
            client,
            {"document_type": str(document_type.pk), ausgabe.slug: "3", rueckgabe.slug: "2"},
            HTTP_HX_REQUEST="true",
        )

        tree = _parse(response.content.decode())
        slot = tree.find_by_id("tally-number-form-slot")
        assert Event.objects.filter(document_type=document_type).count() == 1
        assert response["HX-Retarget"] == f"#tally-row-{document_type.pk}"
        assert response["HX-Reswap"] == "outerHTML"
        assert response["HX-Trigger-After-Swap"] == "zeitstrom-changed"
        assert slot is not None and slot.attrs["hx-swap-oob"] == "innerHTML"
