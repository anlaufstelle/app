"""Unit-Tests fuer min_contact_stage choices auf DocumentType."""

import pytest
from django.core.exceptions import ValidationError

from core.constants import CONTACT_STAGE_CHOICES
from core.models import DocumentType


class TestMinContactStageChoices:
    """Prueft, dass min_contact_stage ein CharField mit choices ist."""

    def test_field_has_choices(self):
        """Das Feld min_contact_stage muss choices definiert haben."""
        field = DocumentType._meta.get_field("min_contact_stage")
        assert field.choices is not None, "min_contact_stage hat keine choices"
        assert len(field.choices) > 0, "min_contact_stage choices sind leer"

    def test_choices_match_contact_stage_constants(self):
        """Die choices muessen mit CONTACT_STAGE_CHOICES uebereinstimmen."""
        field = DocumentType._meta.get_field("min_contact_stage")
        assert list(field.choices) == list(CONTACT_STAGE_CHOICES)

    def test_valid_value_accepted(self, facility):
        """Gueltige Werte werden akzeptiert."""
        dt = DocumentType(
            facility=facility,
            name="Test Valid",
            min_contact_stage="identified",
        )
        dt.full_clean()  # Sollte keine ValidationError werfen

    def test_invalid_value_rejected(self, facility):
        """Ungueltige Werte werden bei full_clean abgelehnt."""
        dt = DocumentType(
            facility=facility,
            name="Test Invalid",
            min_contact_stage="ungueltig",
        )
        with pytest.raises(ValidationError) as exc_info:
            dt.full_clean()
        assert "min_contact_stage" in exc_info.value.message_dict

    def test_blank_value_accepted(self, facility):
        """Leerer Wert ist erlaubt (blank=True)."""
        dt = DocumentType(
            facility=facility,
            name="Test Blank",
            min_contact_stage="",
        )
        dt.full_clean()  # Sollte keine ValidationError werfen

    def test_null_value_accepted(self, facility):
        """None ist erlaubt (null=True)."""
        dt = DocumentType(
            facility=facility,
            name="Test Null",
            min_contact_stage=None,
        )
        dt.full_clean()  # Sollte keine ValidationError werfen
