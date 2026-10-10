"""Public configuration responses expose the effective field protection (Refs #2522)."""

from html import unescape

import pytest
from django.test.html import parse_html
from django.urls import reverse
from django.utils.html import strip_tags

from core.models import DocumentTypeField, FieldGroup, FieldTemplate
from tests._factories import make_document_type

pytestmark = pytest.mark.django_db


def elements(root, attribute, value):
    for child in root.children:
        if isinstance(child, str):
            continue
        if dict(child.attributes).get(attribute) == value:
            yield child
        yield from elements(child, attribute, value)


def text_content(element):
    return " ".join(unescape(strip_tags(str(element))).split())


@pytest.mark.parametrize("type_level", ["normal", "elevated", "high", "unknown"])
@pytest.mark.parametrize("field_level", ["", "normal", "elevated", "high", "unknown"])
@pytest.mark.parametrize("grouped", [False, True])
def test_board_exposes_effective_protection(client, admin_user, facility, type_level, field_level, grouped):
    doc_type = make_document_type(facility=facility, name="Protection type", sensitivity=type_level)
    field = FieldTemplate.objects.create(facility=facility, name="Protection field", sensitivity=field_level)
    group = FieldGroup.objects.create(document_type=doc_type, name="Fields") if grouped else None
    link = DocumentTypeField(document_type=doc_type, field_template=field, field_group=group)
    if type_level == "high" and field_level == "":
        # The display must stay accurate even for configuration injected outside model saves.
        DocumentTypeField.objects.bulk_create([link])
    else:
        link.save()
    client.force_login(admin_user)
    response = client.get(reverse("core:config_doctype_detail", args=[doc_type.pk]))
    row = next(elements(parse_html(response.content.decode()), "data-dtf-id", str(link.pk)))
    text = text_content(row)
    ranks = {"normal": 0, "elevated": 1, "high": 2, "unknown": 3}
    expected_rank = max(ranks[type_level], ranks[field_level]) if field_level else ranks[type_level]
    assert ["Normal", "Erhöht", "Hoch", "Unbekannte Stufe"][expected_rank] in text
    assert ("Verschlüsselt" if field.is_encrypted else "Klartext") in text
    indexed = not field.is_encrypted and expected_rank == 0
    assert ("Im Suchindex" if indexed else "Nicht im Suchindex") in text
    assert "Sichtbar für:" in text
    expected_roles = ["Assistenz", "Fachkraft", "Leitung", "Anwendungsbetreuung"]
    for index, role in enumerate(expected_roles):
        assert (role in text) == (expected_rank <= [0, 1, 2, 2][index])
    if not field_level:
        assert "wie Typ" in text
    elif ranks[field_level] > ranks[type_level]:
        assert "Feldvorlage:" in text
        assert "1 Feld ist höher eingestuft" in response.content.decode()


def test_list_shows_own_level_and_index_per_assigned_type(client, admin_user, facility):
    field = FieldTemplate.objects.create(facility=facility, name="Shared field")
    for level in ("normal", "elevated"):
        doc_type = make_document_type(facility=facility, name=f"Type {level}", sensitivity=level)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    client.force_login(admin_user)
    response = client.get(reverse("core:config_fieldtemplate_list"), HTTP_HX_REQUEST="true")
    soup = parse_html(response.content.decode())
    assert "Stufe" in text_content(soup)
    row = next(elements(soup, "data-testid", "fieldtemplate-row"))
    text = text_content(row)
    assert "wie Typ" in text
    assert "Klartext" in text
    for name, status in (("Type normal", "Im Suchindex"), ("Type elevated", "Nicht im Suchindex")):
        item = next(item for item in elements(row, "data-testid", "field-index-status") if name in text_content(item))
        assert status in text_content(item)


def test_htmx_assignment_removal_and_reorder_keep_badges(client, admin_user, facility):
    doc_type = make_document_type(facility=facility, name="Board type")
    field = FieldTemplate.objects.create(facility=facility, name="Restricted field", sensitivity="high")
    client.force_login(admin_user)
    response = client.post(
        reverse("core:config_doctype_field_add", args=[doc_type.pk]),
        {"field_template": str(field.pk)},
        HTTP_HX_REQUEST="true",
    )
    assert "Verschlüsselt" in response.content.decode()
    assert "Nicht im Suchindex" in response.content.decode()
    link = doc_type.fields.get()
    response = client.post(
        reverse("core:config_reorder_blocks", args=[doc_type.pk]),
        {"blocks": f"field:{link.pk}"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "Verschlüsselt" in response.content.decode()
    response = client.post(
        reverse("core:config_doctype_field_remove", args=[doc_type.pk, link.pk]), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert "Restricted field" not in text_content(
        next(elements(parse_html(response.content.decode()), "data-sortable", "blocks"))
    )
