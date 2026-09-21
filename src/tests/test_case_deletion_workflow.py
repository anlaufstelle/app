"""Loeschworkflow fuer Faelle — Service-Tests (Refs #1818, ADR-044).

Gegenstueck zu ``test_client_deletion_workflow.py``. Geprueft wird die
Service-Schicht in :mod:`core.services.case.deletion`: gestaffelter Antrag
(P1c), Papierkorb, Wiederherstellung, Fristablauf mit Redaktion (P3a),
Legal-Hold-Sperre (P4) und der Verbleib der Ereignisse (P5).

Die Sicht-Grenze (Papierkorb-Fall ist ueberall unsichtbar) liegt bewusst in
``test_cases_trash_invariant.py`` — dort, wo bis #1818 die gegenteilige
Invariante gepinnt war.
"""

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, Attachment, AuditLog, Case, Client, Episode, LegalHold, User
from core.models.outcome import Milestone, OutcomeGoal
from core.models.workitem import DeletionRequest
from core.services.case.deletion import (
    anonymize_case_now,
    anonymize_eligible_soft_deleted_cases,
    approve_case_deletion,
    case_requires_four_eyes,
    get_pending_case_deletion_request,
    pending_deletion_case_ids,
    reject_case_deletion,
    request_case_deletion,
    restore_case,
    soft_delete_case,
)
from core.services.dashboard import render_activity_summary

pytestmark = pytest.mark.django_db


def _hold(facility, user, target_type, target_id, **kwargs):
    return LegalHold.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        reason="Gerichtsverfahren",
        created_by=user,
        **kwargs,
    )


class TestFourEyesStaging:
    """P1(c): Vier-Augen nur bei ``contact_stage=QUALIFIED``."""

    def test_qualified_client_requires_four_eyes(self, facility, client_qualified, staff_user):
        case = Case.objects.create(facility=facility, client=client_qualified, title="Fall Q", created_by=staff_user)
        assert case_requires_four_eyes(case) is True

    def test_identified_client_allows_direct_deletion(self, case_open):
        assert case_open.client.contact_stage == Client.ContactStage.IDENTIFIED
        assert case_requires_four_eyes(case_open) is False


class TestPendingRequestSurvivesADowngrade:
    """Refs #1818: Ein laufender Antrag darf nicht durch die Hintertuer umgangen werden.

    Die Kontaktstufe der Person ist waehrend eines laufenden Loeschantrags
    aenderbar. Wird sie von „Mit Zusatzangaben" auf „Nur Pseudonym"
    herabgestuft, entfaellt die Vier-Augen-Pflicht — die Route darf deshalb
    aber nicht auf den direkten Ausgang umschalten, solange der Antrag offen
    ist. Sonst landet der Fall im Papierkorb, waehrend der Antrag PENDING
    bleibt, und die spaetere Genehmigung laeuft in eine ValidationError.
    """

    def test_direct_route_defers_to_the_open_request(self, client, facility, client_qualified, staff_user, lead_user):
        from django.urls import reverse

        case = Case.objects.create(
            facility=facility, client=client_qualified, title="Fall mit Antrag", created_by=staff_user
        )
        dr = request_case_deletion(case, staff_user, reason="Antrag vor der Herabstufung")
        assert dr.status == DeletionRequest.Status.PENDING

        # Herabstufung waehrend des laufenden Antrags — ein erlaubter Vorgang.
        client_qualified.contact_stage = Client.ContactStage.IDENTIFIED
        client_qualified.save(update_fields=["contact_stage"])
        assert case_requires_four_eyes(case) is False

        client.force_login(lead_user)
        client.post(reverse("core:case_delete_request", kwargs={"pk": case.pk}), {"reason": "erneut"})

        case.refresh_from_db()
        dr.refresh_from_db()
        assert case.is_deleted is False, "Der offene Antrag wurde durch den direkten Weg umgangen"
        assert dr.status == DeletionRequest.Status.PENDING


