"""Charakterisierungstests fuer ``Client.anonymize`` (Refs #776).

Sicherheitsnetz fuer Refactorings: dokumentieren das aktuelle
Verhalten von :func:`core.services.client.main.anonymize_client`. Wenn ein
Refactoring eines dieser Tests umstoesst, ist das ein Verhaltensbruch
(nicht ein Refactoring im engeren Sinne).

Drei Charakterisierungs-Cases plus Trigger-State-Snapshot:

1. Plain — Client ohne Events/Cases/Workitems; nur Klient-Felder werden
   redigiert.
2. With attachments — Client mit Event + EventAttachment auf Disk; die
   Disk-Datei wird geloescht (RF-005-Vermeidung).
3. With deletion request — Client mit DeletionRequest fuer Event-Target;
   ``DeletionRequest.reason`` wird redigiert, die Antrags-Meta bleibt.
4. Trigger-State — ``bypass_replication_triggers`` wird waehrend der
   ``EventHistory``-Redaktion einmal angefasst (Snapshot).
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import (
    Client,
    DeletionRequest,
    Event,
    EventHistory,
)
from core.services.client import anonymize_client


@pytest.mark.django_db
class TestClientAnonymizeCharacterization:
    def test_plain_client_redacts_master_fields(self, facility, staff_user):
        client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Stern-7",
            notes="sehr persoenliche Notiz",
            age_cluster=Client.AgeCluster.AGE_27_PLUS,
            created_by=staff_user,
        )

        anonymize_client(client, user=staff_user)

        client.refresh_from_db()
        assert client.pseudonym.startswith("Gelöscht-")
        assert client.notes == ""
        assert client.age_cluster == Client.AgeCluster.UNKNOWN
        assert client.is_active is False

    def test_client_with_attachments_deletes_disk_files(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "x"},
            created_by=staff_user,
        )
        # ``delete_event_attachments`` wird in ``anonymize_client`` per
        # function-local Import gezogen; daher am Quellmodul patchen.
        with patch("core.services.file_vault.delete_event_attachments") as del_files:
            anonymize_client(client_identified, user=staff_user)

        # Charakterisierung: pro betroffenem Event genau ein Aufruf an
        # ``delete_event_attachments`` — der Service entfernt die Disk-Dateien.
        del_files.assert_called_once()
        assert del_files.call_args.args[0].pk == event.pk

    def test_client_with_deletion_request_redacts_reason(
        self, facility, staff_user, lead_user, doc_type_contact, client_identified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=str(event.pk),
            reason="enthaelt unautorisierte Klarnamen",
            requested_by=staff_user,
        )

        anonymize_client(client_identified, user=lead_user)

        dr.refresh_from_db()
        # Antrags-Meta bleibt fuer den 4-Augen-Audit-Trail erhalten ...
        assert dr.requested_by_id == staff_user.pk
        assert str(dr.target_id) == str(event.pk)
        # ... aber der freie Reason-Text ist redigiert.
        assert dr.reason == "[Anonymisiert]"

    def test_activity_summaries_redacted_for_client_and_event_targets(self, facility, staff_user, doc_type_contact):
        """Refs #1067: Zeitstrom darf das alte Pseudonym nicht überleben.

        ``create_client`` („Person Stern-9 angelegt") und ``create_event``
        („Kontakt für Stern-9") schreiben das Klartext-Pseudonym in
        ``Activity.summary`` — beide Spuren müssen nach der Anonymisierung
        redigiert sein.
        """
        from core.models.activity import Activity
        from core.services.client import create_client
        from core.services.events import create_event

        client = create_client(
            facility,
            staff_user,
            pseudonym="Stern-9",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "x"},
            client=client,
        )
        assert Activity.objects.filter(facility=facility, summary__contains="Stern-9").count() == 2

        anonymize_client(client, user=staff_user)

        assert not Activity.objects.filter(facility=facility, summary__contains="Stern-9").exists()

    def test_trigger_bypass_invoked_for_event_history_redaction(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        from core.services.events import create_event

        # ``create_event`` legt eine EventHistory-Zeile (CREATE) an, die der
        # append-only-Trigger spaeter beim UPDATE verteidigen wuerde —
        # genau die Situation, fuer die ``bypass_replication_triggers``
        # gebaut wurde.
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "x"},
            client=client_identified,
        )
        assert EventHistory.objects.filter(event__client=client_identified).exists()

        called = []
        from core.services.system import bypass_replication_triggers as real_bypass

        @contextmanager
        def _spy():
            called.append("entered")
            with real_bypass():
                yield
            called.append("exited")

        with patch("core.services.client.main.bypass_replication_triggers", _spy):
            anonymize_client(client_identified, user=staff_user)

        # Snapshot: der Bypass-Block wurde genau einmal betreten und sauber
        # verlassen — der append-only-Trigger der EventHistory-Tabelle wird
        # durch ``SET LOCAL session_replication_role = replica`` umgangen.
        assert called == ["entered", "exited"]


@pytest.mark.django_db
class TestAnonymizeClientHelpers:
    """Refs #905: isolierte Tests für die internen ``_redact_*``-Helper.

    Die Public API ``anonymize_client`` ruft die sieben Helper sequentiell;
    diese Tests prüfen jeden Schritt einzeln gegen seine Verantwortung,
    damit zukünftige Aenderungen einen klaren Bruchpunkt haben.
    """

    def test_redact_client_identity_only_touches_client_fields(self, facility, staff_user):
        from core.services.client import _redact_client_identity

        client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Original-1",
            notes="Geheimnotiz",
            age_cluster=Client.AgeCluster.AGE_27_PLUS,
            created_by=staff_user,
        )

        _redact_client_identity(client)
        client.refresh_from_db()

        assert client.pseudonym.startswith("Gelöscht-")
        assert client.notes == ""
        assert client.age_cluster == Client.AgeCluster.UNKNOWN
        assert client.is_active is False
        # contact_stage bleibt unangetastet — gehört nicht zur Identity-Redaktion.
        assert client.contact_stage == Client.ContactStage.IDENTIFIED

    def test_redact_cases_and_episodes_returns_case_ids(self, facility, staff_user, client_identified):
        from core.models.case import Case
        from core.models.episode import Episode
        from core.services.client import _redact_cases_and_episodes

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Klartext-Titel",
            description="Klartext-Beschreibung",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        episode = Episode.objects.create(
            case=case,
            title="Klartext-Episode",
            description="Klartext-Beschreibung-Episode",
            started_at=timezone.now().date(),
            created_by=staff_user,
        )

        case_ids = _redact_cases_and_episodes(client_identified)
        case.refresh_from_db()
        episode.refresh_from_db()

        assert case.pk in case_ids
        assert case.title.startswith("[Anonymisiert ")
        assert case.description == ""
        assert episode.title == "Episode (anonymisiert)"
        assert episode.description == ""

    def test_redact_cases_returns_empty_list_when_no_cases(self, client_identified):
        from core.services.client import _redact_cases_and_episodes

        assert _redact_cases_and_episodes(client_identified) == []

    def test_redact_workitems_anonymizes_all_states(self, facility, staff_user, client_identified):
        from core.models import WorkItem
        from core.services.client import _redact_workitems

        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Offene Aufgabe",
            description="Klartext",
            created_by=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.DONE,
            title="Erledigte Aufgabe",
            description="Klartext",
            created_by=staff_user,
        )

        _redact_workitems(client_identified)

        for wi in client_identified.work_items.all():
            assert wi.title == "Aufgabe (anonymisiert)"
            assert wi.description == ""

    def test_delete_event_attachments_for_client_returns_event_ids(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        from core.services.client import _delete_event_attachments_for_client

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
            created_by=staff_user,
        )
        with patch("core.services.file_vault.delete_event_attachments") as del_files:
            event_ids = _delete_event_attachments_for_client(client_identified)

        assert event.pk in event_ids
        del_files.assert_called_once()

    def test_delete_event_attachments_for_client_returns_empty_when_no_events(self, client_identified):
        from core.services.client import _delete_event_attachments_for_client

        assert _delete_event_attachments_for_client(client_identified) == []

    def test_redact_event_history_is_no_op_without_event_ids(self):
        from core.services.client import _redact_event_history

        # Kein Event → kein Bypass-Trigger, kein UPDATE.
        with patch("core.services.client.main.bypass_replication_triggers") as bypass:
            _redact_event_history([])
        bypass.assert_not_called()

    def test_redact_deletion_requests_only_touches_event_targets(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        from core.services.client import _redact_deletion_requests

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=str(event.pk),
            reason="Klartext-Begruendung",
            requested_by=staff_user,
        )

        _redact_deletion_requests([event.pk])
        dr.refresh_from_db()
        assert dr.reason == "[Anonymisiert]"
        # Metadaten bleiben unangetastet — Vier-Augen-Audit-Trail.
        assert dr.requested_by_id == staff_user.pk
        assert dr.status == DeletionRequest.Status.PENDING

    def test_redact_live_events_clears_data_json_and_search_text(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """Refs #1089: Live-Events des Klienten werden PII-frei, bleiben aber
        als Statistik-Aggregat erhalten (data_json -> {}, search_text -> "")."""
        from core.services.client import _redact_live_events

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Klartext-Krise"},
            created_by=staff_user,
        )
        # search_text deterministisch befuellen, ohne Slug-Mechanik: ``.update()``
        # umgeht das pre_save-Signal, der Klartext steht roh in der Spalte.
        Event.objects.filter(pk=event.pk).update(search_text="Klartext-Krise")

        _redact_live_events(client_identified)

        event.refresh_from_db()
        assert event.data_json == {}
        assert event.search_text == ""
        # Event bleibt als Aggregat erhalten — nur die PII ist raus.
        assert event.is_deleted is False

    def test_redact_live_events_skips_soft_deleted(self, facility, staff_user, doc_type_contact, client_identified):
        """Bereits soft-deletete Events bleiben unangetastet (Refs #1089).

        Ihr ``search_text``-Leck im Retention-Soft-Delete ist ein eigener
        Befund (H5, #1092) — der Live-Event-Fix darf ihn nicht maskieren.
        """
        from core.services.client import _redact_live_events

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_deleted=True,
            created_by=staff_user,
        )
        Event.objects.filter(pk=event.pk).update(search_text="Restspur-Klartext")

        _redact_live_events(client_identified)

        event.refresh_from_db()
        assert event.search_text == "Restspur-Klartext"

    def test_redact_deletion_requests_is_no_op_without_event_ids(self):
        from core.services.client import _redact_deletion_requests

        # Defensive: kein Crash, kein UPDATE, wenn keine Events vorhanden.
        _redact_deletion_requests([])  # darf nicht raisen

    def test_redact_activities_only_touches_own_targets(self, facility, staff_user, doc_type_contact):
        """Refs #1067: Redaktion trifft nur Client-/Event-Activities DIESES Klienten."""
        from core.models.activity import Activity
        from core.services.client import _redact_activities, create_client
        from core.services.events import create_event

        own = create_client(facility, staff_user, pseudonym="Eigen-1")
        own_event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "x"},
            client=own,
        )
        create_client(facility, staff_user, pseudonym="Fremd-1")

        _redact_activities(own, [own_event.pk])

        assert not Activity.objects.filter(facility=facility, summary__contains="Eigen-1").exists()
        # Fremder Klient bleibt unangetastet.
        assert Activity.objects.filter(facility=facility, summary__contains="Fremd-1").exists()
