"""Charakterisierungstests fuer ``Client.anonymize`` (Refs #776).

Sicherheitsnetz fuer Refactorings: dokumentieren das aktuelle
Verhalten von :func:`core.services.client.main.anonymize_client`. Wenn ein
Refactoring eines dieser Tests umstoesst, ist das ein Verhaltensbruch
(nicht ein Refactoring im engeren Sinne).

Drei Charakterisierungs-Cases plus Trigger-State-Snapshot:

1. Plain — Client ohne Events/Cases/Workitems; nur Klient-Felder werden
   redigiert.
2. With attachments — Client mit Event + Attachment auf Disk; die
   Disk-Datei wird geloescht (RF-005-Vermeidung).
3. With deletion request — Client mit DeletionRequest fuer Event-Target;
   ``DeletionRequest.reason`` wird redigiert, die Antrags-Meta bleibt.
4. Trigger-State — ``bypass_replication_triggers`` wird waehrend der
   ``EventHistory``-Redaktion einmal angefasst (Snapshot).
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
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
        # ``delete_attachments_for`` wird in ``anonymize_client`` per
        # function-local Import gezogen; daher am Quellmodul patchen.
        with patch("core.services.file_vault.delete_attachments_for") as del_files:
            anonymize_client(client_identified, user=staff_user)

        # Charakterisierung: ein Aufruf je Anhang-Owner — der Service entfernt
        # die Disk-Dateien. Anforderung geaendert mit M24 (Refs #1738,
        # ADR-039 §7): vorher pinnte dieser Test GENAU EINEN Aufruf (nur das
        # Ereignis). Seit dem Owner-Umbau traegt auch die Person selbst
        # Anhaenge, also kommt ihr Aufruf hinzu; Faelle hat diese Fixture
        # keine, deshalb bleibt es bei zwei.
        deleted_pks = [call.args[0].pk for call in del_files.call_args_list]
        assert deleted_pks == [event.pk, client_identified.pk]

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
        # transaktional per Owner-``ALTER TABLE ... DISABLE/ENABLE TRIGGER``
        # umgangen (A1.4, Refs #1026).
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

    def test_event_ids_for_client_collects_the_event_ids(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """Refs #1782: das Einsammeln der ``event_ids`` ist ein eigener,
        rein lesender Helfer geworden — bis dahin fielen sie als Nebenprodukt
        von ``_delete_event_attachments_for_client`` an und zwangen dessen
        Datei-Loeschung damit an den Anfang der Kaskade."""
        from core.services.client import _event_ids_for_client

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
            created_by=staff_user,
        )

        assert event.pk in _event_ids_for_client(client_identified)

    def test_event_ids_for_client_returns_empty_when_no_events(self, client_identified):
        from core.services.client import _event_ids_for_client

        assert _event_ids_for_client(client_identified) == []

    def test_delete_event_attachments_for_client_deletes_per_given_event(
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
        with patch("core.services.file_vault.delete_attachments_for") as del_files:
            _delete_event_attachments_for_client(client_identified, [event.pk])

        del_files.assert_called_once()
        assert del_files.call_args.args[0].pk == event.pk

    def test_delete_event_attachments_for_client_is_a_no_op_without_event_ids(self, client_identified):
        from core.services.client import _delete_event_attachments_for_client

        with patch("core.services.file_vault.delete_attachments_for") as del_files:
            assert _delete_event_attachments_for_client(client_identified, []) is None
        del_files.assert_not_called()

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

    def test_redact_client_deletion_request_redacts_client_target_reason(self, facility, staff_user, client_identified):
        """Refs #1091: Der Client-Target-Loeschantrag wird redigiert.

        Der Vier-Augen-Antrag aus ``request_client_deletion`` hat
        ``target_type="Client"`` und wird vom Event-only-Helper
        ``_redact_deletion_requests`` nicht erfasst — sein Freitext
        ``reason`` bliebe sonst als PII-Residue stehen.
        """
        from core.services.client import _redact_client_deletion_request

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Klartext-Begruendung mit Klarnamen",
            requested_by=staff_user,
        )

        _redact_client_deletion_request(client_identified)
        dr.refresh_from_db()
        assert dr.reason == "[Anonymisiert]"
        # Antrags-Meta bleibt fuer den Vier-Augen-Audit-Trail erhalten.
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

    def test_redact_activities_clears_workitem_target_summary(self, facility, staff_user):
        """Refs #1090: WorkItem-Target-Activity mit PII-Titel wird redigiert.

        ``create_workitem`` schreibt eine Activity „Aufgabe: <titel>" mit
        Target = WorkItem; der Titel kann das Klienten-Pseudonym tragen.
        ``_redact_activities`` muss sie auch ohne Event-IDs erfassen.
        """
        from core.models.activity import Activity
        from core.services.case import create_workitem
        from core.services.client import _redact_activities, create_client

        client = create_client(facility, staff_user, pseudonym="Aufgaben-PII-1")
        create_workitem(facility, staff_user, client=client, title="Aufgaben-PII-1")

        # Positiv-Kontrolle: Activity traegt den PII-Titel.
        assert Activity.objects.filter(facility=facility, summary__contains="Aufgaben-PII-1").exists()

        _redact_activities(client, [])

        assert not Activity.objects.filter(facility=facility, summary__contains="Aufgaben-PII-1").exists()
        # WorkItem-Target-Activity ist auf den Anonym-Marker gesetzt.
        assert Activity.objects.filter(
            facility=facility,
            verb=Activity.Verb.CREATED,
            summary="[Anonymisiert]",
        ).exists()

    def test_redact_case_goals_clears_titles_but_keeps_progress(self, facility, staff_user, client_identified):
        """Refs #1751 (Punkt 1): Wirkungsziel-/Meilenstein-Freitext wird getilgt.

        Erhalten bleiben die Fortschritts-Marker (``is_achieved``,
        ``is_completed`` samt Datum) — sie sind anonyme Kennzahlen und tragen
        keinen Personenbezug, waehrend Titel/Beschreibung genau das tun.
        """
        from core.models.case import Case
        from core.models.outcome import Milestone, OutcomeGoal
        from core.services.client import _redact_case_goals, _redact_cases_and_episodes

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Klartext-Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        goal = OutcomeGoal.objects.create(
            case=case,
            title="Wohnung fuer Klartext-Person",
            description="Klartext-Zielbeschreibung",
            is_achieved=True,
            achieved_at=timezone.now().date(),
            created_by=staff_user,
        )
        milestone = Milestone.objects.create(
            goal=goal,
            title="Termin bei Klartext-Amt",
            is_completed=True,
            completed_at=timezone.now().date(),
        )

        _redact_case_goals(_redact_cases_and_episodes(client_identified))

        goal.refresh_from_db()
        milestone.refresh_from_db()
        assert goal.title == "Wirkungsziel (anonymisiert)"
        assert goal.description == ""
        assert milestone.title == "Meilenstein (anonymisiert)"
        # Fortschritt bleibt als anonyme Kennzahl erhalten.
        assert goal.is_achieved is True
        assert goal.achieved_at is not None
        assert milestone.is_completed is True
        assert milestone.completed_at is not None

    def test_redact_case_goals_is_no_op_without_cases(self):
        from core.services.client import _redact_case_goals

        assert _redact_case_goals([]) is None

    def test_redact_case_goals_leaves_other_clients_goals(self, facility, staff_user, client_identified):
        """Fremde Wirkungsziele derselben Einrichtung bleiben unangetastet."""
        from core.models.case import Case
        from core.models.outcome import OutcomeGoal
        from core.services.client import _redact_case_goals, _redact_cases_and_episodes, create_client

        other = create_client(facility, staff_user, pseudonym="Fremd-Ziel-1")
        other_case = Case.objects.create(
            facility=facility,
            client=other,
            title="Fremd-Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        other_goal = OutcomeGoal.objects.create(case=other_case, title="Fremd-Ziel", created_by=staff_user)

        _redact_case_goals(_redact_cases_and_episodes(client_identified))

        other_goal.refresh_from_db()
        assert other_goal.title == "Fremd-Ziel"

    def test_redact_activities_clears_case_target_summary(self, facility, staff_user, client_identified):
        """Refs #1751 (Punkt 2): Activities mit Target = Case werden mitredigiert.

        ``close_case`` schreibt „Fall ‚<titel>' geschlossen" mit Target = Case.
        Diese Zeilen fielen durch den alten Client-/Event-/WorkItem-Filter und
        ueberlebten die Anonymisierung bis zur ``retention_activities_days``-Frist.
        """
        from core.models.activity import Activity
        from core.models.case import Case
        from core.services.case import close_case
        from core.services.client import _redact_activities

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall-PII-Titel",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        close_case(case, staff_user)

        # Positiv-Kontrolle: die Case-Target-Activity traegt den Falltitel.
        assert Activity.objects.filter(facility=facility, summary__contains="Fall-PII-Titel").exists()

        _redact_activities(client_identified, [])

        assert not Activity.objects.filter(facility=facility, summary__contains="Fall-PII-Titel").exists()

    def test_redact_activities_leaves_foreign_case_activities(self, facility, staff_user, client_identified):
        """Gegenprobe: der Case-Zweig bleibt auf die Faelle DIESES Klienten begrenzt."""
        from core.models.activity import Activity
        from core.models.case import Case
        from core.services.case import close_case
        from core.services.client import _redact_activities, create_client

        other = create_client(facility, staff_user, pseudonym="Fremd-Fall-1")
        other_case = Case.objects.create(
            facility=facility,
            client=other,
            title="Fremd-Fall-Titel",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        close_case(other_case, staff_user)

        _redact_activities(client_identified, [])

        assert Activity.objects.filter(facility=facility, summary__contains="Fremd-Fall-Titel").exists()

    def test_redact_retention_proposals_keeps_evidence_metadata(self, facility, staff_user, doc_type_contact):
        """Refs #1751 (Punkt 3): nur das Pseudonym geht raus, der Nachweis bleibt.

        ``cleanup_stale_proposals`` fasst nur Vorschläge gelöschter Events an —
        ein über den Papierkorb-Ablauf anonymisierter Klient mit noch lebenden
        Events behielt sein Original-Pseudonym im ``details``-JSON.
        """
        from core.models import Client, RetentionProposal
        from core.retention.proposals import build_proposal_details, create_proposal
        from core.services.client import _redact_retention_proposals

        client = Client.objects.create(
            facility=facility,
            pseudonym="Vorschlag-PII-1",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        event = Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            created_by=staff_user,
        )
        proposal, _created = create_proposal(
            facility,
            RetentionProposal.TargetType.EVENT,
            event.pk,
            timezone.localdate(),
            build_proposal_details(event),
            "identified",
        )
        assert proposal.details["pseudonym"] == "Vorschlag-PII-1"  # Positiv-Kontrolle

        _redact_retention_proposals([event.pk])

        proposal.refresh_from_db()
        assert proposal.details["pseudonym"] == "[Anonymisiert]"
        # Nachweis-Metadaten unangetastet.
        assert proposal.details["occurred_at"]
        assert proposal.details["contact_stage"] == Client.ContactStage.IDENTIFIED
        assert proposal.status == RetentionProposal.Status.PENDING
        assert proposal.retention_category == "identified"

    def test_redact_retention_proposals_is_no_op_without_event_ids(self):
        from core.services.client import _redact_retention_proposals

        assert _redact_retention_proposals([]) is None

    def test_redact_legal_holds_clears_reason_but_keeps_trail(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """Refs #1751 (Punkt 4): Begründung raus, Nachweis-Metadaten bleiben.

        ``LegalHold`` kannte bisher weder Redaktions- noch Pruning-Pfad —
        aufgehobene Holds trugen ihre Klartext-Begründung für immer weiter.
        """
        from core.models import LegalHold
        from core.services.client import _redact_legal_holds

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            created_by=staff_user,
        )
        event_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren Klartext-Person",
            created_by=staff_user,
        )
        client_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=client_identified.pk,
            reason="Aufbewahrungspflicht Klartext-Person",
            created_by=staff_user,
        )

        _redact_legal_holds(client_identified, [event.pk])

        event_hold.refresh_from_db()
        client_hold.refresh_from_db()
        assert event_hold.reason == "[Anonymisiert]"
        assert client_hold.reason == "[Anonymisiert]"
        # Nachweis-Trail unangetastet: Ziel, Ersteller, Zeitstempel.
        assert event_hold.target_id == event.pk
        assert event_hold.created_by_id == staff_user.pk
        assert event_hold.created_at is not None

    def test_redact_legal_holds_leaves_foreign_targets(self, facility, staff_user, client_identified):
        """Gegenprobe: Holds fremder Ziele derselben Einrichtung bleiben stehen."""
        from core.models import Client, LegalHold
        from core.services.client import _redact_legal_holds

        other = Client.objects.create(facility=facility, pseudonym="Fremd-Hold-1")
        foreign_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=other.pk,
            reason="Fremde Begruendung",
            created_by=staff_user,
        )

        _redact_legal_holds(client_identified, [])

        foreign_hold.refresh_from_db()
        assert foreign_hold.reason == "Fremde Begruendung"


# ---------------------------------------------------------------------------
# Datei-Loeschungen am Transaktionsende (Refs #1782, #1344)
# ---------------------------------------------------------------------------

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _attach_real_file(facility, owner, user, name):
    """Echter Upload durch den Vault — DB-Zeile UND ``.enc`` auf der Platte."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.models import FieldTemplate
    from core.services.file_vault import store_encrypted_file

    ft = FieldTemplate.objects.create(facility=facility, name=name, field_type=FieldTemplate.FieldType.FILE)
    upload = SimpleUploadedFile(f"{name}.pdf", PDF_MINIMAL, content_type="application/pdf")
    return store_encrypted_file(facility, upload, ft, owner, user)


