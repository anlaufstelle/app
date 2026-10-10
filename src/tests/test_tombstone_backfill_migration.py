"""Altbestands-Backfill der Tombstone-Timeline (Cross-Review-Befund 2).

Die Generalisierung aus #2047
lief nur bei KUENFTIGEN Anonymisierungen. Bestehende Tombstones erreichen die
beiden Pfade nie wieder:

* Der nächtliche Hard-Pfad selektiert ``anonymized_at IS NULL`` (Refs #2013) —
  und Migration 0165 hat den Bestand bereits gestempelt.
* Die K-Anon-Bridge schliesst ``k_anonymized=True`` und ``Gelöscht-``-Pseudonyme
  ausdrücklich aus.

Der Altbestand behielt damit exakte Ereigniszeiten — und der Export wies ihn
gleichwohl über ``event_timestamps_generalized`` als generalisiert aus. Das war
eine sachlich falsche Aussage in einer Art.-15-Auskunft, und damit der
schwerere Teil des Befunds: eine fehlende Härtung ist eine Lücke, eine falsche
Zusicherung ist eine Falschauskunft.

Migration ``0171`` zieht beides nach. Diese Tests prüfen das Ergebnis über die
**gleiche Logik**, die die Migration anwendet, gegen im Test künstlich
„vergessene" Tombstones — ein echter ``migrate``-Lauf gegen die Testdatenbank
wäre hier weder isolierbar noch aussagekräftig (die Migration ist zum
Testzeitpunkt längst durch).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from core.models import Client, Event

BERLIN = ZoneInfo("Europe/Berlin")


def _aware(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def _run_backfill():
    """Run migration 0171 with its historical models against the fixture rows.

    The real migration predates the four-eyes downgrade guard (Refs #2524).
    Passing today's models would incorrectly apply that guard to this
    historical tombstone cleanup. Use Django's migration state instead of
    bypassing the guard or replacing the migration's implementation.
    The RunPython entry point retains its separate database-role check.
    """
    import importlib

    module = importlib.import_module("core.migrations.0171_generalize_existing_tombstones")
    apps = MigrationLoader(connection).project_state([("core", "0171_generalize_existing_tombstones")]).apps
    module.generalize_existing_tombstones(apps)


def _make_tombstone(facility, staff_user, doc_type, *, k_anon=False, stage=Client.ContactStage.QUALIFIED):
    """Ein Tombstone, wie ihn der Bestand VOR #2047 hinterlassen hat.

    Bewusst per ``objects.create`` + ``update`` gebaut statt über
    ``anonymize_client`` — der Service generalisiert seit #2047 selbst, und
    genau der ungenerelisierte Altzustand ist hier der Prüfgegenstand.
    """
    client = Client.objects.create(
        facility=facility,
        pseudonym=f"Gelöscht-alt-{timezone.now().timestamp()}",
        contact_stage=stage,
        age_cluster=Client.AgeCluster.AGE_18_26,
        is_active=False,
        created_by=staff_user,
    )
    Client.objects.filter(pk=client.pk).update(
        anonymized_at=None if k_anon else timezone.now(),
        k_anonymized=k_anon,
    )
    event = Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=_aware(2026, 5, 3, 14, 32),
        created_by=staff_user,
        is_deleted=True,
    )
    client.refresh_from_db()
    return client, event


@pytest.mark.django_db
class TestBackfillGeneralizesLegacyTombstones:
    def test_hard_tombstone_events_are_bucketed(self, facility, staff_user, doc_type_contact):
        _client, event = _make_tombstone(facility, staff_user, doc_type_contact)
        _run_backfill()
        local = timezone.localtime(Event.objects.get(pk=event.pk).occurred_at)
        assert (local.year, local.month, local.day) == (2026, 5, 1)
        assert (local.hour, local.minute, local.second) == (0, 0, 0)

    def test_hard_tombstone_contact_stage_is_reset(self, facility, staff_user, doc_type_contact):
        client, _event = _make_tombstone(facility, staff_user, doc_type_contact)
        _run_backfill()
        client.refresh_from_db()
        assert client.contact_stage == Client.ContactStage.IDENTIFIED

    def test_k_anon_tombstone_events_are_bucketed_too(self, facility, staff_user, doc_type_contact):
        """Auch der K-Anon-Altbestand — sonst bliebe der Export-Marker für ihn
        eine Falschaussage."""
        _client, event = _make_tombstone(facility, staff_user, doc_type_contact, k_anon=True)
        _run_backfill()
        local = timezone.localtime(Event.objects.get(pk=event.pk).occurred_at)
        assert (local.day, local.hour, local.minute) == (1, 0, 0)

    def test_k_anon_contact_stage_is_preserved(self, facility, staff_user, doc_type_contact):
        """Die k-Anonymität ruht auf ``(age_cluster, contact_stage)`` — der
        Backfill darf die Äquivalenzklasse dieses Pfads NICHT zerstören."""
        client, _event = _make_tombstone(facility, staff_user, doc_type_contact, k_anon=True)
        _run_backfill()
        client.refresh_from_db()
        assert client.contact_stage == Client.ContactStage.QUALIFIED
        assert client.age_cluster == Client.AgeCluster.AGE_18_26

    def test_living_clients_are_untouched(self, facility, client_identified, doc_type_contact, staff_user):
        """Der Backfill fasst ausschliesslich Tombstones an."""
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_aware(2026, 5, 3, 14, 32),
            created_by=staff_user,
        )
        before_stage = client_identified.contact_stage
        _run_backfill()
        assert Event.objects.get(pk=event.pk).occurred_at == _aware(2026, 5, 3, 14, 32)
        client_identified.refresh_from_db()
        assert client_identified.contact_stage == before_stage

    def test_is_idempotent(self, facility, staff_user, doc_type_contact):
        _client, event = _make_tombstone(facility, staff_user, doc_type_contact)
        _run_backfill()
        first = Event.objects.get(pk=event.pk).occurred_at
        _run_backfill()
        assert Event.objects.get(pk=event.pk).occurred_at == first


@pytest.mark.django_db
class TestExportMarkerTellsTheTruth:
    def test_marker_is_true_after_the_backfill(self, facility, staff_user, doc_type_contact, admin_user):
        from core.services.client import export_client_data

        client, _event = _make_tombstone(facility, staff_user, doc_type_contact)
        _run_backfill()
        client.refresh_from_db()
        data = export_client_data(client, facility, admin_user)
        assert data["client"]["event_timestamps_generalized"] is True

    def test_marker_is_absent_for_a_living_client(self, facility, client_identified, admin_user):
        from core.services.client import export_client_data

        data = export_client_data(client_identified, facility, admin_user)
        assert "event_timestamps_generalized" not in data["client"]


@pytest.mark.django_db
class TestChunking:
    """Die Chargierung muss die geschriebene Menge begrenzen, nicht nur die
    vorgelesenen Personen-IDs (Cross-Review-Runde 2).

    Die erste Fassung begrenzte ``_CHUNK`` auf die Client-IDs und sammelte
    danach **alle** Ereignisse dieser Personen in ein einziges ``bulk_update``.
    Der Test fährt die Schleife mit einer winzigen Chargengröße gegen mehr
    Zeilen, als eine Charge fasst: läuft sie nicht weiter (oder endlos), fällt
    das hier auf.
    """

    def test_more_rows_than_one_chunk_are_all_generalized(self, facility, staff_user, doc_type_contact, monkeypatch):
        import importlib

        module = importlib.import_module("core.migrations.0171_generalize_existing_tombstones")
        monkeypatch.setattr(module, "_CHUNK", 2)

        client, first = _make_tombstone(facility, staff_user, doc_type_contact)
        extra = [
            Event.objects.create(
                facility=facility,
                client=client,
                document_type=doc_type_contact,
                occurred_at=_aware(2026, month, 17, 9, 5),
                created_by=staff_user,
                is_deleted=True,
            )
            for month in (6, 7, 8, 9)
        ]

        _run_backfill()

        for event in [first, *extra]:
            local = timezone.localtime(Event.objects.get(pk=event.pk).occurred_at)
            assert (local.day, local.hour, local.minute) == (1, 0, 0)

    def test_more_clients_than_one_chunk_get_their_stage_reset(
        self, facility, staff_user, doc_type_contact, monkeypatch
    ):
        """Dieselbe Prüfung für die zweite Schleife (``contact_stage``)."""
        import importlib

        module = importlib.import_module("core.migrations.0171_generalize_existing_tombstones")
        monkeypatch.setattr(module, "_CHUNK", 2)

        clients = [_make_tombstone(facility, staff_user, doc_type_contact)[0] for _ in range(5)]
        _run_backfill()
        for client in clients:
            client.refresh_from_db()
            assert client.contact_stage == Client.ContactStage.IDENTIFIED
