"""Pins for date validation on partial episode updates.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: stored start date fallback (2).
Follows test_mutation_pins_0997_case_cases.py.
"""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import AuditLog, Episode
from core.services.case.episodes import episode_basis_token, update_episode

pytestmark = pytest.mark.django_db


def test_partial_end_update_checks_the_stored_start_without_audit(episode, staff_user):
    """Updating only the end cannot precede the locked start or leave an audit.

    Kills x_update_episode__mutmut_25 and x_update_episode__mutmut_27.
    """
    Episode.objects.filter(pk=episode.pk).update(started_at=date(2026, 6, 15), ended_at=date(2026, 6, 17))
    episode.refresh_from_db()
    token = episode_basis_token(episode)
    audit_ids = set(AuditLog.objects.values_list("pk", flat=True))
    episode.started_at = date(2026, 6, 1)
    with pytest.raises(ValidationError) as error:
        update_episode(episode, staff_user, basis_token=token, ended_at=date(2026, 6, 14))
    assert error.value.message_dict == {"ended_at": [_("Das Ende darf nicht vor dem Beginn liegen.")]}
    episode.refresh_from_db()
    assert (episode.started_at, episode.ended_at) == (date(2026, 6, 15), date(2026, 6, 17))
    assert set(AuditLog.objects.values_list("pk", flat=True)) == audit_ids
