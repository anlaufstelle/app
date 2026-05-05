"""Validator-Tests für FieldTemplate (Audit-Massnahme #10, Refs #733).

Prueft, dass ``FieldTemplate.clean()`` HIGH-Sensitivity-Felder ohne
``is_encrypted`` blockiert, damit Art.-9-relevanter Klartext nicht
durch falsche Konfiguration in JSONB-Backups landet.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import DocumentType, FieldTemplate


@pytest.mark.django_db
class TestSensitivityHighRequiresEncryption:
    def test_high_sensitivity_without_encryption_raises(self, facility):
        ft = FieldTemplate(
            facility=facility,
            name="HighOhneEncrypt",
            field_type=FieldTemplate.FieldType.TEXT,
            slug="ft-validator-test",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=False,
        )
        with pytest.raises(ValidationError) as excinfo:
            ft.full_clean()
        assert "is_encrypted" in excinfo.value.error_dict

    def test_high_sensitivity_with_encryption_ok(self, facility):
        ft = FieldTemplate(
            facility=facility,
            name="HighMitEncrypt",
            field_type=FieldTemplate.FieldType.TEXT,
            slug="ft-validator-test",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        # Soll keine Exception werfen
        ft.full_clean()

    def test_elevated_without_encryption_ok(self, facility):
        # ELEVATED erzwingt keine Verschluesselung — Validator betrifft
        # nur HIGH.
        ft = FieldTemplate(
            facility=facility,
            name="ElevatedOhneEncrypt",
            field_type=FieldTemplate.FieldType.TEXT,
            slug="ft-validator-test",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            is_encrypted=False,
        )
        ft.full_clean()

    def test_normal_without_encryption_ok(self, facility):
        ft = FieldTemplate(
            facility=facility,
            name="NormalOhneEncrypt",
            field_type=FieldTemplate.FieldType.TEXT,
            slug="ft-validator-test",
            sensitivity=DocumentType.Sensitivity.NORMAL,
            is_encrypted=False,
        )
        ft.full_clean()

    def test_blank_sensitivity_without_encryption_ok(self, facility):
        # sensitivity="" (default, erbt vom DocumentType) blockt nicht —
        # Validator greift nur, wenn das Feld selbst auf HIGH steht.
        ft = FieldTemplate(
            facility=facility,
            name="LeereSensitivity",
            field_type=FieldTemplate.FieldType.TEXT,
            slug="ft-validator-test",
            sensitivity="",
            is_encrypted=False,
        )
        ft.full_clean()
