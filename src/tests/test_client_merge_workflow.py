"""Duplikat-Merge fuer Personen — Service-Tests (Refs #1876, ADR-045).

Geprueft wird die Service-Schicht in :mod:`core.services.client.merge`:
gestaffelter Vier-Augen-Weg (wie die Loeschung, ADR-044 P1c), die
Datenwanderung aller sechs eingehenden FKs, der Tombstone der Quelle im
Papierkorb (``merged_into``), das Zusammenspiel mit dem Retention-Lauf und
das Undo bis zum Fristablauf.

Die Sicht-Grenzen (Views, Papierkorb-Liste, Art.-15-Export) sind bewusst
NICHT hier — sie folgen mit den Views (Stufen 2/3 von #1876).
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, LegalHold, RecentClientVisit
from core.models.offline_revocation import OfflineClientRevocation
from core.models.workitem import DeletionRequest, WorkItem
from core.services.client import (
    anonymize_eligible_soft_deleted_clients,
    request_client_deletion,
    restore_client,
)
from core.services.client.merge import (
    approve_client_merge,
    get_pending_merge_request,
    merge_clients,
    merge_requires_four_eyes,
    reject_client_merge,
    request_client_merge,
    unmerge_client,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Merge-Quelle",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Merge-Ziel",
        created_by=staff_user,
    )


def _event(facility, client, doc_type, user, case=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        case=case,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=user,
    )


class TestMergeStaging:
    """Vier-Augen gestaffelt wie die Loeschung (ADR-045, Entscheid 1)."""

    def test_qualified_source_requires_four_eyes(self, client_qualified, target):
        assert merge_requires_four_eyes(client_qualified, target) is True

    def test_qualified_target_requires_four_eyes(self, source, client_qualified):
        assert merge_requires_four_eyes(source, client_qualified) is True

    def test_both_identified_allows_direct_merge(self, source, target):
        assert merge_requires_four_eyes(source, target) is False

    def test_direct_merge_refuses_qualified_pair(self, client_qualified, target, lead_user):
        with pytest.raises(ValidationError):
            merge_clients(client_qualified, target, lead_user)

    def test_direct_merge_requires_lead(self, source, target, staff_user):
        with pytest.raises(ValidationError):
            merge_clients(source, target, staff_user)


class TestMergeValidation:
    def test_refuses_self_merge(self, source, lead_user):
        with pytest.raises(ValidationError):
            merge_clients(source, source, lead_user)

    def test_refuses_cross_facility_merge(self, source, second_facility, staff_user, lead_user):
        other = Client.objects.create(
            facility=second_facility,
            pseudonym="Fremde Einrichtung",
        )
        with pytest.raises(ValidationError):
            merge_clients(source, other, lead_user)

    def test_refuses_trashed_participant(self, source, target, lead_user):
        source.soft_delete(user=lead_user)
        with pytest.raises(ValidationError):
            merge_clients(source, target, lead_user)

    def test_refuses_anonymized_participant(self, source, target, lead_user):
        target.is_active = False
        target.save(update_fields=["is_active"])
        with pytest.raises(ValidationError):
            merge_clients(source, target, lead_user)

    def test_refuses_participant_under_legal_hold(self, source, target, lead_user):
        LegalHold.objects.create(
            facility=source.facility,
            target_type="Client",
            target_id=source.pk,
            reason="Gerichtsverfahren",
            created_by=lead_user,
        )
        with pytest.raises(ValidationError):
            merge_clients(source, target, lead_user)

    def test_refuses_pending_deletion_request(self, source, target, staff_user, lead_user):
        request_client_deletion(source, staff_user, reason="Antrag laeuft")
        with pytest.raises(ValidationError):
            merge_clients(source, target, lead_user)


class TestMergeExecution:
    """Direkter Weg (beide IDENTIFIED, Leitung)."""

    def test_moves_events_cases_and_workitems(self, facility, source, target, doc_type_contact, staff_user, lead_user):
        case = Case.objects.create(facility=facility, client=source, title="Fall Quelle", created_by=staff_user)
        case_event = _event(facility, source, doc_type_contact, staff_user, case=case)
        free_event = _event(facility, source, doc_type_contact, staff_user)
        item = WorkItem.objects.create(facility=facility, client=source, title="Aufgabe", created_by=staff_user)

        merge_clients(source, target, lead_user)

        case.refresh_from_db()
        case_event.refresh_from_db()
        free_event.refresh_from_db()
        item.refresh_from_db()
        assert case.client_id == target.pk
        assert case_event.client_id == target.pk
        assert free_event.client_id == target.pk
        assert item.client_id == target.pk
        # Invariante Event.client == Event.case.client (services/events/crud.py)
        assert case_event.case_id == case.pk

    def test_recent_visits_deduplicated(self, facility, source, target, staff_user, lead_user):
        now = timezone.now()
        # staff_user hat BEIDE besucht -> Kollision auf unique_together (user, client)
        sv = RecentClientVisit.objects.create(user=staff_user, client=source, facility=facility, is_favorite=True)
        RecentClientVisit.objects.filter(pk=sv.pk).update(visited_at=now)
        tv = RecentClientVisit.objects.create(user=staff_user, client=target, facility=facility, is_favorite=False)
        RecentClientVisit.objects.filter(pk=tv.pk).update(visited_at=now - timedelta(days=3))
        # lead_user hat nur die Quelle besucht -> Zeile wandert
        lv = RecentClientVisit.objects.create(user=lead_user, client=source, facility=facility)
        RecentClientVisit.objects.filter(pk=lv.pk).update(visited_at=now - timedelta(days=1))

        merge_clients(source, target, lead_user)

        assert not RecentClientVisit.objects.filter(client=source).exists()
        merged = RecentClientVisit.objects.get(user=staff_user, client=target)
        assert merged.is_favorite is True  # OR beider Seiten
        assert merged.visited_at == now  # juengstes visited_at gewinnt
        assert RecentClientVisit.objects.filter(user=lead_user, client=target).exists()

    def test_source_becomes_tombstone_with_merge_audit(self, source, target, lead_user):
        merge_clients(source, target, lead_user)

        source.refresh_from_db()
        assert source.is_deleted is True
        assert source.merged_into_id == target.pk
        assert source.deleted_by == lead_user

        merged_row = AuditLog.objects.get(
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Client",
            target_id=str(source.pk),
        )
        assert merged_row.detail["merged_into"] == str(target.pk)
        # Breach-Neutralitaet: der Merge-Soft-Delete darf detect_mass_client_destruction
        # nicht speisen — weder CLIENT_SOFT_DELETED noch DELETION_APPROVED.
        assert not AuditLog.objects.filter(action=AuditLog.Action.CLIENT_SOFT_DELETED).exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).exists()

    def test_source_offline_bundles_revoked(self, source, target, lead_user):
        merge_clients(source, target, lead_user)
        assert OfflineClientRevocation.objects.filter(client_pk=source.pk).exists()

    def test_adopts_only_empty_target_fields(
        self, facility, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        source.document_type = doc_type_client_profile
        source.data_json = {"wohnsituation": "Notunterkunft", "diagnose": "F32"}
        source.save()
        target.document_type = doc_type_client_profile
        target.data_json = {"wohnsituation": "eigene Wohnung"}
        target.save()

        merge_clients(source, target, lead_user)

        target.refresh_from_db()
        # Ziel behaelt seine Werte, Quelle fuellt nur leere Felder
        assert target.data_json["wohnsituation"] == "eigene Wohnung"
        assert "diagnose" in target.data_json
        merged_row = AuditLog.objects.get(action=AuditLog.Action.CLIENT_MERGED)
        assert merged_row.detail["adopted_slugs"] == ["diagnose"]
        # KEINE Werte im Audit-detail
        assert "F32" not in str(merged_row.detail)

    def test_adopts_document_type_when_target_has_none(
        self, facility, source, target, doc_type_client_profile, lead_user
    ):
        source.document_type = doc_type_client_profile
        source.data_json = {"wohnsituation": "Notunterkunft"}
        source.save()

        merge_clients(source, target, lead_user)

        target.refresh_from_db()
        assert target.document_type_id == doc_type_client_profile.pk
        assert target.data_json["wohnsituation"] == "Notunterkunft"


class TestMergeFourEyes:
    """Antrag + Genehmigung, wenn eine der beiden Personen QUALIFIED ist."""

    def test_request_creates_pending_merge_request(self, client_qualified, target, staff_user):
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt angelegt")
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.target_type == DeletionRequest.TargetType.CLIENT
        assert dr.target_id == client_qualified.pk
        assert dr.merge_into_id == target.pk
        assert AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        ).exists()
        assert get_pending_merge_request(client_qualified) == dr

    def test_request_is_idempotent(self, client_qualified, target, staff_user):
        first = request_client_merge(client_qualified, target, staff_user, reason="einmal")
        second = request_client_merge(client_qualified, target, staff_user, reason="zweimal")
        assert first.pk == second.pk
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED).count() == 1

    def test_approve_rejects_requester_as_reviewer(self, client_qualified, target, admin_user):
        dr = request_client_merge(client_qualified, target, admin_user, reason="Doppelt")
        with pytest.raises(ValidationError):
            approve_client_merge(dr, admin_user)

    def test_approve_requires_confirmer_right(self, client_qualified, target, staff_user, assistant_user):
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt")
        with pytest.raises(ValidationError):
            approve_client_merge(dr, assistant_user)

    def test_approve_executes_merge_and_closes_request(
        self, facility, client_qualified, target, doc_type_contact, staff_user, admin_user
    ):
        event = _event(facility, client_qualified, doc_type_contact, staff_user)
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt")

        result = approve_client_merge(dr, admin_user)

        assert result.pk == target.pk
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == admin_user
        event.refresh_from_db()
        assert event.client_id == target.pk
        client_qualified.refresh_from_db()
        assert client_qualified.is_deleted is True
        assert client_qualified.merged_into_id == target.pk
        # Kontaktstufe: hoechste gewinnt — das Ziel steigt auf QUALIFIED.
        target.refresh_from_db()
        assert target.contact_stage == Client.ContactStage.QUALIFIED
        assert AuditLog.objects.filter(
            action=AuditLog.Action.STAGE_CHANGE,
            target_id=str(target.pk),
        ).exists()
        # Kein DELETION_APPROVED (Breach-Zaehler, siehe ADR-045).
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).exists()

    def test_approve_is_single_shot(self, client_qualified, target, staff_user, admin_user):
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt")
        approve_client_merge(dr, admin_user)
        with pytest.raises(ValidationError):
            approve_client_merge(dr, admin_user)

    def test_reject_leaves_clients_untouched(self, client_qualified, target, staff_user, admin_user):
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt")
        reject_client_merge(dr, admin_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        client_qualified.refresh_from_db()
        assert client_qualified.is_deleted is False
        assert client_qualified.merged_into_id is None
        assert AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REJECTED,
            target_id=str(dr.pk),
        ).exists()

    def test_generic_deletion_approval_refuses_merge_requests(self, client_qualified, target, staff_user, admin_user):
        """Ein Merge-Antrag darf nicht als blosser Loeschantrag genehmigt werden —
        sonst landet die Quelle im Papierkorb, ohne dass irgendetwas wandert."""
        from core.services.client import approve_client_deletion

        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt")
        with pytest.raises(ValidationError):
            approve_client_deletion(dr, admin_user)
        client_qualified.refresh_from_db()
        assert client_qualified.is_deleted is False


class TestTombstoneRetention:
    def test_retention_run_redacts_tombstone_and_keeps_reference(
        self, facility, source, target, settings_obj, lead_user
    ):
        merge_clients(source, target, lead_user)
        Client.objects.filter(pk=source.pk).update(
            deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
        )

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        assert count == 1
        source.refresh_from_db()
        assert source.is_active is False
        # Der Verweis ueberlebt die Redaktion — der Audit-Trail der Quelle
        # bleibt ueber den Tombstone erreichbar (ADR-045, Entscheid 2).
        assert source.merged_into_id == target.pk

    def test_restore_refuses_merge_tombstone(self, source, target, lead_user):
        """Wiederherstellen aus dem Papierkorb wuerde die Quelle ohne ihre
        gewanderten Daten auferstehen lassen — nur unmerge stellt konsistent her."""
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        with pytest.raises(ValidationError):
            restore_client(source, lead_user)


class TestUnmerge:
    def test_unmerge_restores_assignments(self, facility, source, target, doc_type_contact, staff_user, lead_user):
        case = Case.objects.create(facility=facility, client=source, title="Fall Quelle", created_by=staff_user)
        case_event = _event(facility, source, doc_type_contact, staff_user, case=case)
        free_event = _event(facility, source, doc_type_contact, staff_user)
        item = WorkItem.objects.create(facility=facility, client=source, title="Aufgabe", created_by=staff_user)
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        source.refresh_from_db()
        assert source.is_deleted is False
        assert source.merged_into_id is None
        for obj in (case, case_event, free_event, item):
            obj.refresh_from_db()
            assert obj.client_id == source.pk
        assert AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_RESTORED,
            target_id=str(source.pk),
        ).exists()

    def test_unmerge_reverts_adopted_data_and_stage(
        self, facility, client_qualified, target, doc_type_client_profile, staff_user, admin_user, lead_user
    ):
        client_qualified.document_type = doc_type_client_profile
        client_qualified.data_json = {"diagnose": "F32"}
        client_qualified.save()
        target.document_type = doc_type_client_profile
        target.save()
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt")
        approve_client_merge(dr, admin_user)
        client_qualified.refresh_from_db()

        unmerge_client(client_qualified, lead_user)

        target.refresh_from_db()
        assert "diagnose" not in target.data_json
        assert target.contact_stage == Client.ContactStage.IDENTIFIED
        client_qualified.refresh_from_db()
        assert client_qualified.is_deleted is False
        assert client_qualified.data_json.get("diagnose")  # Quelle unangetastet

    def test_unmerge_keeps_post_merge_events_on_target(
        self, facility, source, target, doc_type_contact, staff_user, lead_user
    ):
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        new_event = _event(facility, target, doc_type_contact, staff_user)

        unmerge_client(source, lead_user)

        new_event.refresh_from_db()
        assert new_event.client_id == target.pk

    def test_unmerge_requires_lead(self, source, target, staff_user, lead_user):
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        with pytest.raises(ValidationError):
            unmerge_client(source, staff_user)

    def test_unmerge_refuses_redacted_source(self, facility, source, target, settings_obj, lead_user):
        """Nach der Tilgung sind die Stammdaten der Quelle unwiederbringlich —
        nur die Zuordnung waere noch umkehrbar, und das ist kein Restore."""
        merge_clients(source, target, lead_user)
        Client.objects.filter(pk=source.pk).update(
            deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
        )
        anonymize_eligible_soft_deleted_clients(source.facility, settings_obj)
        source.refresh_from_db()
        with pytest.raises(ValidationError):
            unmerge_client(source, lead_user)

    def test_unmerge_requires_merge_tombstone(self, source, lead_user):
        with pytest.raises(ValidationError):
            unmerge_client(source, lead_user)


class TestChainMerge:
    """Ketten-Merges duerfen das Undo-Versprechen nicht still brechen (ADR-045).

    A→B und danach B→C liesse ``unmerge_client(A)`` fehlerfrei durchlaufen,
    obwohl die Inventar-Updates (Filter ``client=target``) 0 Zeilen treffen —
    A kaeme als leeres Duplikat zurueck, waehrend die Daten bei C liegen.
    Deshalb: solange eine Person einen unredigierten Tombstone traegt, ist sie
    selbst keine zulaessige QUELLE; und ``unmerge_client`` weist ein Ziel ab,
    das seinerseits zusammengefuehrt wurde (Legacy-/Reparaturzustaende).
    """

    @pytest.fixture
    def third(self, facility, staff_user):
        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Merge-Drittakte",
            created_by=staff_user,
        )

    def test_direct_chain_merge_is_blocked(self, source, target, third, lead_user):
        merge_clients(source, target, lead_user)
        with pytest.raises(ValidationError):
            merge_clients(target, third, lead_user)

    def test_requested_chain_merge_is_blocked(self, source, target, third, staff_user, lead_user):
        merge_clients(source, target, lead_user)
        with pytest.raises(ValidationError):
            request_client_merge(target, third, staff_user, reason="Kette")

    def test_merge_into_tombstone_carrier_stays_allowed_and_undoable(
        self, facility, source, target, third, doc_type_contact, staff_user, lead_user
    ):
        """C→B bleibt erlaubt, wenn B Tombstones TRAEGT — das Inventar von A
        liegt weiterhin bei B, LIFO-Undo funktioniert nachweislich."""
        ev = _event(facility, source, doc_type_contact, staff_user)
        merge_clients(source, target, lead_user)
        merge_clients(third, target, lead_user)

        source.refresh_from_db()
        unmerge_client(source, lead_user)

        ev.refresh_from_db()
        source.refresh_from_db()
        assert ev.client_id == source.pk
        assert source.is_deleted is False

    def test_chain_merge_allowed_after_tombstone_redaction(
        self, facility, source, target, third, settings_obj, lead_user
    ):
        merge_clients(source, target, lead_user)
        Client.objects.filter(pk=source.pk).update(
            deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
        )
        anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        result = merge_clients(target, third, lead_user)

        assert result.pk == third.pk

    def test_unmerge_refuses_when_target_is_itself_merged(
        self, facility, source, target, third, doc_type_contact, staff_user, lead_user
    ):
        """Abwehr in der Tiefe fuer Alt-/Reparaturzustaende: liegt das Ziel
        selbst als Merge-Tombstone vor, wanderte nichts zurueck — der Aufruf
        muss scheitern statt ein leeres Duplikat wiederzubeleben."""
        ev = _event(facility, source, doc_type_contact, staff_user)
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        # Kettenzustand direkt herstellen (der Validierungs-Guard verhindert
        # ihn ueber die Service-Wege) — Simulation eines Alt-Datenbestands.
        target.merged_into = third
        target.save(update_fields=["merged_into"])
        target.soft_delete(user=lead_user)
        Event.objects.filter(pk=ev.pk).update(client=third)

        with pytest.raises(ValidationError):
            unmerge_client(source, lead_user)

        source.refresh_from_db()
        assert source.is_deleted is True
        assert source.merged_into_id == target.pk
