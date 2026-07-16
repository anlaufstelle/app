"""Retention proposal card status badges route through ``{% status_badge %}`` (COD-03).

``retention/partials/proposal_card.html`` hard-coded the label "Aufgeschoben"
for ``status == 'held'``, while the model defines
``RetentionProposal.Status.HELD = "Legal Hold"`` — a legally held record was
shown as merely postponed. The pending badge also drifted to ``bg-yellow-100``
instead of the amber palette color used everywhere else. The badges now route
through the shared ``{% status_badge %}`` tag / ``_STATUS_COLOR_MAP``.
Refs #1478 (COD-03).
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string

from core.models import RetentionProposal


def _make_proposal(facility, status):
    proposal = RetentionProposal(
        facility=facility,
        target_type="Event",
        target_id=facility.pk,  # any UUID-shaped value; not dereferenced here
        deletion_due_at="2026-01-01",
        status=status,
        retention_category="identified",
    )
    # Annotations the view attaches at runtime.
    proposal.urgency = None
    proposal.active_hold = None
    return proposal


@pytest.mark.django_db
class TestProposalCardStatusBadge:
    def test_held_renders_legal_hold_not_aufgeschoben(self, facility):
        proposal = _make_proposal(facility, RetentionProposal.Status.HELD)
        html = render_to_string("core/retention/partials/proposal_card.html", {"proposal": proposal})
        assert "Legal Hold" in html
        assert "Aufgeschoben" not in html

    def test_pending_badge_uses_amber_palette_not_raw_yellow(self, facility):
        proposal = _make_proposal(facility, RetentionProposal.Status.PENDING)
        html = render_to_string("core/retention/partials/proposal_card.html", {"proposal": proposal})
        assert "Ausstehend" in html
        assert "bg-amber-100" in html
        assert "bg-yellow-100" not in html

    def test_approved_badge_keeps_green_palette(self, facility):
        proposal = _make_proposal(facility, RetentionProposal.Status.APPROVED)
        html = render_to_string("core/retention/partials/proposal_card.html", {"proposal": proposal})
        assert "Freigegeben" in html
        assert "bg-green-100" in html