class TestRequestCaseDeletion:
    def test_creates_pending_request(self, case_open, staff_user):
        dr = request_case_deletion(case_open, staff_user, "Fall irrtuemlich angelegt")
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.target_type == DeletionRequest.TargetType.CASE
        assert dr.target_id == case_open.pk
        assert dr.requested_by == staff_user

    def test_idempotent_returns_existing_pending(self, case_open, staff_user):
        first = request_case_deletion(case_open, staff_user, "Grund 1")
        second = request_case_deletion(case_open, staff_user, "Grund 2 (ignoriert)")
        assert first.pk == second.pk
        assert DeletionRequest.objects.filter(target_id=case_open.pk).count() == 1

    def test_audit_detail_carries_only_the_case_uuid(self, case_open, staff_user):
        dr = request_case_deletion(case_open, staff_user, "Grund")
        audit = AuditLog.objects.get(action=AuditLog.Action.DELETION_REQUESTED, target_id=str(dr.pk))
        assert audit.detail == {"target_case": str(case_open.pk)}

    def test_audit_row_attributes_requester_facility_and_target_type(self, case_open, staff_user):
        """Provenienz der append-only ``DELETION_REQUESTED``-Zeile (Art. 5 Abs. 2).

        ``AuditLog.user``/``facility`` sind nullable und ``audit_event``
        akzeptiert ``None`` ausdruecklich (Cron-/System-Pfade) — fiele eines
        der Felder weg, liefe der Antrag als System-Zeile aus jedem User- und
        Facility-Filter der Audit-Ansicht heraus. ``target_type`` ist der
        dokumentierte Rueckweg zum Antrag samt ``reason`` (Muster #932, wie im
        Ereignis-Pfad per ``test_deletion_approval_audit`` gepinnt).
        """
        dr = request_case_deletion(case_open, staff_user, "Grund")
        audit = AuditLog.objects.get(action=AuditLog.Action.DELETION_REQUESTED, target_id=str(dr.pk))
        assert audit.user == staff_user
        assert audit.facility == case_open.facility
        assert audit.target_type == "DeletionRequest"

    def test_pending_lookup_and_id_set(self, case_open, staff_user, facility):
        assert get_pending_case_deletion_request(case_open) is None
        assert pending_deletion_case_ids(facility, [case_open]) == set()
        dr = request_case_deletion(case_open, staff_user, "Grund")
        assert get_pending_case_deletion_request(case_open).pk == dr.pk
        assert pending_deletion_case_ids(facility, [case_open]) == {case_open.pk}


class TestSoftDeleteCase:
    """Direkter Weg (P1c, nicht-qualifizierte Person) — ohne Vier-Augen-Antrag."""

    def test_moves_case_to_trash_with_audit_and_activity(self, case_open, staff_user):
        soft_delete_case(case_open, staff_user)
        case_open.refresh_from_db()
        assert case_open.is_deleted is True
        assert case_open.deleted_by_id == staff_user.pk
        assert case_open.deleted_at is not None

        audit = AuditLog.objects.get(action=AuditLog.Action.CASE_SOFT_DELETED, target_id=str(case_open.pk))
        assert audit.target_type == "Case"
        assert audit.detail == {"trigger": "direct"}
        # Provenienz der append-only Zeile: ohne Actor/Facility griffe weder
        # der User- noch der Facility-Filter der Audit-Ansicht.
        assert audit.user == staff_user
        assert audit.facility == case_open.facility
        activity = Activity.objects.get(
            verb=Activity.Verb.DELETED,
            target_type=ContentType.objects.get_for_model(Case),
            target_id=case_open.pk,
        )
        assert activity.actor == staff_user
        assert activity.facility == case_open.facility
        # Pinnt zugleich die titel-freie Formulierung aus dem PII-freien
        # Aktionscode; der Falltitel liegt nur am Ziel (#1972).
        assert activity.context["action"] == Activity.Action.CASE_TRASHED
        assert render_activity_summary(activity) == "Fall in den Papierkorb verschoben"

    def test_second_soft_delete_is_rejected(self, case_open, staff_user):
        soft_delete_case(case_open, staff_user)
        with pytest.raises(ValidationError):
            soft_delete_case(case_open, staff_user)

    def test_events_keep_their_assignment_in_trash(self, case_open, staff_user, case_event):
        """P5: der Papierkorb loest die Zuordnung NICHT — sonst waere ein
        Wiederherstellen verlustbehaftet."""
        soft_delete_case(case_open, staff_user)
        case_event.refresh_from_db()
        assert case_event.case_id == case_open.pk


