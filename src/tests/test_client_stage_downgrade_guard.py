"""Security N2: only lead/admin may downgrade a QUALIFIED client's contact_stage.

The four-eyes DeletionRequest that protects qualified (Art-9-adjacent)
documentation is gated on the client being ``QUALIFIED`` at delete time. A
single staff member could otherwise downgrade the client to ``IDENTIFIED``
first and then soft-delete its events unilaterally, defeating the control. The
guard lives in the service layer (``update_client``, the SSOT), so any
non-form caller (offline replay) is covered too — not only ``ClientUpdateView``.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import Client
from core.services.client import update_client

pytestmark = pytest.mark.django_db


def test_staff_cannot_downgrade_qualified_client(client_qualified, staff_user):
    with pytest.raises(ValidationError):
        update_client(
            client_qualified,
            staff_user,
            old_stage=Client.ContactStage.QUALIFIED,
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
    client_qualified.refresh_from_db()
    assert client_qualified.contact_stage == Client.ContactStage.QUALIFIED


def test_lead_may_downgrade_qualified_client(client_qualified, lead_user):
    update_client(
        client_qualified,
        lead_user,
        old_stage=Client.ContactStage.QUALIFIED,
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    client_qualified.refresh_from_db()
    assert client_qualified.contact_stage == Client.ContactStage.IDENTIFIED


def test_staff_may_upgrade_identified_to_qualified(client_identified, staff_user):
    # Only DOWNGRADE from QUALIFIED is restricted — qualifying a person stays open to staff.
    update_client(
        client_identified,
        staff_user,
        old_stage=Client.ContactStage.IDENTIFIED,
        contact_stage=Client.ContactStage.QUALIFIED,
    )
    client_identified.refresh_from_db()
    assert client_identified.contact_stage == Client.ContactStage.QUALIFIED


def test_staff_may_edit_nonstage_fields_on_qualified_client(client_qualified, staff_user):
    # A non-stage edit must not be blocked by the downgrade guard.
    update_client(
        client_qualified,
        staff_user,
        old_stage=Client.ContactStage.QUALIFIED,
        contact_stage=Client.ContactStage.QUALIFIED,
        notes="aktualisiert",
    )
    client_qualified.refresh_from_db()
    assert client_qualified.contact_stage == Client.ContactStage.QUALIFIED
    assert client_qualified.notes == "aktualisiert"
