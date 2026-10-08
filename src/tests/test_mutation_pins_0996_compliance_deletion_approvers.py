"""Pins for the action hint of the approver card (``core.services.compliance.deletion_approvers``).

Source: mutation delta of the release-0.99.6 checkpoint, 3 mutants.
Group:

* ``_deletion_approver_checks`` — a critical or warning card tells the admin
  where to grant the right ``Löschbestätigung``; the green card has no hint.
"""

from __future__ import annotations

import pytest
from django.utils.translation import gettext as _

from core.models import User
from core.services import compliance
from core.services.compliance import ComplianceStatus

pytestmark = pytest.mark.django_db

_GRANT_HINT = "Verwaltung → Zugänge öffnen und mindestens zwei aktiven Konten das Recht „Löschbestätigung“ geben."


def _approver(facility, username):
    return User.objects.create_user(
        username=username,
        role=User.Role.LEAD,
        facility=facility,
        is_active=True,
        can_confirm_deletion=True,
    )


def _approver_card():
    cards = [c for c in compliance.aggregate_checks() if c.key == "deletion_approvers"]
    assert len(cards) == 1
    return cards[0]


class TestApproverCardActionHint:
    def test_critical_card_points_to_the_access_settings(self, facility):
        """Without any approver the critical card carries the grant hint.

        Kills ``x__deletion_approver_checks__mutmut_72`` (hint ``None``),
        ``x__deletion_approver_checks__mutmut_79`` (hint argument dropped) and
        ``x__deletion_approver_checks__mutmut_84`` (hint only on the green card).
        """
        card = _approver_card()

        assert card.status == ComplianceStatus.CRITICAL
        assert card.action_hint == _(_GRANT_HINT)

    def test_warning_card_points_to_the_access_settings(self, facility):
        """With a single approver the warning card carries the grant hint.

        Kills ``x__deletion_approver_checks__mutmut_72``,
        ``x__deletion_approver_checks__mutmut_79`` and
        ``x__deletion_approver_checks__mutmut_84``.
        """
        _approver(facility, "only_approver")

        card = _approver_card()

        assert card.status == ComplianceStatus.WARNING
        assert card.action_hint == _(_GRANT_HINT)

    def test_ok_card_has_no_hint(self, facility):
        """With two approvers the green card has no action hint.

        Kills ``x__deletion_approver_checks__mutmut_84`` (inverted condition puts the hint on the green card).
        """
        _approver(facility, "first_approver")
        _approver(facility, "second_approver")

        card = _approver_card()

        assert card.status == ComplianceStatus.OK
        assert card.action_hint is None