class TestApproveCaseDeletion:
    def test_approve_soft_deletes_and_closes_request(self, case_open, staff_user, lead_user):
        dr = request_case_deletion(case_open, staff_user, "Grund")
        approve_case_deletion(dr, lead_user)

        case_open.refresh_from_db()
        dr.refresh_from_db()
        assert case_open.is_deleted is True
        assert case_open.deleted_by_id == lead_user.pk
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == lead_user

        audit = AuditLog.objects.get(action=AuditLog.Action.CASE_SOFT_DELETED, target_id=str(case_open.pk))
        assert audit.detail == {"deletion_request": str(dr.pk)}
        # Actor der Genehmigung ist der Reviewer — nicht der Antragsteller.
        assert audit.user == lead_user
        assert audit.facility == case_open.facility

    def test_approve_writes_the_generic_workflow_entry(self, case_open, staff_user, lead_user):
        """``DELETION_APPROVED`` macht die Antrags-Entscheidung als eigenen
        Schritt sichtbar — und ist die Zeile, die die Massen-Destruktions-
        Erkennung zaehlt."""
        dr = request_case_deletion(case_open, staff_user, "Grund")
        approve_case_deletion(dr, lead_user)
        audit = AuditLog.objects.get(action=AuditLog.Action.DELETION_APPROVED, target_id=str(dr.pk))
        assert audit.detail == {"target_case": str(case_open.pk)}

    def test_self_review_blocked(self, case_open, staff_user):
        dr = request_case_deletion(case_open, staff_user, "Grund")
        with pytest.raises(ValidationError):
            approve_case_deletion(dr, staff_user)

    def test_reviewer_without_confirm_right_blocked(self, case_open, staff_user, assistant_user):
        dr = request_case_deletion(case_open, staff_user, "Grund")
        assert assistant_user.can_confirm_deletion is False
        with pytest.raises(ValidationError):
            approve_case_deletion(dr, assistant_user)
        case_open.refresh_from_db()
        assert case_open.is_deleted is False

    def test_double_approve_rejected_after_status_change(self, case_open, staff_user, lead_user, admin_user):
        """TOCTOU (Muster #1376 I9): der zweite Aufruf sieht den bereits
        fortgeschrittenen Status und bricht sauber ab."""
        dr = request_case_deletion(case_open, staff_user, "Grund")
        approve_case_deletion(dr, lead_user)
        with pytest.raises(ValidationError):
            approve_case_deletion(dr, admin_user)
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.CASE_SOFT_DELETED,
                target_id=str(case_open.pk),
            ).count()
            == 1
        )

    def test_status_recheck_fires_before_the_trash_guard(self, case_open, staff_user, lead_user, admin_user):
        """Der PENDING-Re-Check pinnt sich SELBST, nicht den ``is_deleted``-Guard.

        Der Test darueber blieb gruen, wenn man ``select_for_update()`` UND den
        Status-Re-Check ersatzlos strich: die erwartete ``ValidationError`` fiel
        dann aus ``_move_case_to_trash`` („Der Fall liegt bereits im
        Papierkorb."), und auch die Zaehl-Assertion hielt, weil das Audit erst
        nach jenem Guard geschrieben wird. Hier wird der Antragsstatus von
        aussen fortgeschrieben, WAEHREND der Fall noch lebt — dann gibt es
        keinen zweiten Guard, der einspringen koennte, und die Meldung sagt
        auch, welcher gegriffen hat.
        """
        dr = request_case_deletion(case_open, staff_user, "Grund")
        DeletionRequest.objects.filter(pk=dr.pk).update(status=DeletionRequest.Status.APPROVED)
        case_open.refresh_from_db()
        assert case_open.is_deleted is False

        with pytest.raises(ValidationError) as exc:
            approve_case_deletion(dr, admin_user)
        assert "bereits bearbeitet" in " ".join(exc.value.messages)

        case_open.refresh_from_db()
        assert case_open.is_deleted is False
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.CASE_SOFT_DELETED, target_id=str(case_open.pk)
        ).exists()