@pytest.fixture
def anonymize_file_fixture(facility, staff_user, doc_type_contact, client_identified):
    """Person mit Ereignis-, Personen- und Fall-eigenem Anhang (echte Dateien)."""
    from core.models import Case
    from core.services.file_vault import get_attachment_path

    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 5, "notiz": "x"},
        created_by=staff_user,
    )
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Datei-Fall",
        created_by=staff_user,
    )
    attachments = {
        "event": _attach_real_file(facility, event, staff_user, "Ereignisdatei"),
        "client": _attach_real_file(facility, client_identified, staff_user, "Personendatei"),
        "case": _attach_real_file(facility, case, staff_user, "Falldatei"),
    }
    paths = {key: get_attachment_path(att) for key, att in attachments.items()}
    assert all(path.exists() for path in paths.values()), "Setup: .enc-Dateien fehlen"
    return SimpleNamespace(event=event, case=case, client=client_identified, attachments=attachments, paths=paths)


@pytest.mark.django_db
class TestAnonymizeDeletesFilesAtTransactionEnd:
    """Refs #1782 (+#1344).

    ``_delete_event_attachments_for_client`` lief bis hierher FRUEH in der
    Kaskade — sein Rueckgabewert (``event_ids``) speiste die nachgelagerten
    Redaktionen. Ab diesem Punkt waren Ereignis-Dateien von der Platte entfernt,
    ihre DB-Zeilen aber noch in der offenen Transaktion: brach eine spaetere
    Redaktion ab, rollten die Zeilen zurueck und zeigten auf nicht mehr
    existierende Dateien (Download → 500 statt 404).

    Der ``.enc``-Unlink ist nicht rollback-bar. Die Kaskade sammelt die
    ``event_ids`` deshalb jetzt getrennt ein (``_event_ids_for_client``) und
    fuehrt ALLE physischen Loeschungen — DB-Zeilen wie Disk-Unlinks — gebuendelt
    am Transaktionsende aus.
    """

    def test_successful_run_removes_rows_and_files_for_every_owner(self, staff_user, anonymize_file_fixture):
        from core.models import Attachment

        anonymize_client(anonymize_file_fixture.client, user=staff_user)

        assert not Attachment.objects.filter(
            pk__in=[att.pk for att in anonymize_file_fixture.attachments.values()]
        ).exists()
        for owner_type, path in anonymize_file_fixture.paths.items():
            assert not path.exists(), f"{owner_type}: .enc-Datei ueberlebt die Anonymisierung"

    def test_abort_mid_cascade_leaves_rows_and_files_consistent(self, staff_user, anonymize_file_fixture):
        """Der Kern von #1782: bricht eine spaetere Redaktion ab, darf KEINE
        Datei fehlen, deren DB-Zeile zurueckgerollt wurde."""
        from core.models import Attachment

        boom = RuntimeError("Redaktion abgebrochen")
        with patch("core.services.client.main._redact_activities", side_effect=boom):
            with pytest.raises(RuntimeError, match="Redaktion abgebrochen"):
                anonymize_client(anonymize_file_fixture.client, user=staff_user)

        for owner_type, attachment in anonymize_file_fixture.attachments.items():
            assert Attachment.objects.filter(pk=attachment.pk).exists(), f"{owner_type}: Zeile fehlt nach Rollback"
            assert anonymize_file_fixture.paths[owner_type].exists(), (
                f"{owner_type}: .enc-Datei geloescht, obwohl die DB-Zeile zurueckgerollt wurde — "
                "die Zeile zeigt jetzt ins Leere (Download 500 statt 404)"
            )

    def test_event_ids_survive_the_split_and_still_feed_the_redactions(
        self, facility, staff_user, anonymize_file_fixture
    ):
        """Die Redaktionen haengen an denselben ``event_ids`` wie vorher — auch
        wenn die IDs jetzt getrennt vom Loeschen eingesammelt werden."""
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=str(anonymize_file_fixture.event.pk),
            reason="Klartext im Antrag",
            requested_by=staff_user,
        )

        anonymize_client(anonymize_file_fixture.client, user=staff_user)

        dr.refresh_from_db()
        assert dr.reason == "[Anonymisiert]"


