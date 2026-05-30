"""Mutation-Followup-Tests für ``core.services.client_export`` — Serializer.

Refs Welle 7 (#930). Sub-File aus ``test_mutation_followup_client_export``;
enthält die Test-Klasse ``TestSerializeEvent`` (Event-pro-Eintrag-
Serialisierung inkl. Decryption-Branches).
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import (
    DocumentType,
    Event,
)
from core.services.client_export import (
    _serialize_event,
)
from core.services.file_vault import encrypt_field
from tests._mutation_followup_client_export_helpers import (
    _make_doc_type,
    _make_event,
)

# ---------------------------------------------------------------------------
# _serialize_event — pro-Event-Serialisierung inkl. Decryption
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeEvent:
    """Refs Welle 7 — ``_serialize_event`` (Line 24).

    Branches:
    - ``if event.data_json:`` (truthy)
    - ``isinstance(value, dict)`` → safe_decrypt
    - else-Branch → 1:1 passthrough
    - ``event.created_by`` ``None``-Fallback
    """

    def test_contains_all_four_keys(self, sample_event):
        result = _serialize_event(sample_event)
        assert set(result.keys()) == {
            "document_type",
            "occurred_at",
            "created_by",
            "data",
        }

    def test_document_type_uses_name_not_pk(self, sample_event):
        """Mutation ``event.document_type.name`` → ``event.document_type_id``
        würde eine UUID statt einem lesbaren Namen liefern."""
        result = _serialize_event(sample_event)
        assert result["document_type"] == sample_event.document_type.name
        assert result["document_type"] == "Kontakt"

    def test_occurred_at_is_isoformat(self, sample_event):
        result = _serialize_event(sample_event)
        assert isinstance(result["occurred_at"], str)
        assert result["occurred_at"] == sample_event.occurred_at.isoformat()
        assert "T" in result["occurred_at"]

    def test_created_by_username(self, sample_event, staff_user):
        result = _serialize_event(sample_event)
        assert result["created_by"] == staff_user.username

    def test_created_by_none_when_user_missing(self, facility, client_identified, doc_type_contact):
        """Mutation ``if event.created_by else None`` → fester Wert würde
        ``None``-Branch verändern."""
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=None,
        )
        result = _serialize_event(ev)
        assert result["created_by"] is None

    def test_empty_data_json_yields_empty_data_dict(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``if event.data_json:`` (Negation) würde non-empty
        Dicts überspringen oder leere durchwinken — hier prüfen wir den
        Falsy-Branch explizit."""
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user, data_json={})
        result = _serialize_event(ev)
        assert result["data"] == {}

    def test_plain_value_passes_through_untouched(self, facility, client_identified, doc_type_contact, staff_user):
        """Non-dict-Werte landen 1:1 in ``data`` — der ``isinstance(value, dict)``-
        Check ist die Boundary."""
        ev = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"dauer": 42, "notiz": "hello"},
        )
        result = _serialize_event(ev)
        assert result["data"]["dauer"] == 42
        assert result["data"]["notiz"] == "hello"

    def test_dict_value_without_encryption_marker_passes_through(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """``safe_decrypt`` liefert das Dict unverändert zurück, wenn kein
        ``__encrypted__``-Marker drin ist. So fangen wir den
        ``isinstance(value, dict)``-Branch, ohne Fernet-Keys aufsetzen
        zu müssen."""
        payload = {"some": "nested", "n": 1}
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": payload},
            created_by=staff_user,
        )
        result = _serialize_event(ev)
        assert result["data"]["notiz"] == payload

    def test_encrypted_dict_value_is_decrypted(self, facility, client_identified, staff_user):
        """Mutation ``safe_decrypt(value)`` → ``value`` würde den Ciphertext
        statt Klartext im Export liefern — kritisch für DSGVO Art. 15."""
        doc_type = _make_doc_type(facility, name="Krise", sensitivity=DocumentType.Sensitivity.HIGH)
        # Verschlüsselter Marker; safe_decrypt → Klartext
        encrypted_value = encrypt_field("geheimer-text")
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"krise-notiz": encrypted_value},
            created_by=staff_user,
        )
        result = _serialize_event(ev)
        assert result["data"]["krise-notiz"] == "geheimer-text"
