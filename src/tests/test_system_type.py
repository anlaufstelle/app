"""Tests für DocumentType.system_type — Immutabilität, Rename-Safety, None-Verhalten."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.bans import get_active_bans
from core.services.export import get_jugendamt_statistics


@pytest.fixture
def doc_type_ban(facility):
    """DocumentType mit system_type=BAN."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        category=DocumentType.Category.ADMIN,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )
    ft_grund = FieldTemplate.objects.create(
        facility=facility, name="Grund_st", field_type=FieldTemplate.FieldType.TEXTAREA, is_required=True
    )
    ft_bis = FieldTemplate.objects.create(facility=facility, name="Bis_st", field_type=FieldTemplate.FieldType.DATE)
    ft_aktiv = FieldTemplate.objects.create(
        facility=facility, name="Aktiv_st", field_type=FieldTemplate.FieldType.BOOLEAN
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_grund, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_bis, sort_order=1)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_aktiv, sort_order=2)
    return doc_type


@pytest.fixture
def doc_type_contact(facility):
    """DocumentType mit system_type=CONTACT."""
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt_st",
        category=DocumentType.Category.CONTACT,
        system_type=DocumentType.SystemType.CONTACT,
    )


@pytest.mark.django_db
class TestSystemTypeImmutability:
    def test_system_type_immutable_after_creation(self, doc_type_ban):
        """system_type kann nach Erstellung nicht geändert werden."""
        doc_type_ban.system_type = "contact"
        with pytest.raises(ValidationError, match="system_type kann nach Erstellung nicht geändert werden"):
            doc_type_ban.save()

    def test_system_type_none_stays_mutable(self, facility):
        """system_type=None darf gesetzt werden (erstmaliges Zuweisen)."""
        dt = DocumentType.objects.create(
            facility=facility,
            name="Benutzerdefiniert",
            category=DocumentType.Category.NOTE,
            system_type=None,
        )
        dt.system_type = "note"
        dt.save()  # Kein Fehler
        dt.refresh_from_db()
        assert dt.system_type == "note"

    def test_system_type_same_value_no_error(self, doc_type_ban):
        """Gleiches system_type nochmal setzen ist erlaubt (kein false positive)."""
        doc_type_ban.system_type = "ban"
        doc_type_ban.save()  # Kein Fehler


@pytest.mark.django_db
class TestRenameSafety:
    def test_rename_doctype_bans_still_work(self, facility, client_identified, staff_user, doc_type_ban):
        """DocumentType.name ändern → Bans funktionieren weiterhin über system_type."""
        future_date = (date.today() + timedelta(days=30)).isoformat()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_ban,
            occurred_at=timezone.now(),
            data_json={"grund": "Bedrohung", "bis": future_date, "aktiv": True},
            created_by=staff_user,
        )

        # Name ändern
        doc_type_ban.name = "Platzverweis"
        doc_type_ban.save()

        # Bans werden weiterhin über system_type gefunden
        bans = get_active_bans(facility)
        assert len(bans) == 1

    def test_rename_doctype_export_still_works(self, facility, staff_user, doc_type_contact):
        """DocumentType.name ändern → Jugendamt-Export funktioniert weiterhin über system_type."""
        # Kontakt-Event erstellen
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )

        # Name ändern
        doc_type_contact.name = "Erstgespräch"
        doc_type_contact.save()

        # Export mappt weiterhin über system_type
        today = date.today()
        stats = get_jugendamt_statistics(facility, today - timedelta(days=1), today + timedelta(days=1))
        assert stats["total"] == 1
        assert any(name == "Kontakte" for name, _ in stats["by_category"])


@pytest.mark.django_db
class TestSystemTypeNone:
    def test_none_system_type_excluded_from_bans(self, facility):
        """DocumentType ohne system_type wird von Bans ignoriert."""
        bans = get_active_bans(facility)
        assert bans == []

    def test_none_system_type_excluded_from_export(self, facility, staff_user):
        """DocumentType ohne system_type wird vom Jugendamt-Export ausgeschlossen."""
        dt_custom = DocumentType.objects.create(
            facility=facility,
            name="Custom",
            category=DocumentType.Category.SERVICE,
            system_type=None,
        )
        Event.objects.create(
            facility=facility,
            document_type=dt_custom,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )

        today = date.today()
        stats = get_jugendamt_statistics(facility, today - timedelta(days=1), today + timedelta(days=1))
        assert stats["total"] == 0
