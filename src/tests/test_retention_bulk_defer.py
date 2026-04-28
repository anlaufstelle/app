"""Tests for retention bulk actions (#514) and deferred-follow-up behaviour (#515)."""

import uuid
from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import AuditLog, Client, Event, RetentionProposal, Settings
from core.services.retention import (
    bulk_approve_proposals,
    bulk_defer_proposals,
    bulk_reject_proposals,
    create_proposal,
    defer_proposal,
    reactivate_deferred_proposals,
    reject_proposal,
)


def _make_proposal(facility, status=RetentionProposal.Status.PENDING, target_id=None):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=target_id or uuid.uuid4(),
        retention_category="anonymous",
        deletion_due_at=date.today() - timedelta(days=1),
        status=status,
    )


@pytest.mark.django_db
class TestBulkActions:
    def test_bulk_approve_processes_all(self, facility, lead_user):
        ps = [_make_proposal(facility) for _ in range(3)]
        count = bulk_approve_proposals(ps, lead_user)
        assert count == 3
        for p in ps:
            p.refresh_from_db()
            assert p.status == RetentionProposal.Status.APPROVED

    def test_bulk_defer_increments_defer_count(self, facility, lead_user):
        p1 = _make_proposal(facility)
        bulk_defer_proposals([p1], lead_user, days=14)
        p1.refresh_from_db()
        assert p1.status == RetentionProposal.Status.DEFERRED
        assert p1.defer_count == 1
        assert p1.deferred_until == date.today() + timedelta(days=14)

    def test_bulk_reject_marks_all(self, facility, lead_user):
        p1 = _make_proposal(facility)
        bulk_reject_proposals([p1], lead_user)
        p1.refresh_from_db()
        assert p1.status == RetentionProposal.Status.REJECTED


@pytest.mark.django_db
class TestDeferFollowup:
    def test_defer_writes_audit_entry(self, facility, lead_user):
        p = _make_proposal(facility)
        defer_proposal(p, lead_user, days=7)
        entry = AuditLog.objects.filter(target_id=str(p.target_id)).latest("timestamp")
        assert entry.detail.get("category") == "retention_proposal_deferred"
        assert entry.detail.get("defer_count") == 1

    def test_defer_count_accumulates(self, facility, lead_user):
        p = _make_proposal(facility)
        defer_proposal(p, lead_user)
        # Reset to pending to defer again
        p.status = RetentionProposal.Status.PENDING
        p.save(update_fields=["status"])
        defer_proposal(p, lead_user)
        p.refresh_from_db()
        assert p.defer_count == 2

    def test_reject_writes_audit_entry(self, facility, lead_user):
        p = _make_proposal(facility)
        reject_proposal(p, lead_user)
        entry = AuditLog.objects.filter(target_id=str(p.target_id)).latest("timestamp")
        assert entry.detail.get("category") == "retention_proposal_rejected"


# ---------------------------------------------------------------------------
# Deferred-Expiry Re-Aktivierung (Refs #515, WP5 Gap-Analyse)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeferredExpiry:
    """Abgelaufenes ``deferred_until`` muss per ``reactivate_deferred_proposals``
    wieder in ``PENDING`` überführt werden — sonst „gewinnen" zurückgestellte
    Vorschläge nach Ablauf stillschweigend.

    Der bestehende Test ``test_idempotent_with_deferred_proposal`` deckt nur
    den Fall ab, dass ein DEFERRED-Proposal als aktiv gezählt wird. Hier wird
    der Expiry-Pfad zusätzlich geprüft.
    """

    def test_expired_deferred_is_reset_to_pending(self, facility, lead_user):
        Settings.objects.create(
            facility=facility,
            retention_auto_approve_after_defer=False,
            retention_max_defer_count=2,
        )
        p = _make_proposal(facility, status=RetentionProposal.Status.DEFERRED)
        # `deferred_until` in der Vergangenheit — Re-Aktivierung fällig.
        p.deferred_until = date.today() - timedelta(days=1)
        p.defer_count = 1
        p.save(update_fields=["deferred_until", "defer_count"])

        reactivated, auto_approved = reactivate_deferred_proposals(facility)

        p.refresh_from_db()
        assert reactivated == 1
        assert auto_approved == 0
        assert p.status == RetentionProposal.Status.PENDING

    def test_not_yet_expired_deferred_stays_deferred(self, facility, lead_user):
        Settings.objects.create(
            facility=facility,
            retention_auto_approve_after_defer=False,
            retention_max_defer_count=2,
        )
        p = _make_proposal(facility, status=RetentionProposal.Status.DEFERRED)
        # `deferred_until` in der Zukunft — nicht anfassen.
        p.deferred_until = date.today() + timedelta(days=7)
        p.save(update_fields=["deferred_until"])

        reactivated, auto_approved = reactivate_deferred_proposals(facility)

        p.refresh_from_db()
        assert reactivated == 0
        assert auto_approved == 0
        assert p.status == RetentionProposal.Status.DEFERRED

    def test_expired_deferred_auto_approved_when_over_max_defers(self, facility, lead_user):
        """Wenn ``auto_approve_after_defer=True`` und ``defer_count >= max_defer_count``,
        wird ein abgelaufener Deferred-Proposal direkt APPROVED statt erneut vorgelegt."""
        Settings.objects.create(
            facility=facility,
            retention_auto_approve_after_defer=True,
            retention_max_defer_count=2,
        )
        p = _make_proposal(facility, status=RetentionProposal.Status.DEFERRED)
        p.deferred_until = date.today() - timedelta(days=1)
        p.defer_count = 2  # bereits am Limit
        p.save(update_fields=["deferred_until", "defer_count"])

        reactivated, auto_approved = reactivate_deferred_proposals(facility)

        p.refresh_from_db()
        assert auto_approved == 1
        assert reactivated == 0
        assert p.status == RetentionProposal.Status.APPROVED


