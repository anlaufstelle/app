"""Tap tiles of the tally panel: whole-tile target, sizes, structure (tally tile redesign)."""

from html.parser import HTMLParser

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.events import build_tally_summary
from tests._tally_helpers import post_tally


class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []
        self.text = ""

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def find_testid(self, testid):
        return next((n for n in self.descendants() if n.attrs.get("data-testid") == testid), None)

    def find_by_id(self, node_id):
        return next((n for n in self.descendants() if n.attrs.get("id") == node_id), None)

    def find_by_attr(self, name):
        return next((n for n in self.descendants() if name in n.attrs), None)

    def all_text(self):
        return self.text + "".join(child.all_text() for child in self.children)

    def contains(self, other):
        node = other
        while node is not None:
            if node is self:
                return True
            node = node.parent
        return False


class _TreeBuilder(HTMLParser):
    _VOID = {"input", "br", "img", "meta", "link", "hr"}

    def __init__(self):
        super().__init__()
        self.root = _Node("root", [], None)
        self._current = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self._current)
        self._current.children.append(node)
        if tag not in self._VOID:
            self._current = node

    def handle_endtag(self, tag):
        node = self._current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self._current = node.parent

    def handle_data(self, data):
        self._current.text += data


def _parse(html):
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root


def _number_tally_type(facility, *, name="Spritzentausch"):
    document_type = DocumentType.objects.create(facility=facility, name=name, tally_enabled=True)
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Anzahl",
        field_type=FieldTemplate.FieldType.NUMBER,
        is_required=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    return document_type


def _render_panel(facility, user):
    summary = build_tally_summary(facility, user)
    for row in summary["rows"]:
        row["tally_increment_url"] = reverse("core:tally_increment") + f"?document_type={row['document_type'].pk}"
    return render_to_string(
        "core/zeitstrom/partials/tally_panel.html",
        {"tally_summary": summary, "tally_next": "/", "tally_filter_state": {}, "tally_context": {}},
    )


@pytest.mark.django_db
class TestTallyTilePanel:
    def test_rows_form_a_two_column_grid_that_fills_with_150px_tiles_on_desktop(
        self, facility, staff_user, doc_type_contact
    ):
        tree = _parse(_render_panel(facility, staff_user))

        row = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        grid = row.parent
        assert {"grid", "grid-cols-2", "md:grid-cols-[repeat(auto-fill,minmax(150px,1fr))]"} <= grid.classes
        assert grid.parent.attrs["data-testid"] == "tally-panel"

    def test_panel_names_the_tap_behaviour_next_to_the_heading(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        hint = tree.find_testid("tally-hint")
        assert hint is not None
        assert "tippen zählt +1 · anonym" in " ".join(hint.text.split())
        assert tree.find_testid("tally-live-context") is not None

    def test_tile_is_at_least_72px_on_mobile_and_64px_on_desktop(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        tile = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        assert {"min-h-[72px]", "md:min-h-[64px]", "relative"} <= tile.classes

    def test_whole_tile_is_the_tap_target_of_the_increment_button(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        tile = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        button = tile.find_testid(f"tally-increment-{doc_type_contact.pk}")
        assert button.tag == "button"
        assert button.attrs["type"] == "submit"
        # The button's pseudo-element stretches over the whole tile.
        assert {"after:absolute", "after:inset-0"} <= button.classes
        assert "btn-secondary" not in button.classes
        assert "+1" in button.text

    def test_increment_button_keeps_a_44px_floor_on_every_breakpoint(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        button = tree.find_testid(f"tally-increment-{doc_type_contact.pk}")
        assert {"min-h-[44px]", "min-w-[44px]"} <= button.classes
        assert not {cls for cls in button.classes if cls.startswith("md:min-h-[") or cls.startswith("md:min-w-[")}

    def test_counter_badge_and_name_are_all_inside_the_tile(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        tile = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        count = tree.find_testid(f"tally-count-{doc_type_contact.pk}")
        badge = tree.find_testid(f"tally-increment-{doc_type_contact.pk}")
        name = tree.find_testid(f"tally-name-{doc_type_contact.pk}")
        for element in (count, badge, name):
            assert tile.contains(element)
        assert name.text.strip() == doc_type_contact.name
        assert {"text-[30px]", "font-bold", "tabular-nums"} <= count.classes

    def test_number_input_tile_is_stretched_too_and_marks_the_extra_input_with_an_ellipsis(self, facility, staff_user):
        document_type = _number_tally_type(facility)

        tree = _parse(_render_panel(facility, staff_user))

        tile = tree.find_testid(f"tally-row-{document_type.pk}")
        link = tile.find_testid(f"tally-increment-{document_type.pk}")
        assert link.tag == "a"
        assert {"after:absolute", "after:inset-0", "min-h-[44px]", "min-w-[44px]"} <= link.classes
        assert "+ …" in " ".join(link.text.split())
        # No-JS fallback and HTMX hooks stay on the element.
        assert link.attrs["href"].startswith(reverse("core:tally_increment"))
        assert link.attrs["hx-get"] == link.attrs["href"]
        assert link.attrs["hx-target"] == f"#tally-action-{document_type.pk}"

    def test_form_hooks_and_labels_survive_the_restyling(self, facility, staff_user, doc_type_contact):
        tree = _parse(_render_panel(facility, staff_user))

        tile = tree.find_testid(f"tally-row-{doc_type_contact.pk}")
        form = next(n for n in tile.descendants() if n.tag == "form")
        assert "data-offline-tally-form" in form.attrs
        assert "data-offline-tally-direct-form" in form.attrs
        assert form.attrs["hx-target"] == f"#tally-row-{doc_type_contact.pk}"
        assert form.attrs["hx-swap"] == "outerHTML"
        assert "X-Idempotency-Key" in form.attrs["hx-headers"]
        count = tile.find_testid(f"tally-count-{doc_type_contact.pk}")
        assert "data-count-label-singular" in count.attrs
        assert "data-pending-aria-plural" in count.attrs
        assert "aria-label" in count.attrs
        assert tile.attrs["id"] == f"tally-row-{doc_type_contact.pk}"

    def test_plain_panel_render_does_not_request_a_pulse(self, facility, staff_user, doc_type_contact):
        html = _render_panel(facility, staff_user)

        assert "data-tally-pulse" not in html


@pytest.mark.django_db
class TestTallyTilePulse:
    def test_fresh_increment_response_flags_the_replaced_tile_for_the_colour_pulse(
        self, client, staff_user, doc_type_contact
    ):
        client.force_login(staff_user)

        response = post_tally(client, {"document_type": str(doc_type_contact.pk)}, HTTP_HX_REQUEST="true")

        tile = _parse(response.content.decode()).find_testid(f"tally-row-{doc_type_contact.pk}")
        assert response.status_code == 200
        assert "data-tally-pulse" in tile.attrs

    def test_replayed_increment_does_not_pulse_again(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        payload = {"document_type": str(doc_type_contact.pk)}
        first = post_tally(
            client, payload, idempotency_key="12345678-1234-4234-8234-123456789abc", HTTP_HX_REQUEST="true"
        )
        replay = post_tally(
            client, payload, idempotency_key="12345678-1234-4234-8234-123456789abc", HTTP_HX_REQUEST="true"
        )

        assert "data-tally-pulse" in first.content.decode()
        assert "data-tally-pulse" not in replay.content.decode()
