"""Field protection display and policy messages across locales (Refs #2530).

The protection display lives in ``core.services.compliance.sensitivity``. Its
texts must reach the browser through gettext on every request, never as raw
literals, and the stable level/role vocabulary must not change in German.
"""

import html
import re
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test.html import parse_html
from django.urls import reverse
from django.utils import translation
from django.utils.html import strip_tags

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.compliance.sensitivity import field_protection, validate_explicit_high_field
from tests._factories import make_document_type

pytestmark = pytest.mark.django_db

# Vocabulary of the protection display and of the high-level policy message.
GERMAN_WORDS = re.compile(
    r"\b(?:Erhöht|Hoch|Unbekannte|Stufe|wie|Typ|Verschlüsselt|Klartext|Feldvorlage|"
    r"Suchindex|Nicht|Sichtbar|für|Keine|Rolle|Assistenz|Fachkraft|Leitung|Anwendungsbetreuung|"
    r"Systemadministration|Dokumentationstyp|erben|Wählen|ausdrückliche|Feldstufe|Berichtsfelder|sonst|"
    r"darf|nicht|Im)\b"
)

LEVELS = ["normal", "elevated", "high", "unknown"]
FIELD_LEVELS = ["", "normal", "elevated", "high", "unknown"]


def translate(message):
    """Replace every message by a marker; placeholders stay visible."""
    return "Translated " + " ".join(re.findall(r"%\([a-zA-Z0-9_]+\)s", message))


def _visible_text(markup):
    return " ".join(html.unescape(strip_tags(markup)).split())


@pytest.mark.parametrize("type_level", LEVELS + [None])
@pytest.mark.parametrize("field_level", FIELD_LEVELS)
@pytest.mark.parametrize("encrypted", [False, True])
def test_protection_display_uses_active_translation_backend(facility, type_level, field_level, encrypted):
    """Every branch of the display (level, origin, index, roles) is resolved per request."""
    doc_type = make_document_type(facility=facility, name="Protection", sensitivity=type_level) if type_level else None
    field = FieldTemplate.objects.create(
        facility=facility, name="Field", sensitivity=field_level, is_encrypted=encrypted
    )
    with translation.override("en"), patch("django.utils.translation._trans.gettext", side_effect=translate):
        protection = field_protection(doc_type, field)
        markup = render_to_string(
            "core/config/partials/field_protection.html",
            {"protection": protection, "show_roles": True, "show_origin": True, "show_index": True},
        )
    text = _visible_text(markup)
    assert "Translated" in text
    assert not GERMAN_WORDS.findall(text), (type_level, field_level, GERMAN_WORDS.findall(text))


def test_roles_without_permitted_role_use_translated_placeholder():
    protection = {
        "level": "x",
        "color": "gray",
        "roles": "",
        "inherits": True,
        "field_level": "x",
        "higher": False,
        "is_encrypted": False,
        "is_indexed": False,
    }
    with translation.override("en"), patch("django.utils.translation._trans.gettext", side_effect=translate):
        markup = render_to_string(
            "core/config/partials/field_protection.html",
            {"protection": protection, "show_roles": True, "show_origin": True, "show_index": True},
        )
    assert not GERMAN_WORDS.findall(_visible_text(markup))


def test_explicit_high_message_uses_active_translation_backend():
    def message():
        with pytest.raises(ValidationError) as caught:
            validate_explicit_high_field(DocumentType.Sensitivity.HIGH, "", "Name")
        return caught.value.messages[0]

    with translation.override("en"), patch("django.utils.translation._trans.gettext", side_effect=translate):
        text = message()
    assert text.startswith("Translated")
    assert not GERMAN_WORDS.findall(text)
    # German source text keeps its placeholder substitution and wording.
    with translation.override("de"):
        assert message() == (
            "Feldvorlage „Name“ darf in einem Dokumentationstyp mit Stufe „Hoch“ nicht erben. "
            "Wählen Sie eine ausdrückliche Feldstufe: „Erhöht“ für Berichtsfelder, sonst „Hoch“."
        )


def _protection_blocks(client, user, doc_type):
    client.force_login(user)
    response = client.get(reverse("core:config_doctype_detail", args=[doc_type.pk]))
    assert response.status_code == 200
    root = parse_html(response.content.decode())
    found = []

    def walk(element):
        for child in element.children:
            if isinstance(child, str):
                continue
            if dict(child.attributes).get("data-testid") == "field-protection":
                found.append(_visible_text(str(child)))
            walk(child)

    walk(root)
    return found


def test_german_board_keeps_protection_wording(client, admin_user, facility):
    admin_user.preferred_language = "de"
    admin_user.save(update_fields=["preferred_language"])
    doc_type = make_document_type(facility=facility, name="Board type", sensitivity="elevated")
    field = FieldTemplate.objects.create(facility=facility, name="Restricted", sensitivity="high")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    with translation.override("de"):
        blocks = _protection_blocks(client, admin_user, doc_type)
    assert len(blocks) == 1
    for expected in ("Hoch", "Verschlüsselt", "Feldvorlage:", "Nicht im Suchindex", "Sichtbar für:", "Leitung"):
        assert expected in blocks[0]


def test_english_board_has_no_german_protection_text(client, admin_user, facility):
    admin_user.preferred_language = "en"
    admin_user.save(update_fields=["preferred_language"])
    doc_type = make_document_type(facility=facility, name="Board type", sensitivity="elevated")
    field = FieldTemplate.objects.create(facility=facility, name="Restricted", sensitivity="high")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    with translation.override("en"):
        blocks = _protection_blocks(client, admin_user, doc_type)
    assert blocks
    assert not GERMAN_WORDS.findall(" ".join(blocks))


def test_english_board_renders_protection_card(client, admin_user, facility):
    admin_user.preferred_language = "en"
    admin_user.save(update_fields=["preferred_language"])
    doc_type = make_document_type(facility=facility, name="Board type", sensitivity="elevated")
    field = FieldTemplate.objects.create(facility=facility, name="Restricted", sensitivity="high")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    with translation.override("en"):
        blocks = _protection_blocks(client, admin_user, doc_type)
    assert len(blocks) == 1
