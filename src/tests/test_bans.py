"""Tests für Hausverbot-Logik."""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.bans import get_active_bans


@pytest.fixture
def doc_type_hausverbot(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        category=DocumentType.Category.ADMIN,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )
    ft_grund = FieldTemplate.objects.create(
        facility=facility,
        name="Grund",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_required=True,
    )
    ft_bis = FieldTemplate.objects.create(
        facility=facility,
        name="Bis",
        field_type=FieldTemplate.FieldType.DATE,
    )
    ft_aktiv = FieldTemplate.objects.create(
        facility=facility,
        name="Aktiv",
        field_type=FieldTemplate.FieldType.BOOLEAN,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_grund, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_bis, sort_order=1)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_aktiv, sort_order=2)
    return doc_type


@pytest.fixture
def active_ban_event(facility, client_identified, staff_user, doc_type_hausverbot):
    future_date = (date.today() + timedelta(days=30)).isoformat()
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"grund": "Bedrohung", "bis": future_date, "aktiv": True},
        created_by=staff_user,
    )


@pytest.fixture
def expired_ban_event(facility, client_identified, staff_user, doc_type_hausverbot):
    past_date = (date.today() - timedelta(days=1)).isoformat()
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"grund": "Vandalismus", "bis": past_date, "aktiv": True},
        created_by=staff_user,
    )


@pytest.fixture
def inactive_ban_event(facility, client_identified, staff_user, doc_type_hausverbot):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"grund": "Aufgehoben", "bis": "", "aktiv": False},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestBanService:
    def test_active_ban_detected(self, facility, active_ban_event):
        bans = get_active_bans(facility)
        assert len(bans) == 1
        assert bans[0]["grund"] == "Bedrohung"
        assert bans[0]["client"] == active_ban_event.client

    def test_expired_ban_not_included(self, facility, expired_ban_event):
        bans = get_active_bans(facility)
        assert len(bans) == 0

    def test_inactive_ban_not_included(self, facility, inactive_ban_event):
        bans = get_active_bans(facility)
        assert len(bans) == 0

    def test_no_bans_returns_empty(self, facility):
        bans = get_active_bans(facility)
        assert bans == []

    def test_ban_without_bis_date_is_active(self, facility, client_identified, staff_user, doc_type_hausverbot):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_hausverbot,
            occurred_at=timezone.now(),
            data_json={"grund": "Unbefristet", "bis": "", "aktiv": True},
            created_by=staff_user,
        )
        bans = get_active_bans(facility)
        assert len(bans) == 1
        assert bans[0]["grund"] == "Unbefristet"

    def test_deleted_event_not_included(self, facility, client_identified, staff_user, doc_type_hausverbot):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_hausverbot,
            occurred_at=timezone.now(),
            data_json={"grund": "Gelöscht", "bis": "", "aktiv": True},
            created_by=staff_user,
            is_deleted=True,
        )
        bans = get_active_bans(facility)
        assert len(bans) == 0


@pytest.mark.django_db
class TestBanBannerInAktivitaetslog:
    def test_ban_banner_visible(self, client, staff_user, active_ban_event):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert "Hausverbot" in content
        assert "Bedrohung" in content

    def test_no_banner_without_bans(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert "Hausverbot:" not in content
