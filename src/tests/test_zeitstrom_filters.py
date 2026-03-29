"""Tests für Zeitstrom-Dokumentationstyp-Filter."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event


@pytest.mark.django_db
class TestZeitstromDocTypeFilter:
    """Zeitstrom filtert Events nach Dokumentationstyp."""

    def test_filter_by_doc_type(self, client, staff_user, facility, doc_type_contact, doc_type_crisis):
        """Nur Events des gewählten Dokumentationstyps werden angezeigt."""
        client.force_login(staff_user)
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            document_type=doc_type_crisis,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )

        today = timezone.localdate().isoformat()
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"doc_type": str(doc_type_contact.pk), "date": today},
        )
        assert response.status_code == 200
        feed_items = response.context["feed_items"]
        event_items = [i for i in feed_items if i["type"] == "event"]
        assert all(i["object"].document_type_id == doc_type_contact.pk for i in event_items)
        assert len(event_items) == 1

    def test_no_filter_returns_all(self, client, staff_user, facility, doc_type_contact, doc_type_crisis):
        """Ohne Filter werden alle Events angezeigt."""
        client.force_login(staff_user)
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            document_type=doc_type_crisis,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )

        today = timezone.localdate().isoformat()
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"date": today},
        )
        assert response.status_code == 200
        feed_items = response.context["feed_items"]
        event_items = [i for i in feed_items if i["type"] == "event"]
        assert len(event_items) == 2

    def test_zeitstrom_has_document_types(self, client, staff_user, facility, doc_type_contact):
        """Zeitstrom-Seite enthält Dokumentationstypen im Kontext."""
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        assert "document_types" in response.context
