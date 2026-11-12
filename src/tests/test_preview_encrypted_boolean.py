"""Feed preview of encrypted fields: the stored envelope is decrypted before
the empty and boolean checks, so an encrypted ``False`` never previews as "Ja".
"""

import pytest
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.events import enrich_events_with_preview
from core.services.file_vault import is_encrypted_value
from tests._factories import make_document_type, make_event


def _preview(event, user):
    event.refresh_from_db()
    feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
    enrich_events_with_preview(feed_items, user)
    return event.preview_fields, event.expanded_fields


@pytest.fixture
def medical_doc_type(facility):
    """Mirrors the default package: a HIGH boolean field that is always encrypted."""
    dt = make_document_type(
        facility=facility,
        name="Medizinische Versorgung",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    hospital = FieldTemplate.objects.create(
        facility=facility,
        name="Krankenhaus",
        slug="krankenhaus",
        field_type="boolean",
        is_encrypted=True,
        sensitivity=DocumentType.Sensitivity.HIGH,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=hospital, sort_order=0)
    return dt


def _make(facility, client, doc_type, user, data):
    return make_event(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=data,
        created_by=user,
    )


@pytest.mark.django_db
class TestEncryptedBooleanPreview:
    def test_encrypted_false_is_neither_previewed_nor_expanded(
        self, facility, admin_user, client_identified, medical_doc_type
    ):
        event = _make(facility, client_identified, medical_doc_type, admin_user, {"krankenhaus": False})
        event.refresh_from_db()
        assert is_encrypted_value(event.data_json["krankenhaus"])  # stored as an envelope

        preview, expanded = _preview(event, admin_user)

        assert preview == []
        assert expanded == []

    def test_encrypted_true_previews_as_yes(self, facility, admin_user, client_identified, medical_doc_type):
        event = _make(facility, client_identified, medical_doc_type, admin_user, {"krankenhaus": True})

        preview, expanded = _preview(event, admin_user)

        assert preview == [{"label": "Krankenhaus", "value": "Ja", "is_textarea": False}]
        assert expanded == preview

    def test_undecryptable_boolean_shows_placeholder_never_yes(
        self, facility, admin_user, client_identified, medical_doc_type
    ):
        event = _make(facility, client_identified, medical_doc_type, admin_user, {})
        Event.objects.filter(pk=event.pk).update(
            data_json={"krankenhaus": {"__encrypted__": True, "value": "not-a-fernet-token"}}
        )

        preview, expanded = _preview(event, admin_user)

        assert [entry["value"] for entry in preview] == ["[verschlüsselt]"]
        assert [entry["value"] for entry in expanded] == ["[verschlüsselt]"]

    def test_undecryptable_text_shows_placeholder(self, facility, admin_user, client_identified):
        dt = make_document_type(facility=facility, name="EncryptedText")
        ft = FieldTemplate.objects.create(
            facility=facility, name="Notizfeld", slug="notizfeld", field_type="text", is_encrypted=True
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = _make(facility, client_identified, dt, admin_user, {})
        Event.objects.filter(pk=event.pk).update(
            data_json={"notizfeld": {"__encrypted__": True, "value": "not-a-fernet-token"}}
        )

        preview, _expanded = _preview(event, admin_user)

        assert [entry["value"] for entry in preview] == ["[verschlüsselt]"]

    def test_encrypted_text_number_and_select_preview_unchanged(self, facility, admin_user, client_identified):
        dt = make_document_type(facility=facility, name="EncryptedMix")
        specs = [
            ("Bemerkung", "bemerkung", "text", None),
            ("Anzahl", "anzahl", "number", None),
            (
                "Versorgung",
                "versorgung",
                "select",
                [{"slug": "wunde", "label": "Wundversorgung", "is_active": True}],
            ),
            (
                "Hilfen",
                "hilfen",
                "multi_select",
                [
                    {"slug": "a", "label": "Alpha", "is_active": True},
                    {"slug": "b", "label": "Beta", "is_active": True},
                ],
            ),
        ]
        for order, (name, slug, field_type, options) in enumerate(specs):
            ft = FieldTemplate.objects.create(
                facility=facility,
                name=name,
                slug=slug,
                field_type=field_type,
                is_encrypted=True,
                options_json=options or [],
            )
            DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=order)
        event = _make(
            facility,
            client_identified,
            dt,
            admin_user,
            {"bemerkung": "kurz", "anzahl": 3, "versorgung": "wunde", "hilfen": ["a", "b"]},
        )
        event.refresh_from_db()
        assert all(is_encrypted_value(event.data_json[slug]) for _n, slug, _t, _o in specs)

        _preview_fields, expanded = _preview(event, admin_user)

        # Compared as rendered text: the template prints the value with ``{{ }}``.
        assert [(entry["label"], str(entry["value"])) for entry in expanded] == [
            ("Bemerkung", "kurz"),
            ("Anzahl", "3"),
            ("Versorgung", "Wundversorgung"),
            ("Hilfen", "Alpha, Beta"),
        ]


@pytest.mark.django_db
class TestEncryptedBooleanFeedValidator:
    """The watch validator hashes the raw envelope instead of decrypting it.

    It may therefore treat an encrypted ``False`` as present, but it must
    change whenever the rendered preview changes.
    """

    def test_flipping_an_encrypted_boolean_changes_the_feed_validator(
        self, facility, admin_user, client_identified, medical_doc_type
    ):
        from core.services.zeitstrom_filters import resolve_zeitstrom_state
        from core.services.zeitstrom_watch import build_zeitstrom_component_validator

        event = _make(facility, client_identified, medical_doc_type, admin_user, {"krankenhaus": False})
        state = resolve_zeitstrom_state({}, facility)
        before = build_zeitstrom_component_validator(admin_user, facility, state, "feed")

        event.refresh_from_db()
        event.data_json = {"krankenhaus": True}
        event.save()
        after_true = build_zeitstrom_component_validator(admin_user, facility, state, "feed")

        event.data_json = {"krankenhaus": False}
        event.save()
        after_false = build_zeitstrom_component_validator(admin_user, facility, state, "feed")

        assert before != after_true
        assert after_true != after_false
