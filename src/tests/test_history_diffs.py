"""Tests for EventHistory diff template tag (compute_diff)."""

import pytest
from django.test import override_settings
from django.utils import timezone

from core.models import EventHistory
from core.services.events import create_event, soft_delete_event, update_event
from core.services.file_vault import EncryptionKeyMissing
from core.templatetags.history_tags import ENCRYPTED_PLACEHOLDER, RESTRICTED_PLACEHOLDER, compute_diff


@pytest.mark.django_db
class TestComputeDiffUpdate:
    """Test compute_diff for UPDATE actions."""

    def test_update_shows_changed_fields(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
        )
        update_event(event, staff_user, {"dauer": 30, "notiz": "Erstgespräch"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 1
        field = result["fields"][0]
        assert field["label"] == "Dauer"
        assert field["old_value"] == "15"
        assert field["new_value"] == "30"
        assert field["changed"] is True

    def test_update_all_fields_changed(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Alt"},
        )
        update_event(event, staff_user, {"dauer": 30, "notiz": "Neu"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 2
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Notiz" in labels

    def test_update_no_changes_yields_empty_fields(self, facility, staff_user, doc_type_contact):
        """If data_before == data_after, no changed fields are returned."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        # Force an update with same data
        update_event(event, staff_user, {"dauer": 15})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 0


@pytest.mark.django_db
class TestComputeDiffCreate:
    """Test compute_diff for CREATE actions."""

    def test_create_shows_all_fields(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        assert result["action"] == "create"
        assert len(result["fields"]) == 2
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Notiz" in labels
        for f in result["fields"]:
            assert "value" in f

    def test_create_resolves_labels(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        assert result["fields"][0]["label"] == "Dauer"


@pytest.mark.django_db
class TestComputeDiffDelete:
    """Test compute_diff for DELETE actions."""

    def test_delete_shows_redacted_fields(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Abschluss"},
        )
        soft_delete_event(event, staff_user)

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()
        result = compute_diff(entry)

        assert result["action"] == "delete"
        assert len(result["fields"]) == 2
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Notiz" in labels
        # Values are redacted, not the original data
        for f in result["fields"]:
            assert f["value"] == "\u2013 (gelöscht)"


@pytest.mark.django_db
class TestComputeDiffEncrypted:
    """Fail-closed path: without a user, encrypted fields stay masked."""

    def test_encrypted_field_masked_on_create(self, facility, staff_user, doc_type_crisis):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertrauliche Info"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        assert result["action"] == "create"
        assert len(result["fields"]) == 1
        assert result["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER

    def test_encrypted_field_masked_on_update(self, facility, staff_user, doc_type_crisis):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Alt"},
        )
        update_event(event, staff_user, {"notiz-krise": "Neu"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 1
        assert result["fields"][0]["old_value"] == ENCRYPTED_PLACEHOLDER
        assert result["fields"][0]["new_value"] == ENCRYPTED_PLACEHOLDER

    def test_encrypted_field_masked_on_delete(self, facility, staff_user, doc_type_crisis):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Geheim"},
        )
        soft_delete_event(event, staff_user)

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()
        # data_before is now redacted (only field names, no values)
        assert entry.data_before == {"_redacted": True, "fields": ["notiz-krise"]}
        result = compute_diff(entry)

        assert result["action"] == "delete"
        assert len(result["fields"]) == 1
        # Encrypted fields still show the encrypted placeholder even in redacted mode
        assert result["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER


@pytest.mark.django_db
class TestComputeDiffSensitivity:
    """Test that sensitive fields are masked based on user role."""

    def test_compute_diff_masks_sensitive_fields_for_staff(self, facility, staff_user, doc_type_crisis):
        """Staff user cannot see HIGH-sensitivity fields — they must be masked."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertrauliche Info"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()

        # Without user: encrypted placeholder (is_encrypted=True takes precedence)
        result_no_user = compute_diff(entry)
        assert result_no_user["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER

        # With staff user: HIGH sensitivity field → restricted placeholder
        # (restricted check runs before encrypted, so restricted wins)
        result_with_user = compute_diff(entry, user=staff_user)
        assert result_with_user["fields"][0]["value"] == RESTRICTED_PLACEHOLDER

    def test_compute_diff_masks_elevated_field_for_assistant(self, facility, assistant_user):
        """Assistant user cannot see ELEVATED-sensitivity fields — they must be masked."""
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        # NORMAL doc type so assistant can create events, but with an ELEVATED field
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt (Elevated-Feld)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Vertrauliche Notiz",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)

        event = create_event(
            facility=facility,
            user=assistant_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"vertrauliche-notiz": "Geheim"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()

        result = compute_diff(entry, user=assistant_user)
        assert result["fields"][0]["value"] == RESTRICTED_PLACEHOLDER

    def test_compute_diff_shows_sensitive_fields_for_lead(self, facility, doc_type_crisis):
        """Lead users see the decrypted value of permitted HIGH fields."""
        from core.models.user import User

        lead_user = User.objects.create_user(
            username="testlead_hist",
            role=User.Role.LEAD,
            facility=facility,
        )
        event = create_event(
            facility=facility,
            user=lead_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertraulich"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()

        result = compute_diff(entry, user=lead_user)
        assert result["fields"][0]["value"] == "Vertraulich"

    def test_compute_diff_masks_sensitive_update_for_staff(self, facility, staff_user, doc_type_crisis):
        """Update diffs must also mask sensitive fields for staff."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Alt"},
        )
        update_event(event, staff_user, {"notiz-krise": "Neu"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()

        result = compute_diff(entry, user=staff_user)
        assert len(result["fields"]) == 1
        assert result["fields"][0]["old_value"] == RESTRICTED_PLACEHOLDER
        assert result["fields"][0]["new_value"] == RESTRICTED_PLACEHOLDER

    def test_compute_diff_masks_sensitive_delete_for_staff(self, facility, staff_user, doc_type_crisis):
        """Delete diffs must also mask sensitive fields for staff."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Geheim"},
        )
        soft_delete_event(event, staff_user)

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()

        result = compute_diff(entry, user=staff_user)
        assert len(result["fields"]) == 1
        assert result["fields"][0]["value"] == RESTRICTED_PLACEHOLDER


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "sensitivity", "expected"),
    [
        ("assistant", "normal", "Klartext"),
        ("assistant", "elevated", RESTRICTED_PLACEHOLDER),
        ("assistant", "high", RESTRICTED_PLACEHOLDER),
        ("staff", "normal", "Klartext"),
        ("staff", "elevated", "Klartext"),
        ("staff", "high", RESTRICTED_PLACEHOLDER),
        ("lead", "normal", "Klartext"),
        ("lead", "elevated", "Klartext"),
        ("lead", "high", "Klartext"),
    ],
)
def test_encrypted_history_role_sensitivity_matrix(facility, role, sensitivity, expected):
    """Assistenz/Fachkraft/Leitung × NORMAL/ELEVATED/HIGH (Refs #1961)."""
    from core.models import DocumentType, DocumentTypeField, FieldTemplate
    from core.models.user import User

    user = User.objects.create_user(
        username=f"history-{role}-{sensitivity}",
        role=role,
        facility=facility,
    )
    document_type = DocumentType.objects.create(
        facility=facility,
        name=f"Historie {role} {sensitivity}",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    field = FieldTemplate.objects.create(
        facility=facility,
        name=f"Historienwert {role} {sensitivity}",
        field_type=FieldTemplate.FieldType.TEXT,
        sensitivity=sensitivity,
        is_encrypted=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    event = create_event(
        facility=facility,
        user=user,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={field.slug: "Klartext"},
    )
    entry = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)

    result = compute_diff(entry, user=user)

    assert result["fields"][0]["value"] == expected


@pytest.mark.django_db
class TestComputeDiffFieldMetadata:
    """Test that frozen field_metadata is used instead of live FieldTemplates."""

    def test_field_rename_does_not_affect_old_history(self, facility, staff_user, doc_type_contact):
        """After renaming a FieldTemplate, old history entries still show the original name."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        assert entry.field_metadata["dauer"]["name"] == "Dauer"

        from core.models import FieldTemplate

        ft = FieldTemplate.objects.get(facility=facility, slug="dauer")
        ft.name = "Dauer (Minuten)"
        ft.save()

        result = compute_diff(entry)
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Dauer (Minuten)" not in labels

    def test_legacy_entry_without_metadata_falls_back(self, facility, staff_user, doc_type_contact):
        """Legacy entries without field_metadata fall back to live FieldTemplates."""
        from core.models import Event

        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            created_by=staff_user,
        )
        legacy_entry = EventHistory.objects.create(
            event=event,
            changed_by=staff_user,
            action=EventHistory.Action.CREATE,
            data_after={"dauer": 15},
        )

        result = compute_diff(legacy_entry)
        assert result["fields"][0]["label"] == "Dauer"


@pytest.mark.django_db
class TestComputeDiffFallbackLabel:
    """Test that unknown slugs use fallback labels."""

    def test_unknown_slug_stripped_by_validation(self, facility, staff_user, doc_type_contact):
        """Unknown fields are stripped by _validate_data_json before saving."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "unknown-field": "value"},
        )

        # Unknown field was stripped during creation
        assert "unknown-field" not in event.data_json
        assert event.data_json == {"dauer": 15}

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Unknown Field" not in labels


# ---------------------------------------------------------------------------
# Systematische Maskierungs-Matrix (Refs #1162): Aktionstyp × encrypted ×
# restricted. Dieses Sicherheitsnetz fixiert das byte-identische
# Maskierungsverhalten, bevor der Kern aus dem Templatetag in den
# Service-Layer (services/events/history_diff.py) gezogen wird.
#
# Maskierungs-Vorrang (aus der bestehenden Implementierung abgeleitet):
#   restricted  > encrypted > Roh-/Formatierungswert
# ``restricted`` ist ausschliesslich dann True, wenn ein ``user`` gesetzt ist
# UND ``user_can_see_field`` False liefert (Feld-Sensitivity > Rollen-Maximum).
# Ohne ``user`` ist nie etwas restricted.
# ---------------------------------------------------------------------------


@pytest.fixture
def doc_type_masking_matrix(facility):
    """NORMAL-DocType mit vier Feldern, die encrypted × (high-)sensitivity aufspannen.

    Der DocType ist NORMAL, damit auch ein STAFF-User Events anlegen darf; die
    Maskierung wird ausschliesslich ueber die *Feld*-Sensitivity (high) und das
    ``is_encrypted``-Flag gesteuert. Fuer einen STAFF-User (max. ELEVATED) ist
    jedes ``high``-Feld restricted, jedes Feld ohne Sensitivity-Override nicht.

    Slugs/Eigenschaften:
      - ``enc-restr``    : is_encrypted=True,  sensitivity=high  -> enc+restr
      - ``enc-open``     : is_encrypted=True,  sensitivity=""    -> enc, frei
      - ``plain-restr``  : is_encrypted=False, sensitivity=high  -> restr
      - ``plain-open``   : is_encrypted=False, sensitivity=""    -> frei
    """
    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Masking-Matrix",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )

    specs = [
        ("Enc Restr", True, "high"),
        ("Enc Open", True, ""),
        ("Plain Restr", False, "high"),
        ("Plain Open", False, ""),
    ]
    for sort_order, (name, is_encrypted, sensitivity) in enumerate(specs):
        # Refs #1270 (T5): seit dem save()-Backstop erzwingt FieldTemplate.save()
        # die HIGH⇒verschlüsselt-Invariante. Die Kombination "unverschlüsselt +
        # HIGH" (``plain-restr``) ist daher nur noch als *Bestandsdatensatz*
        # möglich (vor der Invariante angelegt). Um den Render-Zweig "restricted,
        # aber nicht encrypted" weiter abzudecken, legen wir das Feld an und
        # setzen die HIGH-Sensitivity per ``QuerySet.update()`` nachträglich —
        # das umgeht ``save()`` wie eine Legacy-/Bulk-Schreiboperation.
        needs_legacy_high = sensitivity == "high" and not is_encrypted
        ft = FieldTemplate.objects.create(
            facility=facility,
            name=name,
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=is_encrypted,
            sensitivity="" if needs_legacy_high else sensitivity,
        )
        if needs_legacy_high:
            FieldTemplate.objects.filter(pk=ft.pk).update(sensitivity="high")
            ft.refresh_from_db()
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=sort_order)

    return doc_type


# Slugs werden aus den Namen via slugify abgeleitet.
_SLUG_ENC_RESTR = "enc-restr"
_SLUG_ENC_OPEN = "enc-open"
_SLUG_PLAIN_RESTR = "plain-restr"
_SLUG_PLAIN_OPEN = "plain-open"

_RAW_VALUES = {
    _SLUG_ENC_RESTR: "geheim-er",
    _SLUG_ENC_OPEN: "geheim-eo",
    _SLUG_PLAIN_RESTR: "klar-pr",
    _SLUG_PLAIN_OPEN: "klar-po",
}


def _expected_value(slug, raw, *, with_user):
    """Erwarteter Wert nach restricted > file > permitted/plaintext > raw."""
    is_encrypted = slug in (_SLUG_ENC_RESTR, _SLUG_ENC_OPEN)
    is_high = slug in (_SLUG_ENC_RESTR, _SLUG_PLAIN_RESTR)
    if with_user and is_high:
        return RESTRICTED_PLACEHOLDER
    if is_encrypted:
        return str(raw) if with_user else ENCRYPTED_PLACEHOLDER
    return str(raw)


@pytest.mark.django_db
class TestComputeDiffMaskingMatrix:
    """Aktionstyp × encrypted × restricted — vollstaendige Maskierungs-Matrix."""

    @pytest.mark.parametrize("with_user", [False, True], ids=["no_user", "staff_user"])
    def test_create_matrix(self, facility, staff_user, doc_type_masking_matrix, with_user):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json=dict(_RAW_VALUES),
        )
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()

        result = compute_diff(entry, user=staff_user if with_user else None)
        assert result["action"] == "create"

        by_label = {f["label"]: f["value"] for f in result["fields"]}
        for slug, raw in _RAW_VALUES.items():
            label = slug.replace("-", " ").title()
            assert by_label[label] == _expected_value(slug, raw, with_user=with_user)

    @pytest.mark.parametrize("with_user", [False, True], ids=["no_user", "staff_user"])
    def test_update_matrix(self, facility, staff_user, doc_type_masking_matrix, with_user):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json=dict(_RAW_VALUES),
        )
        new_values = {slug: f"{raw}-neu" for slug, raw in _RAW_VALUES.items()}
        update_event(event, staff_user, new_values)
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()

        result = compute_diff(entry, user=staff_user if with_user else None)
        assert result["action"] == "update"

        by_label = {f["label"]: f for f in result["fields"]}
        # Alle vier Felder haben sich geaendert -> alle erscheinen im Diff.
        assert len(result["fields"]) == len(_RAW_VALUES)
        for slug, raw in _RAW_VALUES.items():
            label = slug.replace("-", " ").title()
            field = by_label[label]
            assert field["changed"] is True
            assert field["old_value"] == _expected_value(slug, raw, with_user=with_user)
            assert field["new_value"] == _expected_value(slug, f"{raw}-neu", with_user=with_user)

    @pytest.mark.parametrize("with_user", [False, True], ids=["no_user", "staff_user"])
    def test_delete_redacted_matrix(self, facility, staff_user, doc_type_masking_matrix, with_user):
        """Soft-Delete schreibt eine redacted History (nur Feldnamen, keine Werte)."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json=dict(_RAW_VALUES),
        )
        soft_delete_event(event, staff_user)
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()
        assert entry.data_before.get("_redacted") is True

        result = compute_diff(entry, user=staff_user if with_user else None)
        assert result["action"] == "delete"

        by_label = {f["label"]: f["value"] for f in result["fields"]}
        for slug in _RAW_VALUES:
            label = slug.replace("-", " ").title()
            is_encrypted = slug in (_SLUG_ENC_RESTR, _SLUG_ENC_OPEN)
            is_high = slug in (_SLUG_ENC_RESTR, _SLUG_PLAIN_RESTR)
            if with_user and is_high:
                expected = RESTRICTED_PLACEHOLDER
            elif is_encrypted:
                expected = "– (gelöscht)" if with_user else ENCRYPTED_PLACEHOLDER
            else:
                expected = "– (gelöscht)"
            assert by_label[label] == expected

    @pytest.mark.parametrize("with_user", [False, True], ids=["no_user", "staff_user"])
    def test_delete_unredacted_matrix(self, facility, staff_user, doc_type_masking_matrix, with_user):
        """Legacy/unredacted Delete-History mit Werten -> _display_value-Pfad."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json=dict(_RAW_VALUES),
        )
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        # Kunstgriff: einen DELETE-Eintrag mit echten Werten (ohne _redacted) bauen,
        # um den nicht-redacted Loeschpfad isoliert zu pruefen.
        delete_entry = EventHistory.objects.create(
            event=event,
            changed_by=staff_user,
            action=EventHistory.Action.DELETE,
            data_before=dict(_RAW_VALUES),
            field_metadata=entry.field_metadata,
        )

        result = compute_diff(delete_entry, user=staff_user if with_user else None)
        assert result["action"] == "delete"

        by_label = {f["label"]: f["value"] for f in result["fields"]}
        for slug, raw in _RAW_VALUES.items():
            label = slug.replace("-", " ").title()
            assert by_label[label] == _expected_value(slug, raw, with_user=with_user)

    def test_file_marker_uses_attachment_placeholder_before_decryption(
        self, facility, staff_user, doc_type_masking_matrix
    ):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json={_SLUG_ENC_OPEN: "vorher"},
        )
        create_entry = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)
        marker = {"__file__": True, "attachment_id": "00000000-0000-0000-0000-000000000001"}
        entry = EventHistory.objects.create(
            event=event,
            changed_by=staff_user,
            action=EventHistory.Action.CREATE,
            data_after={_SLUG_ENC_OPEN: marker},
            field_metadata=create_entry.field_metadata,
        )

        result = compute_diff(entry, user=staff_user)

        assert result["fields"] == [{"label": "Enc Open", "value": "[Anhang]"}]

    def test_corrupt_encrypted_token_stays_masked(self, facility, staff_user, doc_type_masking_matrix):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json={_SLUG_ENC_OPEN: "vorher"},
        )
        create_entry = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)
        entry = EventHistory.objects.create(
            event=event,
            changed_by=staff_user,
            action=EventHistory.Action.CREATE,
            data_after={_SLUG_ENC_OPEN: {"__encrypted__": True, "value": "kaputt"}},
            field_metadata=create_entry.field_metadata,
        )

        result = compute_diff(entry, user=staff_user)

        assert result["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER

    def test_missing_encryption_key_propagates(self, facility, staff_user, doc_type_masking_matrix):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json={_SLUG_ENC_OPEN: "vorher"},
        )
        entry = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)

        with (
            override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=""),
            pytest.raises(EncryptionKeyMissing),
        ):
            compute_diff(entry, user=staff_user)

    def test_unchanged_permitted_encrypted_value_is_not_a_diff(self, facility, doc_type_masking_matrix):
        from core.models.user import User

        lead_user = User.objects.create_user(
            username="testlead_hist_unchanged",
            role=User.Role.LEAD,
            facility=facility,
        )
        event = create_event(
            facility=facility,
            user=lead_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json=dict(_RAW_VALUES),
        )
        update_event(event, lead_user, dict(_RAW_VALUES))
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()

        result = compute_diff(entry, user=lead_user)

        assert result["fields"] == []

    def test_changed_permitted_encrypted_value_decrypts_each_side_once(
        self, facility, staff_user, doc_type_masking_matrix, monkeypatch
    ):
        from core.services.events import history_diff

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_masking_matrix,
            occurred_at=timezone.now(),
            data_json={_SLUG_ENC_OPEN: "Alt"},
        )
        update_event(event, staff_user, {_SLUG_ENC_OPEN: "Neu"})
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        original_safe_decrypt = history_diff.safe_decrypt
        calls = 0

        def counting_safe_decrypt(value, default):
            nonlocal calls
            calls += 1
            return original_safe_decrypt(value, default)

        monkeypatch.setattr(history_diff, "safe_decrypt", counting_safe_decrypt)

        result = compute_diff(entry, user=staff_user)

        assert result["fields"][0]["old_value"] == "Alt"
        assert result["fields"][0]["new_value"] == "Neu"
        assert calls == 2