class TestRejectCaseDeletion:
    def test_service_rejects_distinct_reviewer_without_confirmation_right(self, case_open, staff_user, lead_user):
        dr = request_case_deletion(case_open, lead_user, "Grund")

        with pytest.raises(ValidationError) as exc_info:
            reject_case_deletion(dr, staff_user)

        assert exc_info.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REJECTED,
            target_id=str(dr.pk),
        ).exists()

    @pytest.mark.parametrize("role", list(User.Role.values))
    @pytest.mark.parametrize("has_right", [False, True], ids=["without-right", "with-right"])
    def test_reject_permission_depends_on_right_not_role(self, facility, case_open, lead_user, role, has_right):
        reviewer = User.objects.create_user(
            username=f"case-reject-{role}-{has_right}",
            role=role,
            facility=facility,
            is_staff=True,
            can_confirm_deletion=has_right,
        )
        dr = request_case_deletion(case_open, lead_user, "Grund")

        if has_right:
            reject_case_deletion(dr, reviewer)
        else:
            with pytest.raises(ValidationError, match="Löschbestätigung"):
                reject_case_deletion(dr, reviewer)

        dr.refresh_from_db()
        expected = DeletionRequest.Status.REJECTED if has_right else DeletionRequest.Status.PENDING
        assert dr.status == expected
        assert dr.reviewed_by_id == (reviewer.pk if has_right else None)

    def test_reject_sets_status_only(self, case_open, staff_user, lead_user):
        dr = request_case_deletion(case_open, staff_user, "Grund")
        reject_case_deletion(dr, lead_user)

        case_open.refresh_from_db()
        dr.refresh_from_db()
        assert case_open.is_deleted is False
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by == lead_user

    def test_reject_is_audited(self, case_open, staff_user, lead_user):
        """Rechenschaft ueber abgelehnte Loeschversuche (Art. 5 Abs. 2)."""
        dr = request_case_deletion(case_open, staff_user, "Grund")
        reject_case_deletion(dr, lead_user)
        audit = AuditLog.objects.get(action=AuditLog.Action.DELETION_REJECTED, target_id=str(dr.pk))
        assert audit.detail == {"target_case": str(case_open.pk)}

    def test_self_review_blocked(self, case_open, staff_user):
        dr = request_case_deletion(case_open, staff_user, "Grund")
        with pytest.raises(ValidationError):
            reject_case_deletion(dr, staff_user)

    def test_reject_of_an_already_approved_request_is_refused(self, case_open, staff_user, lead_user, admin_user):
        """Kein Zurueckdrehen auf REJECTED, waehrend der Fall im Papierkorb liegt.

        Der Fall-Pfad schreibt anders als ``reject_client_deletion``
        zusaetzlich eine ``DELETION_REJECTED``-Zeile ins append-only AuditLog —
        der widerspruechliche Zustand waere damit dauerhaft protokolliert statt
        transient.
        """
        dr = request_case_deletion(case_open, staff_user, "Grund")
        approve_case_deletion(dr, lead_user)

        with pytest.raises(ValidationError) as exc:
            reject_case_deletion(dr, admin_user)
        assert "bereits bearbeitet" in " ".join(exc.value.messages)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETION_REJECTED, target_id=str(dr.pk)).exists()

    def test_rerequest_after_reject_is_possible(self, case_open, staff_user, lead_user):
        first = request_case_deletion(case_open, staff_user, "Grund")
        reject_case_deletion(first, lead_user)
        second = request_case_deletion(case_open, staff_user, "Zweiter Anlauf")
        assert second.pk != first.pk
        assert second.status == DeletionRequest.Status.PENDING


