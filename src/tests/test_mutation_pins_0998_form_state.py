"""Pins for signed form baselines.

Source: mutation delta of the release-0.99.8 checkpoint, 5 mutants.
Groups: field ordering (3), object identity (1), conflict explanation (1).
"""

import pytest
from django.utils.translation import gettext

from core.models import Episode
from core.services.form_state import FormStateConflictError, require_form_state, sign_form_state

pytestmark = pytest.mark.django_db
FIELDS = ("title", "description")
SALT = "baseline-test"


def test_field_iteration_order_does_not_invalidate_baseline(episode, facility):
    """An unchanged object accepts reversed field order.

    x__payload__mutmut_4, x__payload__mutmut_8, x__payload__mutmut_14.
    """
    token = sign_form_state(episode, fields=FIELDS, facility_id=facility.pk, salt=SALT)
    require_form_state(token, episode, fields=tuple(reversed(FIELDS)), facility_id=facility.pk, salt=SALT)


def test_identical_editable_values_do_not_share_object_identity(episode, facility, staff_user):
    """Equal editable values on another object cannot reuse a token.

    x__payload__mutmut_19.
    """
    other = Episode.objects.create(
        case=episode.case,
        title=episode.title,
        description=episode.description,
        started_at=episode.started_at,
        created_by=staff_user,
    )
    token = sign_form_state(episode, fields=FIELDS, facility_id=facility.pk, salt=SALT)
    with pytest.raises(FormStateConflictError) as error:
        require_form_state(token, other, fields=FIELDS, facility_id=facility.pk, salt=SALT)
    assert error.value.current is other
    other.refresh_from_db()
    assert (other.title, other.description) == (episode.title, episode.description)


def test_invalid_token_explains_recovery(episode, facility):
    """An invalid baseline explains the conflict and preserves the current object.

    xǁFormStateConflictErrorǁ__init____mutmut_2.
    """
    with pytest.raises(FormStateConflictError) as error:
        require_form_state("invalid", episode, fields=FIELDS, facility_id=facility.pk, salt=SALT)
    assert error.value.messages == [
        gettext(
            "Der Formularstand ist veraltet oder ungültig. Es wurde nichts gespeichert. "
            "Ihre Eingaben bleiben erhalten. Prüfen Sie den aktuellen Stand und senden Sie bewusst erneut ab."
        )
    ]
    assert error.value.current is episode
