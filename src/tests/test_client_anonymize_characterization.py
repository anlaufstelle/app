"""RF-T02: Charakterisierungstests fuer ``Client.anonymize`` (Refs #776).

Sicherheitsnetz fuer Sprint 2-Refactorings: dokumentieren das aktuelle
Verhalten von :func:`core.services.clients.anonymize_client`. Wenn ein
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
from core.services.clients import anonymize_client


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

    def test_trigger_bypass_invoked_for_event_history_redaction(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        from core.services.event import create_event

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
        from core.services._db_admin import bypass_replication_triggers as real_bypass

        @contextmanager
        def _spy():
            called.append("entered")
            with real_bypass():
                yield
            called.append("exited")

        with patch("core.services.clients.bypass_replication_triggers", _spy):
            anonymize_client(client_identified, user=staff_user)

        # Snapshot: der Bypass-Block wurde genau einmal betreten und sauber
        # verlassen — der append-only-Trigger der EventHistory-Tabelle wird
        # durch ``SET LOCAL session_replication_role = replica`` umgangen.
        assert called == ["entered", "exited"]
