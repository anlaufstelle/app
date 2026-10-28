"""Pin core corrections after a default profile becomes available.

Source: mutation delta of the release-0.99.9 checkpoint, 2 mutants.
Groups: core correction context (1), redundant validation (1, not pinned).
"""

import pytest

from core.models import DocumentType, RectificationRequest
from core.services.client import rectification

pytestmark = pytest.mark.django_db


def test_core_correction_can_assign_a_new_default_profile(client_identified, facility, staff_user, lead_user):
    """A core correction still succeeds when the sole profile was added later.

    x__apply_rectification__mutmut_15.
    """
    request = rectification.request_rectification(client_identified, staff_user, "pseudonym", "Corrected", "Correction")
    doc = DocumentType.objects.create(facility=facility, name="New profile", entity_type=DocumentType.EntityType.CLIENT)

    rectification.approve_rectification(request, lead_user)

    client_identified.refresh_from_db()
    request.refresh_from_db()
    assert client_identified.pseudonym == "Corrected"
    assert client_identified.document_type == doc
    assert request.status == RectificationRequest.Status.APPROVED
    assert request.reviewed_by == lead_user