# ---------------------------------------------------------------------------
# K-Anonymisierte Clients: Hard-Delete-Pfad überspringt sie (WP5)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestKAnonymizedClientSkip:
    """Clients mit ``k_anonymized=True`` sind eine *Alternative* zum Hard-Delete
    (Refs #535). Im Enforce-Retention-Durchlauf darf ein solcher Client daher
    nicht erneut anonymisiert werden (kein „Gelöscht-<prefix>"-Overwrite, keine
    doppelte Verarbeitung).

    Beobachtung am Code (``enforce_retention._anonymize_clients``):
    Der Skip-Filter basiert aktuell ausschließlich auf
    ``pseudonym__startswith='Gelöscht-'`` — ein k-anonymisiertes
    Pseudonym hat aber das Präfix ``anon-…``. Der Test flaggt dies als
    xfail, damit der Bug sichtbar bleibt, ohne Produktivcode anzufassen.
    """

    @pytest.mark.xfail(
        reason=(
            "enforce_retention._anonymize_clients prüft nur pseudonym-Präfix 'Gelöscht-', "
            "nicht das k_anonymized-Flag. k-anonymisierte Clients werden dadurch ein "
            "zweites Mal via anonymize() überschrieben."
        ),
        strict=True,
    )
    def test_k_anonymized_client_not_touched_by_enforce_retention(
        self,
        facility,
        staff_user,
        doc_type_contact,
    ):
        Settings.objects.create(
            facility=facility,
            retention_anonymous_days=30,
            retention_identified_days=365,
            retention_qualified_days=3650,
            retention_activities_days=30,
        )
        # Ein k-anonymisierter Client mit bereits soft-gelöschtem Event —
        # Kandidat für den _anonymize_clients-Pfad.
        k_client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="anon-deadbeefcafe",
            k_anonymized=True,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=k_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_deleted=True,  # alle Events gelöscht → _anonymize_clients-Kandidat
            created_by=staff_user,
        )
        pseudonym_before = k_client.pseudonym

        call_command("enforce_retention")

        k_client.refresh_from_db()
        # k-anonymisierter Client darf nicht erneut anonymisiert werden —
        # Pseudonym bleibt wie vorher.
        assert k_client.pseudonym == pseudonym_before, (
            f"k-anonymisierter Client wurde überschrieben: {k_client.pseudonym}"
        )

    def test_k_anonymized_client_no_new_proposal_via_create_proposal(self, facility):
        """Positive Invariante: Ein direkter ``create_proposal``-Aufruf mit
        ``target_type='Client'`` ist derzeit nicht Teil des Enforce-Pfads
        (Proposals werden nur für Events angelegt). Dieser Test dokumentiert,
        dass ``create_proposal`` keinen impliziten Skip-Filter für
        k-anonymisierte Clients kennt — die Entscheidung, einen Client-Proposal
        anzulegen, liegt bei der aufrufenden Stelle.
        """
        k_client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="anon-abc123def456",
            k_anonymized=True,
        )
        # Vor dem Aufruf: kein aktiver Proposal für den Client.
        assert not RetentionProposal.objects.filter(
            facility=facility,
            target_type="Client",
            target_id=k_client.pk,
        ).exists()

        # Aufruf wird *technisch* erfolgreich, legt einen Proposal an — das
        # zeigt, dass die Service-Ebene selbst keine k_anonymized-Prüfung hat.
        proposal, created = create_proposal(
            facility=facility,
            target_type="Client",
            target_id=k_client.pk,
            deletion_due_at=date.today(),
            details={"pseudonym": k_client.pseudonym},
            category="identified",
        )
        assert created is True
        assert proposal.target_id == k_client.pk
        # Erneuter Aufruf ist idempotent (Regression-Guard).
        _, created2 = create_proposal(
            facility=facility,
            target_type="Client",
            target_id=k_client.pk,
            deletion_due_at=date.today(),
            details={},
            category="identified",
        )
        assert created2 is False
