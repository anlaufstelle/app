"""Validator-Tests für FieldTemplate (Refs #733).

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

    def test_save_high_sensitivity_forces_encryption_bypassing_clean(self, facility):
        """Refs #1270 (T5): die HIGH⇒verschlüsselt-Invariante muss auch auf
        ``save()``-Ebene greifen — ``.create()``/Seed/Bulk umgehen
        ``clean()``/``full_clean()``, es gibt keinen DB-CHECK.

        Backstop analog zum bestehenden FILE-Feld-Zwang (``FieldTemplate.save()``
        setzt dort bereits ``is_encrypted=True``): ein HIGH-Feld kann danach
        nicht mehr mit ``is_encrypted=False`` persistiert werden — egal über
        welchen Code-Pfad —, damit kein Art.-9-Klartext ins JSONB/Backup gerät.
        """
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Diagnose (direkt gespeichert)",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=False,
        )
        ft.refresh_from_db()
        assert ft.is_encrypted is True

    def test_save_high_sensitivity_already_encrypted_unchanged(self, facility):
        """HIGH + bereits verschlüsselt bleibt unverändert speicherbar."""
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Diagnose verschlüsselt",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        ft.refresh_from_db()
        assert ft.is_encrypted is True
