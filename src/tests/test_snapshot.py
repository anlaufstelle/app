"""Tests für das StatisticsSnapshot-Model und den Snapshot-Service."""

from datetime import datetime

import pytest
from django.utils import timezone

from core.models import DocumentType, Event, StatisticsSnapshot
from core.services.snapshot import (
    create_or_update_snapshot,
    ensure_snapshots_for_months,
)


def _create_event(facility, user, doc_type, client=None, is_anonymous=False, occurred_at=None):
    """Hilfsfunktion zum Erstellen von Events."""
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=occurred_at or timezone.now(),
        data_json={"dauer": 15},
        is_anonymous=is_anonymous,
        created_by=user,
    )


@pytest.mark.django_db
class TestCreateSnapshotCorrectData:
    """create_or_update_snapshot speichert korrekte Statistikdaten."""

    def test_create_snapshot_correct_data(
        self, facility, staff_user, client_identified, client_qualified, doc_type_contact
    ):
        jan = timezone.make_aware(datetime(2025, 1, 15, 10, 0))

        # 2 Events mit identifiziertem Klientel, 1 mit qualifiziertem
        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=jan)
        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=jan)
        _create_event(facility, staff_user, doc_type_contact, client=client_qualified, occurred_at=jan)

        create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        data = snap.data

        assert data["total_contacts"] == 3
        assert data["by_contact_stage"]["identifiziert"] == 2
        assert data["by_contact_stage"]["qualifiziert"] == 1
        assert data["by_contact_stage"]["anonym"] == 0
        assert data["unique_clients"] == 2

        # by_document_type muss Kontakt mit count=3 enthalten
        by_dt = {row["name"]: row["count"] for row in data["by_document_type"]}
        assert by_dt["Kontakt"] == 3


@pytest.mark.django_db
class TestUpsertUpdatesExisting:
    """Erneuter Snapshot-Aufruf aktualisiert bestehenden Snapshot."""

    def test_upsert_updates_existing(self, facility, staff_user, client_identified, doc_type_contact):
        jan = timezone.make_aware(datetime(2025, 1, 15, 10, 0))

        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=jan)
        create_or_update_snapshot(facility, 2025, 1)

        snap1 = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap1.data["total_contacts"] == 1
        original_updated = snap1.updated_at

        # Weiteres Event hinzufuegen und Snapshot erneut erstellen
        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=jan)
        create_or_update_snapshot(facility, 2025, 1)

        snap1.refresh_from_db()
        assert snap1.data["total_contacts"] == 2
        assert snap1.updated_at >= original_updated
        assert StatisticsSnapshot.objects.filter(facility=facility, year=2025, month=1).count() == 1


@pytest.mark.django_db
class TestUniqueConstraint:
    """Gleiche Facility/Year/Month ergibt Update statt Duplikat."""

    def test_unique_constraint(self, facility, staff_user, client_identified, doc_type_contact):
        jan = timezone.make_aware(datetime(2025, 1, 15, 10, 0))

        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=jan)
        create_or_update_snapshot(facility, 2025, 1)
        create_or_update_snapshot(facility, 2025, 1)
        create_or_update_snapshot(facility, 2025, 1)

        assert StatisticsSnapshot.objects.filter(facility=facility, year=2025, month=1).count() == 1


@pytest.mark.django_db
class TestEnsureSnapshotsForMonths:
    """ensure_snapshots_for_months erstellt Snapshots fuer alle betroffenen Monate."""

    def test_ensure_snapshots_for_months(self, facility, staff_user, client_identified, doc_type_contact):
        # Events in 3 verschiedenen vergangenen Monaten
        jan = timezone.make_aware(datetime(2025, 1, 15, 10, 0))
        feb = timezone.make_aware(datetime(2025, 2, 10, 14, 0))
        mar = timezone.make_aware(datetime(2025, 3, 20, 9, 0))

        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=jan)
        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=feb)
        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=mar)

        events = Event.objects.filter(facility=facility)
        ensure_snapshots_for_months(facility, events)

        snapshots = StatisticsSnapshot.objects.filter(facility=facility)
        assert snapshots.count() == 3

        # Alle drei Monate muessen vorhanden sein
        months = set(snapshots.values_list("year", "month"))
        assert (2025, 1) in months
        assert (2025, 2) in months
        assert (2025, 3) in months


@pytest.mark.django_db
class TestJugendamtDataStored:
    """Jugendamt-Daten werden korrekt im Snapshot gespeichert."""

    def test_jugendamt_data_stored(self, facility, staff_user, client_identified):
        # DocumentType mit system_type fuer Jugendamt-Mapping
        dt_contact = DocumentType.objects.create(
            facility=facility,
            name="Kontaktaufnahme",
            category=DocumentType.Category.CONTACT,
            system_type="contact",
        )

        jan = timezone.make_aware(datetime(2025, 1, 15, 10, 0))
        _create_event(facility, staff_user, dt_contact, client=client_identified, occurred_at=jan)
        _create_event(facility, staff_user, dt_contact, client=client_identified, occurred_at=jan)

        create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        jg = snap.jugendamt_data

        assert jg["total"] == 2
        assert jg["unique_clients"] == 1
        # by_category muss "Kontakte" mit count=2 enthalten
        category_dict = {entry[0]: entry[1] for entry in jg["by_category"]}
        assert category_dict["Kontakte"] == 2


@pytest.mark.django_db
class TestTopClientsExcluded:
    """top_clients darf NICHT im Snapshot gespeichert werden."""

    def test_top_clients_excluded(self, facility, staff_user, client_identified, doc_type_contact):
        jan = timezone.make_aware(datetime(2025, 1, 15, 10, 0))
        _create_event(facility, staff_user, doc_type_contact, client=client_identified, occurred_at=jan)

        create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert "top_clients" not in snap.data
