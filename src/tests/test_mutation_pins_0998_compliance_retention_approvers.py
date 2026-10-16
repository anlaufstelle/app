"""Pins for exact retention approver card labels and staffing messages.

Source: mutation delta of the release-0.99.8 checkpoint, 3 mutants.
Groups: success text belongs only to OK (1), translated label (1), complete staffing messages (1).
"""

import pytest
from django.utils.translation import gettext as _

from core.models import User
from core.services.compliance import ComplianceStatus, aggregate_checks

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "count,status",
    [(0, ComplianceStatus.CRITICAL), (1, ComplianceStatus.WARNING), (2, ComplianceStatus.OK)],
    ids=["none", "one", "two"],
)
def test_public_approver_card_explains_its_actual_staffing(facility, count, status):
    """Each staffing state has its own exact message and label:
    x__retention_approver_checks__mutmut_62, x__retention_approver_checks__mutmut_69,
    x__retention_approver_checks__mutmut_72.
    """
    for index in range(count):
        User.objects.create_user(
            username=f"reviewer-{index}",
            facility=facility,
            role=User.Role.LEAD,
            can_confirm_deletion=True,
        )

    card = next(card for card in aggregate_checks() if card.key == "retention_approvers")

    messages = [
        _("Ohne Genehmiger für den Aufbewahrungslauf: %(names)s.") % {"names": facility.name},
        _("Nur ein Genehmiger für den Aufbewahrungslauf, keine manuelle Löschfreigabe möglich: %(names)s.")
        % {"names": facility.name},
        _("Jede Einrichtung hat mindestens zwei aktive Genehmiger für den Aufbewahrungslauf."),
    ]
    assert card.label == _("Genehmiger für den Aufbewahrungslauf")
    assert card.status == status
    assert card.message == messages[count]