class TestRestoreCase:
    def test_restore_clears_trash_flags_and_audits(self, case_open, staff_user, admin_user):
        soft_delete_case(case_open, staff_user)
        restore_case(case_open, admin_user)

        case_open.refresh_from_db()
        assert case_open.is_deleted is False
        assert case_open.deleted_at is None
        assert case_open.deleted_by is None
        assert AuditLog.objects.filter(
            action=AuditLog.Action.CASE_RESTORED,
            target_id=str(case_open.pk),
        ).exists()

    def test_restore_of_live_case_is_rejected(self, case_open, admin_user):
        with pytest.raises(ValidationError):
            restore_case(case_open, admin_user)

    def test_restore_keeps_episodes_and_goals(self, case_open, staff_user, admin_user, episode, outcome_goal):
        soft_delete_case(case_open, staff_user)
        restore_case(case_open, admin_user)
        episode.refresh_from_db()
        outcome_goal.refresh_from_db()
        assert episode.title
        assert outcome_goal.title


@pytest.fixture
def trashed_case(case_open, staff_user):
    soft_delete_case(case_open, staff_user)
    case_open.refresh_from_db()
    return case_open


def _expire(case, settings_obj, days_over=1):
    """Setzt ``deleted_at`` hinter die Papierkorb-Frist (P2b: ``client_trash_days``)."""
    Case.objects.filter(pk=case.pk).update(
        deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + days_over)
    )
    case.refresh_from_db()


class TestTrashExpiryRedaction:
    """P3(a): nach Fristablauf wird redigiert, nicht hart geloescht."""

    def test_case_row_survives_and_content_is_redacted(
        self, facility, settings_obj, trashed_case, episode, outcome_goal, milestone
    ):
        trashed_case.data_json = {"notiz": "Klartext"}
        trashed_case.save(update_fields=["data_json"])
        _expire(trashed_case, settings_obj)

        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 1

        trashed_case.refresh_from_db()
        assert Case.objects.filter(pk=trashed_case.pk).exists()
        assert trashed_case.is_deleted is True
        assert "Anonymisiert" in trashed_case.title
        assert trashed_case.description == ""
        assert trashed_case.data_json == {}
        assert trashed_case.document_type is None

        episode.refresh_from_db()
        outcome_goal.refresh_from_db()
        milestone.refresh_from_db()
        assert Episode.objects.filter(pk=episode.pk).exists()
        assert episode.description == ""
        assert "anonymisiert" in episode.title
        assert "anonymisiert" in outcome_goal.title
        assert "anonymisiert" in milestone.title
        assert OutcomeGoal.objects.filter(pk=outcome_goal.pk).exists()
        assert Milestone.objects.filter(pk=milestone.pk).exists()

    def test_events_lose_only_their_assignment(self, facility, settings_obj, trashed_case, case_event):
        """P5: das Ereignis bleibt in der Personenakte, nur die Fall-Zuordnung faellt."""
        _expire(trashed_case, settings_obj)
        anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        case_event.refresh_from_db()
        assert case_event.case_id is None
        assert case_event.is_deleted is False
        assert case_event.client_id == trashed_case.client_id

    def test_attachments_and_markers_are_gone(self, facility, settings_obj, trashed_case, authz_case_attachment):
        from core.services.file_vault import get_attachment_path

        path = get_attachment_path(authz_case_attachment)
        assert path.exists()
        Case.objects.filter(pk=trashed_case.pk).update(
            data_json={"anhang": {"__files__": True, "entries": [{"id": str(authz_case_attachment.pk), "sort": 0}]}}
        )
        _expire(trashed_case, settings_obj)

        anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        trashed_case.refresh_from_db()
        assert Attachment.objects.filter(case=trashed_case).count() == 0
        assert not path.exists()
        # Invariante I-M1 (ADR-039 §7): kein Marker ohne Zeile.
        assert "__files__" not in str(trashed_case.data_json)

    def test_activity_context_is_defensively_redacted(self, facility, settings_obj, trashed_case, staff_user):
        from core.services.dashboard import log_activity

        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=trashed_case,
            action=Activity.Action.CASE_CLOSED,
        )
        _expire(trashed_case, settings_obj)
        anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        contexts = Activity.objects.filter(
            target_type=ContentType.objects.get_for_model(Case),
            target_id=trashed_case.pk,
        ).values_list("context", flat=True)
        assert set(context["action"] for context in contexts) == {Activity.Action.REDACTED}

    def test_deletion_request_reason_is_redacted(self, facility, settings_obj, case_open, staff_user, lead_user):
        dr = request_case_deletion(case_open, staff_user, "Begruendung mit Klartext")
        approve_case_deletion(dr, lead_user)
        case_open.refresh_from_db()
        _expire(case_open, settings_obj)

        anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        dr.refresh_from_db()
        assert dr.reason == "[Anonymisiert]"

    def test_audit_entry_written(self, facility, settings_obj, trashed_case):
        _expire(trashed_case, settings_obj)
        anonymize_eligible_soft_deleted_cases(facility, settings_obj)
        audit = AuditLog.objects.get(action=AuditLog.Action.CASE_ANONYMIZED, target_id=str(trashed_case.pk))
        assert audit.detail["trigger"] == "trash_days_expired"

    def test_case_within_the_deadline_is_untouched(self, facility, settings_obj, trashed_case):
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 0
        trashed_case.refresh_from_db()
        assert "Anonymisiert" not in trashed_case.title

    def test_live_case_is_never_eligible(self, facility, settings_obj, case_open):
        Case.objects.filter(pk=case_open.pk).update(deleted_at=timezone.now() - timedelta(days=999))
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 0

    def test_dry_run_counts_without_changing(self, facility, settings_obj, trashed_case):
        _expire(trashed_case, settings_obj)
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj, dry_run=True) == 1
        trashed_case.refresh_from_db()
        assert "Anonymisiert" not in trashed_case.title


