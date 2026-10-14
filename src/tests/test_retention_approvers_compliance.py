"""Retention approver cards count only active eligible facility reviewers."""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import User
from core.services.compliance import ComplianceStatus, aggregate_checks
from core.services.retention import approve_proposal, build_retention_dashboard_context, create_proposal

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "count,status", [(0, ComplianceStatus.CRITICAL), (1, ComplianceStatus.WARNING), (2, ComplianceStatus.OK)]
)
def test_retention_card_counts_eligible_roles(facility, count, status):
    for index in range(count):
        User.objects.create_user(
            username=f"reviewer-{index}",
            facility=facility,
            role=User.Role.LEAD if index == 0 else User.Role.FACILITY_ADMIN,
            can_confirm_deletion=True,
        )
    cards = {card.key: card for card in aggregate_checks()}
    assert "retention_approvers" in cards
    card = cards["retention_approvers"]
    assert card.status == status
    assert card.detail == f"{facility.name}: {count}"
    assert (card.action_hint is None) == (count >= 2)


@pytest.mark.parametrize(
    "role,active,right",
    [
        (User.Role.STAFF, True, True),
        (User.Role.ASSISTANT, True, True),
        (User.Role.LEAD, False, True),
        (User.Role.LEAD, True, False),
    ],
)
def test_retention_card_excludes_ineligible_accounts(facility, role, active, right):
    User.objects.create_user(
        username="excluded", facility=facility, role=role, is_active=active, can_confirm_deletion=right
    )
    cards = {card.key: card for card in aggregate_checks()}
    assert "retention_approvers" in cards
    assert cards["retention_approvers"].status == ComplianceStatus.CRITICAL
    if active and right:
        assert cards["deletion_approvers"].status == ComplianceStatus.WARNING


def test_foreign_reviewers_do_not_cover_empty_facility(facility, second_facility):
    for index in range(2):
        User.objects.create_user(
            username=f"foreign-{index}", facility=second_facility, role=User.Role.LEAD, can_confirm_deletion=True
        )
    cards = {card.key: card for card in aggregate_checks()}
    assert "retention_approvers" in cards
    card = cards["retention_approvers"]
    assert card.status == ComplianceStatus.CRITICAL
    assert f"{facility.name}: 0" in card.detail
    assert f"{second_facility.name}: 2" in card.detail


def test_dashboard_shows_one_of_two_and_small_pool(facility, lead_user, client):
    proposal = create_proposal(facility, "Event", uuid.uuid4(), timezone.localdate(), {}, "qualified")[0]
    approve_proposal(proposal, lead_user)
    context = build_retention_dashboard_context(facility, lead_user)
    assert "first_approval_count" in context
    assert context["first_approval_count"] == 1
    assert context["pending_count"] == 1
    assert context["approved_count"] == 0
    client.force_login(lead_user)
    for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
        response = client.get(reverse("core:retention_dashboard"), **headers)
        assert response.status_code == 200
        text = response.content.decode()
        assert "1 von 2 Freigaben" in text
        assert "mindestens zwei aktive Konten" in text
        assert "keine manuelle Löschfreigabe möglich" in text
