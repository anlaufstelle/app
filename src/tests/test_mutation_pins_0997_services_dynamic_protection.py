"""Missing-facility validation at the public multi-facility protection gate.

Source: mutation delta of the release-0.99.7 checkpoint, 1 mutant.
Groups: missing facility error message (1).
"""

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from core.models import Facility
from core.services.dynamic_protection import lock_dynamic_protection_many

pytestmark = [pytest.mark.django_db, pytest.mark.postgres]


def test_missing_facility_gate_explains_the_validation_failure():
    """The gate rejects a missing facility with its translated explanatory message.

    x_lock_dynamic_protection_many__mutmut_44.
    """
    missing_id = uuid4()
    assert not Facility.objects.filter(pk=missing_id).exists()
    with transaction.atomic(), pytest.raises(ValidationError) as error:
        lock_dynamic_protection_many([missing_id])
    assert error.value.messages == [_("Die Einrichtung ist nicht mehr vorhanden.")]