class TestLegalHoldBlocksCaseDeletion:
    """P4: Fall-Hold zulaessig, Ereignis-Hold sperrt den Fall (Spoliation)."""

    def test_hold_on_the_case_defers_redaction(self, facility, settings_obj, trashed_case, lead_user):
        _expire(trashed_case, settings_obj)
        _hold(facility, lead_user, "Case", trashed_case.pk)
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 0
        trashed_case.refresh_from_db()
        assert "Anonymisiert" not in trashed_case.title

    def test_hold_on_an_event_of_the_case_defers_redaction(
        self, facility, settings_obj, trashed_case, case_event, lead_user
    ):
        _expire(trashed_case, settings_obj)
        _hold(facility, lead_user, "Event", case_event.pk)
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 0

    def test_hold_on_the_client_defers_redaction(self, facility, settings_obj, trashed_case, lead_user):
        _expire(trashed_case, settings_obj)
        _hold(facility, lead_user, "Client", trashed_case.client_id)
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 0

    def test_dismissed_hold_does_not_block(self, facility, settings_obj, trashed_case, lead_user):
        _expire(trashed_case, settings_obj)
        _hold(facility, lead_user, "Case", trashed_case.pk, dismissed_at=timezone.now())
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 1

    def test_expired_hold_does_not_block(self, facility, settings_obj, trashed_case, lead_user):
        _expire(trashed_case, settings_obj)
        _hold(facility, lead_user, "Case", trashed_case.pk, expires_at=timezone.localdate() - timedelta(days=1))
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 1

    def test_hold_of_another_facility_does_not_block(self, facility, settings_obj, trashed_case, second_facility_user):
        _expire(trashed_case, settings_obj)
        _hold(second_facility_user.facility, second_facility_user, "Case", trashed_case.pk)
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 1


