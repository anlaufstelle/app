"""Tests fuer Cross-Facility-Retention im ``/system/``-Areal (Refs #875).

Enthaelt die Cluster:

* ``TestSystemRetentionAccess`` — Zugriffsschutz fuer ``/system/retention/``.
* ``TestSystemRetentionAggregation`` — Aggregations-Logik der Retention-View.

Die Fixtures (``retention_proposal_*``) sind hier inline gehalten, da sie nur
von den Tests in dieser Datei genutzt werden (Split aus ``test_system_views.py``).
"""

import uuid
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models.retention import RetentionProposal

# ---------------------------------------------------------------------------
# Tier 2: Cross-Facility-Retention (Refs #875)
# ---------------------------------------------------------------------------


@pytest.fixture
def retention_proposal_pending_overdue(facility, sample_event):
    """PENDING-Proposal in Facility, deletion_due_at in der Vergangenheit."""
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=sample_event.pk,
        deletion_due_at=date.today() - timedelta(days=5),
        status=RetentionProposal.Status.PENDING,
        retention_category="anonymous",
    )


@pytest.fixture
def retention_proposal_pending_future(facility, client_identified, staff_user, doc_type_contact):
    """PENDING-Proposal in Facility, deletion_due_at in der Zukunft."""
    from core.models import Event

    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=200),
        data_json={"dauer": 5},
        created_by=staff_user,
    )
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=event.pk,
        deletion_due_at=date.today() + timedelta(days=10),
        status=RetentionProposal.Status.PENDING,
        retention_category="identified",
    )


@pytest.fixture
def retention_proposal_second_facility(second_facility, second_facility_user):
    """PENDING-Proposal in second_facility — fuer Cross-Facility-Test."""
    return RetentionProposal.objects.create(
        facility=second_facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=uuid.uuid4(),
        deletion_due_at=date.today() + timedelta(days=20),
        status=RetentionProposal.Status.APPROVED,
        retention_category="qualified",
    )


@pytest.mark.django_db
class TestSystemRetentionAccess:
    """``GET /system/retention/`` — Zugriffsschutz."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 403

    def test_super_admin_can_access(self, client, super_admin_user, facility):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200
        # Banner + Subnav-Link "Retention".
        content = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content
        assert reverse("core:system_retention") in content


@pytest.mark.django_db
class TestSystemRetentionAggregation:
    """Aggregations-Logik der Retention-View."""

    def test_pending_count_and_overdue(
        self,
        client,
        super_admin_user,
        facility,
        retention_proposal_pending_overdue,
        retention_proposal_pending_future,
    ):
        """Zwei PENDING in einer Facility — eines ueberfaellig, eines nicht.

        Erwartet: PENDING-Count=2, overdue_count=1, is_critical=True.
        """
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        # Ein Row pro Facility.
        row = next(r for r in rows if r["facility"].pk == facility.pk)
        assert row["count_pending"] == 2
        assert row["overdue_count"] == 1
        assert row["is_critical"] is True
        # next_due_date = min(deletion_due_at) der PENDING — der ueberfaellige.
        assert row["next_due_date"] == retention_proposal_pending_overdue.deletion_due_at

    def test_status_counts_pivot(self, client, super_admin_user, facility, sample_event):
        """Verschiedene Status werden korrekt pivotiert."""
        # Approved
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.APPROVED,
            retention_category="anonymous",
        )
        # Held
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.HELD,
            retention_category="anonymous",
        )
        # Deferred
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.DEFERRED,
            retention_category="anonymous",
        )
        # Rejected (status=REJECTED nicht im unique_active-Set, geht nochmal)
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.REJECTED,
            retention_category="anonymous",
        )
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        row = next(r for r in rows if r["facility"].pk == facility.pk)
        assert row["count_approved"] == 1
        assert row["count_held"] == 1
        assert row["count_deferred"] == 1
        assert row["count_rejected"] == 1

    def test_cross_facility_aggregation(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        retention_proposal_pending_overdue,
        retention_proposal_second_facility,
    ):
        """Beide Facilities tauchen in der Liste auf, mit getrennten Counts."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        # Beide Facilities vorhanden.
        facility_ids = {r["facility"].pk for r in rows}
        assert facility.pk in facility_ids
        assert second_facility.pk in facility_ids

        first = next(r for r in rows if r["facility"].pk == facility.pk)
        second = next(r for r in rows if r["facility"].pk == second_facility.pk)

        # Erste Facility: 1 PENDING (ueberfaellig), 0 APPROVED
        assert first["count_pending"] == 1
        assert first["count_approved"] == 0
        # Zweite Facility: 0 PENDING, 1 APPROVED
        assert second["count_pending"] == 0
        assert second["count_approved"] == 1

    def test_facility_without_proposals_listed(self, client, super_admin_user, facility, second_facility):
        """Auch Facilities ohne Proposals tauchen in der Liste auf (mit Nullen)."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        assert len(rows) >= 2
        for row in rows:
            assert row["count_pending"] == 0
            assert row["overdue_count"] == 0
            assert row["is_critical"] is False
            assert row["next_due_date"] is None

    def test_totals_aggregate(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        retention_proposal_pending_overdue,
        retention_proposal_pending_future,
        retention_proposal_second_facility,
    ):
        """Summen-Zeile addiert ueber alle Facilities."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        totals = response.context["totals"]
        assert totals["count_pending"] == 2  # beide in facility
        assert totals["count_approved"] == 1  # in second_facility
        assert totals["overdue_count"] == 1