@pytest.mark.django_db
class TestKAnonBridgeDeletesFilesAtTransactionEnd:
    """Paritaets-Gegenprobe (Refs #1782): der Bridge-Pfad nutzt dieselben Helfer.

    Sein Datei-Block stand seit M24 schon am Ende — hier als Charakterisierung
    gepinnt, damit die Umstellung des Hard-Pfads die Bridge nicht unbemerkt in
    die andere Richtung zieht.
    """

    def test_successful_bridge_run_removes_owned_rows_and_files(self, staff_user, anonymize_file_fixture):
        from core.models import Attachment
        from core.retention.anonymization import _k_anonymize_with_cascade

        _k_anonymize_with_cascade(anonymize_file_fixture.client, k=5)

        for owner_type in ("client", "case"):
            attachment = anonymize_file_fixture.attachments[owner_type]
            assert not Attachment.objects.filter(pk=attachment.pk).exists()
            assert not anonymize_file_fixture.paths[owner_type].exists()

    def test_bridge_abort_mid_cascade_leaves_rows_and_files_consistent(self, staff_user, anonymize_file_fixture):
        from core.models import Attachment
        from core.retention.anonymization import _k_anonymize_with_cascade

        boom = RuntimeError("Bridge-Redaktion abgebrochen")
        with patch("core.retention.anonymization._redact_activities", side_effect=boom):
            with pytest.raises(RuntimeError, match="Bridge-Redaktion abgebrochen"):
                _k_anonymize_with_cascade(anonymize_file_fixture.client, k=5)

        for owner_type in ("client", "case"):
            attachment = anonymize_file_fixture.attachments[owner_type]
            assert Attachment.objects.filter(pk=attachment.pk).exists(), f"{owner_type}: Zeile fehlt nach Rollback"
            assert anonymize_file_fixture.paths[owner_type].exists(), f"{owner_type}: .enc-Datei fehlt nach Rollback"