class TestAnonymizeCaseNow:
    """Sofort-Tilgung auf Admin-Anforderung (Muster ``anonymize_client_now``)."""

    def test_requires_trash_state(self, case_open, admin_user):
        with pytest.raises(ValidationError):
            anonymize_case_now(case_open, admin_user)

    def test_redacts_and_audits_with_manual_trigger(self, trashed_case, admin_user):
        anonymize_case_now(trashed_case, admin_user)
        trashed_case.refresh_from_db()
        assert "Anonymisiert" in trashed_case.title
        assert trashed_case.is_deleted is True
        audit = AuditLog.objects.get(action=AuditLog.Action.CASE_ANONYMIZED, target_id=str(trashed_case.pk))
        assert audit.detail["trigger"] == "manual"
        # ``Case`` hat kein ``anonymized_by``-Feld — diese Zeile ist der
        # EINZIGE Nachweis, wer die unwiderrufliche Tilgung ausgeloest hat.
        # Ohne Actor saehe die manuelle Tilgung wie ein Cron-Lauf aus.
        assert audit.user == admin_user
        assert audit.facility == trashed_case.facility

    def test_active_hold_blocks_the_manual_path(self, facility, trashed_case, lead_user, admin_user):
        _hold(facility, lead_user, "Case", trashed_case.pk)
        with pytest.raises(ValidationError):
            anonymize_case_now(trashed_case, admin_user)
        trashed_case.refresh_from_db()
        assert "Anonymisiert" not in trashed_case.title


class TestAnonymizationIsFinal:
    """``anonymized_at`` trennt „im Papierkorb" von „endgueltig getilgt"."""

    def test_stamp_is_set_by_the_redaction(self, trashed_case, admin_user):
        assert trashed_case.anonymized_at is None
        anonymize_case_now(trashed_case, admin_user)
        trashed_case.refresh_from_db()
        assert trashed_case.anonymized_at is not None

    def test_expiry_run_touches_each_case_exactly_once(self, facility, settings_obj, trashed_case):
        """Ohne Abbruchbedingung faende JEDER naechtliche Lauf denselben Fall erneut.

        Der Fall bleibt nach der Tilgung soft-geloescht und erfuellt
        ``deleted_at__lt=cutoff`` weiter — die Folge waere eine neue
        ``CASE_ANONYMIZED``-Zeile im append-only AuditLog pro Lauf und ein
        Betriebszaehler, der nie auf Null zurueckfaellt.
        """
        _expire(trashed_case, settings_obj)
        laeufe = [anonymize_eligible_soft_deleted_cases(facility, settings_obj) for _ in range(3)]
        assert laeufe == [1, 0, 0]
        assert (
            AuditLog.objects.filter(action=AuditLog.Action.CASE_ANONYMIZED, target_id=str(trashed_case.pk)).count() == 1
        )

    def test_dry_run_also_skips_already_anonymized_cases(self, facility, settings_obj, trashed_case, admin_user):
        _expire(trashed_case, settings_obj)
        anonymize_case_now(trashed_case, admin_user)
        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj, dry_run=True) == 0

    def test_restore_of_an_anonymized_case_is_refused(self, trashed_case, admin_user):
        """Sonst kaeme eine inhaltsleere Huelle in Liste, Detail und Statistik zurueck."""
        anonymize_case_now(trashed_case, admin_user)
        with pytest.raises(ValidationError) as exc:
            restore_case(trashed_case, admin_user)
        assert "endgültig anonymisiert" in " ".join(exc.value.messages)
        trashed_case.refresh_from_db()
        assert trashed_case.is_deleted is True

    def test_second_manual_anonymization_is_refused(self, trashed_case, admin_user):
        anonymize_case_now(trashed_case, admin_user)
        with pytest.raises(ValidationError):
            anonymize_case_now(trashed_case, admin_user)
        assert (
            AuditLog.objects.filter(action=AuditLog.Action.CASE_ANONYMIZED, target_id=str(trashed_case.pk)).count() == 1
        )

    def test_person_anonymization_stamps_the_case_too(self, case_open, client_identified, admin_user):
        """Der Stempel sitzt in der GETEILTEN Redaktion, nicht nur im Fall-Weg."""
        from core.services.client import anonymize_client_now

        client_identified.soft_delete(user=admin_user)
        anonymize_client_now(client_identified, admin_user)
        case_open.refresh_from_db()
        assert case_open.anonymized_at is not None


class TestRetentionCommandRunsTheCasePath:
    def test_enforce_retention_reports_and_redacts(self, facility, settings_obj, trashed_case, capsys):
        from django.core.management import call_command

        _expire(trashed_case, settings_obj)
        call_command("enforce_retention")
        trashed_case.refresh_from_db()
        assert "Anonymisiert" in trashed_case.title
        assert "trash-expired case(s)" in capsys.readouterr().out
