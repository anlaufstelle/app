"""Keyboard alternatives on the rendered field board."""

import pytest
from django.urls import reverse

from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate
from tests.test_a11y_board_navigation import TreeParser, find


@pytest.mark.django_db
def test_board_keyboard_controls_render_for_mixed_blocks(client, admin_user, doc_type_contact, foreign_field_group):
    group = FieldGroup.objects.create(document_type=doc_type_contact, name="Keyboard group", sort_order=1)
    fields = list(doc_type_contact.fields.order_by("sort_order"))
    fields[0].field_group = group
    fields[0].save(update_fields=["field_group"])
    empty_group = FieldGroup.objects.create(document_type=doc_type_contact, name="Empty group", sort_order=3)
    other_type = DocumentType.objects.create(facility=doc_type_contact.facility, name="Other type")
    other_group = FieldGroup.objects.create(document_type=other_type, name="Other type group")
    template = FieldTemplate.objects.create(facility=doc_type_contact.facility, name="Another field", field_type="text")
    fields.append(
        DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=template, field_group=group, sort_order=1
        )
    )
    client.force_login(admin_user)
    root = TreeParser(
        client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})).content.decode()
    ).root
    board = find(root, "id", "field-board")
    status = find(root, "id", "field-board-status")
    assert status.get("role") == "status"
    assert status.get("aria-live") == "polite"
    # The status region survives the outerHTML swap.
    assert status not in list(board.iter())
    containers = [node for node in board.iter() if node.get("data-sortable")]
    for container in containers:
        rows = [node for node in container if "data-block" in node.attrib or "data-dtf-id" in node.attrib]
        for index, row in enumerate(rows):
            controls = next(node for node in row if "data-order-controls" in node.attrib)
            for direction, boundary in [("up", index == 0), ("down", index == len(rows) - 1)]:
                button = find(controls, "data-board-order", direction)
                assert button.tag == "button" and button.get("type") == "button"
                assert row.get("data-item-name") in button.get("aria-label", "")
                assert ("disabled" in button.attrib) == boundary
    for field in fields:
        row = find(board, "data-dtf-id", str(field.pk))
        select = next(row.iter("select"))
        assert select.get("aria-label") and field.field_template.name in select.get("aria-label")
        assert {option.get("value") for option in select} == {"", str(group.pk), str(empty_group.pk)}
        assert foreign_field_group.name not in "".join(select.itertext())
        assert other_group.name not in "".join(select.itertext())
        assert find(row, "data-board-move", "").get("type") == "button"
    for handle in board.iter("span"):
        if "drag-handle" in handle.get("class", "").split():
            assert handle.get("aria-hidden") == "true"


@pytest.mark.django_db
def test_board_controls_opt_out_of_global_focus_management(client, admin_user, doc_type_contact):
    """#2554: follow-up refreshes (preview, conditions) must not move focus off a pressed board control."""
    group = FieldGroup.objects.create(document_type=doc_type_contact, name="Focus group", sort_order=1)
    field = doc_type_contact.fields.order_by("sort_order").first()
    field.field_group = group
    field.save(update_fields=["field_group"])
    client.force_login(admin_user)
    root = TreeParser(
        client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})).content.decode()
    ).root
    board = find(root, "id", "field-board")
    controls = [node for node in board.iter() if "data-order-controls" in node.attrib]
    assert controls
    for node in controls:
        assert "data-hx-no-focus" in node.attrib


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", ["config_reorder_blocks", "config_reorder_fields", "config_move_field"])
def test_board_endpoints_return_429_after_sixty_requests(
    client, admin_user, doc_type_contact, ratelimit_enabled, endpoint
):
    client.force_login(admin_user)
    url = reverse("core:" + endpoint, kwargs={"pk": doc_type_contact.pk})
    # Invalid batches exercise the real limiter without changing configuration.
    for _ in range(60):
        response = client.post(url, {}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400
    response = client.post(url, {}, HTTP_HX_REQUEST="true")
    assert response.status_code == 429
    assert "Zu viele Anfragen" in response.content.decode()
    assert "field-board" not in response.content.decode()
