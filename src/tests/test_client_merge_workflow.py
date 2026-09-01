"""Duplikat-Merge fuer Personen — Service-Tests (Refs #1876, ADR-045).

Geprueft wird die Service-Schicht in :mod:`core.services.client.merge`:
gestaffelter Vier-Augen-Weg (wie die Loeschung, ADR-044 P1c), die
Datenwanderung aller sechs eingehenden FKs, der Tombstone der Quelle im
Papierkorb (``merged_into``), das Zusammenspiel mit dem Retention-Lauf und
das Undo bis zum Fristablauf.

Die Sicht-Grenzen (Views, Papierkorb-Liste, Art.-15-Export) sind bewusst
NICHT hier — sie liegen bei den View- und Export-Tests
(``test_client_merge_views.py``, ``test_client_export*.py``).
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Client, DocumentTypeField, Event, LegalHold, RecentClientVisit
from core.models.offline_revocation import OfflineClientRevocation
from core.models.retention import RetentionProposal
from core.models.workitem import DeletionRequest, WorkItem
from core.services.client import (
    anonymize_eligible_soft_deleted_clients,
    request_client_deletion,
    restore_client,
)
from core.services.client.merge import (
    _is_empty_value,
    approve_client_merge,
    get_pending_merge_request,
    merge_clients,
    merge_requires_four_eyes,
    reject_client_merge,
    request_client_merge,
    unmerge_client,
)
from core.services.dashboard import log_activity, render_activity_summary
from core.services.system import bypass_replication_triggers
from tests.conftest import _make_attachment

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


def _file_slug_with_attachment(facility, owner, doc_type, user):
    """FILE-Feld am ``doc_type`` plus Anhang und Stufe-B-Marker am ``owner``.

    Liefert ``(slug, attachment)``. Der Marker referenziert die Attachment-PK
    (Entry-Schema ``{"id", "sort"}``, ``services/dynamic_data.py``).
    """
    attachment = _make_attachment(facility, owner, user, ft_name="Nachweis")
    field_template = attachment.field_template
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field_template, sort_order=2)
    owner.document_type = doc_type
    owner.data_json = {field_template.slug: {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}}
    owner.save()
    return field_template.slug, attachment


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


class TestEmptyValueDefinition:
    """Leere-Definition der Uebernahme-Regel (ADR-045, Entscheid 3).

    „Leer" muss auch die real vorkommenden Formular-Leerwerte treffen:
    Django-Forms liefern fuer geleerte Text-/Mehrfachfelder ""/[] in
    ``cleaned_data``, und ``validate_data_json`` filtert Leerwerte nicht —
    der Slot existiert dann MIT Leerwert statt gar nicht. FILE-Marker gelten
    als leer, solange sie keine ``entries`` tragen.
    """

    @pytest.mark.parametrize(
        "value",
        [None, "", [], {}, {"__files__": True}, {"__files__": True, "entries": []}],
        ids=["none", "leerstring", "leere-liste", "leeres-dict", "marker-ohne-entries-key", "marker-leere-entries"],
    )
    def test_empty(self, value):
        assert _is_empty_value(value) is True

    @pytest.mark.parametrize(
        "value",
        ["Notunterkunft", ["a"], {"k": "v"}, 0, False, {"__files__": True, "entries": [{"id": "a", "sort": 0}]}],
        ids=["string", "liste", "dict", "zahl-null", "bool-false", "marker-mit-entries"],
    )
    def test_filled(self, value):
        assert _is_empty_value(value) is False


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

    def test_adopts_source_value_into_blank_target_field(
        self, facility, source, target, doc_type_client_profile, lead_user
    ):
        """Ein Ziel-Feld MIT Leerwert ("" statt fehlendem Slot) zaehlt als leer —
        die Quelle fuellt es (ADR-045, Entscheid 3). Sonst ginge der Quell-Wert
        mit der Tombstone-Redaktion verloren."""
        source.document_type = doc_type_client_profile
        source.data_json = {"wohnsituation": "Notunterkunft"}
        source.save()
        target.document_type = doc_type_client_profile
        target.data_json = {"wohnsituation": ""}
        target.save()

        merge_clients(source, target, lead_user)

        target.refresh_from_db()
        assert target.data_json["wohnsituation"] == "Notunterkunft"
        merged_row = AuditLog.objects.get(action=AuditLog.Action.CLIENT_MERGED)
        assert merged_row.detail["adopted_slugs"] == ["wohnsituation"]

    def test_adopts_file_slug_and_moves_attachments(
        self, facility, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        """Ein GEFUELLTER ``__files__``-Marker der Quelle fuellt ein leeres
        Ziel-Feld — samt Wanderung der referenzierten Attachment-Zeilen
        (Marker-Invariante I-M1, ADR-039 §7).

        Blieben die Dateien bei der Quelle, wuerden sie nach Fristablauf mit
        deren Tombstone redigiert (Modul-Docstring) — unwiederbringlicher
        Verlust von Dateien, die zum Ziel gehoeren.
        """
        slug, attachment = _file_slug_with_attachment(facility, source, doc_type_client_profile, staff_user)
        target.document_type = doc_type_client_profile
        target.save()

        merge_clients(source, target, lead_user)

        target.refresh_from_db()
        attachment.refresh_from_db()
        assert target.data_json[slug] == {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}
        assert attachment.client_id == target.pk
        merged_row = AuditLog.objects.get(action=AuditLog.Action.CLIENT_MERGED)
        assert slug in merged_row.detail["adopted_slugs"]
        # Das Undo-Inventar traegt die gewanderten Datei-Zeilen — unmerge_client
        # liest den Rueckweg von genau hier.
        assert merged_row.detail["moved_attachments"] == [str(attachment.pk)]


class TestMergeFourEyes:
    """Antrag + Genehmigung, wenn eine der beiden Personen QUALIFIED ist."""

    def test_request_creates_pending_merge_request(self, facility, client_qualified, target, staff_user):
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt angelegt")
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.target_type == DeletionRequest.TargetType.CLIENT
        assert dr.target_id == client_qualified.pk
        assert dr.merge_into_id == target.pk
        assert dr.reason == "Doppelt angelegt"
        row = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        )
        # facility haelt die append-only Zeile in den facility-gescopten
        # Audit-Flaechen und in der per-Facility-HMAC-Kette — mit
        # facility=None fiele sie dauerhaft (unheilbar) heraus.
        assert row.facility_id == facility.pk
        # detail pinnt, WEN der Antrag betrifft und WOHIN gemerged wird;
        # reason lebt bewusst NICHT im detail (Refs #1093).
        assert row.detail == {"target_client": str(client_qualified.pk), "merge_into": str(target.pk)}
        assert get_pending_merge_request(client_qualified) == dr

    def test_request_is_idempotent(self, client_qualified, target, staff_user):
        first = request_client_merge(client_qualified, target, staff_user, reason="einmal")
        second = request_client_merge(client_qualified, target, staff_user, reason="zweimal")
        assert first.pk == second.pk
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED).count() == 1

    def test_pending_lookup_ignores_plain_deletion_request(self, client_qualified, staff_user):
        deletion = request_client_deletion(client_qualified, staff_user, reason="Person löschen")

        assert deletion.merge_into_id is None
        assert get_pending_merge_request(client_qualified) is None

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

    def test_rejected_request_blocks_neither_lookup_nor_new_request(
        self, client_qualified, target, staff_user, admin_user
    ):
        """Nach einer Ablehnung muss ein NEUER Antrag moeglich sein.

        ``get_pending_merge_request`` darf nur PENDING liefern: gaebe sie den
        abgelehnten Antrag zurueck, reichte ihn ``request_client_merge`` als
        Idempotenz-Treffer weiter — es entstuende nie wieder ein
        PENDING-Antrag, und ``approve_client_merge`` wiese ihn als „bereits
        bearbeitet" ab: der Merge waere dauerhaft still blockiert.
        """
        first = request_client_merge(client_qualified, target, staff_user, reason="Doppelt")
        reject_client_merge(first, admin_user)

        assert get_pending_merge_request(client_qualified) is None

        second = request_client_merge(client_qualified, target, staff_user, reason="Erneut: Doppelt")
        assert second.pk != first.pk
        assert second.status == DeletionRequest.Status.PENDING
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED).count() == 2

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

    def test_retention_run_redacts_moved_event_traces_of_the_source(
        self, facility, source, target, doc_type_contact, settings_obj, staff_user, lead_user
    ):
        """Die Tombstone-Redaktion muss die GEWANDERTEN Ereignisse mitnehmen.

        ``_event_ids_for_client(source)`` ist nach dem Merge leer — alle
        Ereignisse liegen beim Ziel. Ohne das ``moved_events``-Inventar aus dem
        ``CLIENT_MERGED``-``detail`` ueberlebten das Klartext-Pseudonym der
        Quelle in historischen Activity-Kontexten (vor #1972; Frist 365 Tage
        bzw. bis zur Redaktion des ZIELS), ``RetentionProposal.details['pseudonym']``,
        ``DeletionRequest.reason`` und abgelaufene ``LegalHold.reason`` mit
        Event-Ziel — exakt die Zeilen, die die Kaskade ohne Merge redigiert
        (#1751-Linie).
        """
        event = _event(facility, source, doc_type_contact, staff_user)
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=event,
            action=Activity.Action.EVENT_CREATED,
        )
        proposal = RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=event.pk,
            deletion_due_at=timezone.localdate(),
            details={"pseudonym": source.pseudonym, "occurred_at": str(event.occurred_at)},
            retention_category="identified",
        )
        deletion_request = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=event.pk,
            reason=f"Doppelt dokumentiert bei {source.pseudonym}",
            requested_by=staff_user,
        )
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason=f"Anfrage zum Kontakt mit {source.pseudonym}",
            created_by=lead_user,
            expires_at=timezone.localdate() - timedelta(days=1),
        )

        merge_clients(source, target, lead_user)
        Client.objects.filter(pk=source.pk).update(
            deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
        )

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 1

        activity.refresh_from_db()
        proposal.refresh_from_db()
        deletion_request.refresh_from_db()
        hold.refresh_from_db()
        assert activity.context["action"] == Activity.Action.REDACTED
        assert proposal.details["pseudonym"] == "[Anonymisiert]"
        # Der Nachweis-Trail des Vorschlags bleibt — nur der Freitext geht raus.
        assert proposal.details["occurred_at"] == str(event.occurred_at)
        assert deletion_request.reason == "[Anonymisiert]"
        assert hold.reason == "[Anonymisiert]"

    def test_tombstone_redaction_spares_post_merge_rows_and_target_rows(
        self, facility, source, target, doc_type_contact, settings_obj, staff_user, lead_user
    ):
        """Grenze der Mit-Redaktion: nur die Quell-Spuren, nichts vom Ziel.

        Metadaten, die NACH dem Merge zu einem gewanderten Ereignis entstehen,
        tragen den Kontext des ZIELS (der Retention-Scan schreibt jetzt dessen
        Pseudonym) — sie fallen erst mit der Redaktion des Ziels. Die eigenen
        Zeilen des Ziels bleiben ohnehin unberuehrt.
        """
        moved_event = _event(facility, source, doc_type_contact, staff_user)
        target_event = _event(facility, target, doc_type_contact, staff_user)
        target_activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=target_event,
            action=Activity.Action.EVENT_CREATED,
        )

        merge_clients(source, target, lead_user)

        post_merge_activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=moved_event,
            action=Activity.Action.EVENT_CREATED,
        )
        post_merge_proposal = RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=moved_event.pk,
            deletion_due_at=timezone.localdate(),
            details={"pseudonym": target.pseudonym},
            retention_category="identified",
        )

        Client.objects.filter(pk=source.pk).update(
            deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
        )

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 1

        target_activity.refresh_from_db()
        post_merge_activity.refresh_from_db()
        post_merge_proposal.refresh_from_db()
        expected = f"{doc_type_contact.name} für {target.pseudonym}"
        assert render_activity_summary(target_activity) == expected
        assert render_activity_summary(post_merge_activity) == expected
        assert post_merge_proposal.details["pseudonym"] == target.pseudonym

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

    def test_unmerge_refuses_source_with_anonymized_at_even_if_active(self, source, target, lead_user):
        """Refs #2085: der Guard haengt am terminalen Stempel, nicht an ``is_active``.

        Bis dahin fragte ``unmerge_client`` ``if not source.is_active`` — und das
        trug nur, weil ``_redact_client_identity`` beide Felder in derselben
        Transaktion setzt. Ein terminaler Zustand, dessen Schutz an einem
        nebenbei mitgesetzten Anzeigefeld haengt, ist genau die implizite
        Kopplung, die spaeter bricht. Dieser Test stellt den entkoppelten
        Zustand her (``anonymized_at`` gesetzt, ``is_active`` wieder ``True``)
        und pinnt damit die Angleichung an ``restore_client`` und
        ``restore_case``.
        """
        merge_clients(source, target, lead_user)
        Client.objects.filter(pk=source.pk).update(anonymized_at=timezone.now(), is_active=True)
        source.refresh_from_db()
        assert source.anonymized_at is not None
        assert source.is_active is True

        with pytest.raises(ValidationError) as exc:
            unmerge_client(source, lead_user)

        assert "anonymisiert" in " ".join(exc.value.messages).lower()
        source.refresh_from_db()
        assert source.merged_into_id == target.pk, "Die Zusammenfuehrung wurde trotz Abweisung aufgehoben"

    def test_unmerge_returns_adopted_file_attachments(
        self, facility, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        """Rueckweg des ``moved_attachments``-Inventars: uebernommene
        Datei-Zeilen wandern zur Quelle zurueck, der uebernommene Marker
        faellt am Ziel weg (I-M1: kein Marker ohne passende Zeile)."""
        slug, attachment = _file_slug_with_attachment(facility, source, doc_type_client_profile, staff_user)
        target.document_type = doc_type_client_profile
        target.save()
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        target.refresh_from_db()
        attachment.refresh_from_db()
        assert attachment.client_id == source.pk
        assert slug not in target.data_json
        source.refresh_from_db()
        assert source.data_json[slug]["entries"][0]["id"] == str(attachment.pk)

    def test_unmerge_refuses_missing_merge_audit_row(self, source, target, lead_user):
        """Fehlt die ``CLIENT_MERGED``-Zeile (Alt-/Reparaturzustand,
        Teil-Restore), muss das Undo mit der spezifizierten ValidationError
        abbrechen — „fehlt der Audit-Nachweis: Aufheben nicht moeglich" —
        statt zu crashen oder ohne Inventar weiterzulaufen."""
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        # DB-/Reparatur-Vektor: Zeile lautlos entfernen — Append-Only-Trigger
        # per Owner-DDL umgangen (Muster ``test_audit_chain``).
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(
                action=AuditLog.Action.CLIENT_MERGED,
                target_id=str(source.pk),
            ).delete()

        with pytest.raises(ValidationError):
            unmerge_client(source, lead_user)

        source.refresh_from_db()
        assert source.is_deleted is True
        assert source.merged_into_id == target.pk

    def test_unmerge_refuses_mismatched_merge_inventory(self, facility, source, target, staff_user, lead_user):
        """Passt die juengste ``CLIENT_MERGED``-Zeile nicht zum aktuellen
        ``merged_into``, darf das Undo NICHT mit dem falschen Inventar
        weiterlaufen (es verschoebe die falschen Zeilen), sondern muss sauber
        abbrechen und die Quelle als Tombstone stehen lassen."""
        stranger = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Merge-Fremdziel",
            created_by=staff_user,
        )
        merge_clients(source, target, lead_user)
        # Reparatur-/Altzustand: der Verweis zeigt auf ein Ziel, zu dem das
        # protokollierte Inventar nicht gehoert.
        Client.objects.filter(pk=source.pk).update(merged_into=stranger)
        source.refresh_from_db()

        with pytest.raises(ValidationError):
            unmerge_client(source, lead_user)

        source.refresh_from_db()
        assert source.is_deleted is True

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
