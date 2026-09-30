"""Pins for the episode row lock of the episode write paths (``core.services.case.episodes``).

Source: mutation delta of the checkpoint release-0.99.5, 1 mutant.
Group:

* ``_lock_episode_for_write`` — the locked episode read loses its ``pk``
  condition and returns the first episode of the case instead of the
  addressed one.

The mutant only shows once a case holds a second episode. The neighbouring
episode is therefore the one that comes first without the ``pk`` condition:
it starts later (the model ordering ``-started_at`` puts it in front) and
carries the smaller primary key. Besides the outcome of ``close_episode`` and
``update_episode``, the pins record which episode row the service locks
(``tests._row_lock_helpers``: PostgreSQL plans the captured statement; what
counts is the locked table, the lock strength and the filter values per
column, not alias, quoting, projection, ordering or literal form).
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Episode
from core.services.case.episodes import close_episode, update_episode
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

EPISODE_TABLE = "core_episode"


@pytest.fixture
def target_and_neighbour(case_open, staff_user):
    """Addressed episode plus a neighbour that comes first by ``pk`` and by ``-started_at``."""
    today = timezone.localdate()
    neighbour = Episode.objects.create(
        id=uuid.UUID(int=1),
        case=case_open,
        title="Neighbour",
        started_at=today,
        created_by=staff_user,
    )
    target = Episode.objects.create(
        id=uuid.UUID(int=2**128 - 1),
        case=case_open,
        title="Target",
        started_at=today - timedelta(days=10),
        created_by=staff_user,
    )
    assert list(Episode.objects.filter(case=case_open)) == [neighbour, target]
    return target, neighbour


def _assert_locks_address(queries, episode):
    """Every episode lock addresses the episode's ``pk``; one of them also its case.

    A later lock on the ``pk`` alone (the change diff of ``update_episode``)
    stays valid, as does an additional redundant lock on the same row.
    """
    locks = queries.sperren(EPISODE_TABLE)
    assert locks
    assert all(lock.traegt(id=[episode.pk]) for lock in locks), locks
    assert any(lock.traegt(id=[episode.pk], case_id=[episode.case_id]) for lock in locks), locks


class TestLockEpisodeForWrite:
    """``_lock_episode_for_write`` as ``close_episode``/``update_episode`` use it."""

    def test_close_ends_only_the_addressed_episode(self, target_and_neighbour, staff_user):
        """x__lock_episode_for_write__mutmut_5: without ``pk`` the locked read
        returned the neighbouring episode, so ``close_episode`` ended the
        neighbour and left the addressed episode open."""
        target, neighbour = target_and_neighbour
        with mitschnitt() as queries:
            result = close_episode(target, staff_user)
        assert result.pk == target.pk
        target.refresh_from_db()
        neighbour.refresh_from_db()
        assert target.ended_at is not None
        assert neighbour.ended_at is None
        _assert_locks_address(queries, target)

    def test_trashed_neighbour_does_not_block_the_addressed_episode(self, target_and_neighbour, staff_user):
        """x__lock_episode_for_write__mutmut_5: without ``pk`` the locked read
        returned the trashed neighbour and refused the edit of the addressed
        episode as gone."""
        target, neighbour = target_and_neighbour
        Episode.objects.filter(pk=neighbour.pk).update(is_deleted=True, deleted_at=timezone.now())
        with mitschnitt() as queries:
            update_episode(target, staff_user, title="Renamed")
        target.refresh_from_db()
        neighbour.refresh_from_db()
        assert target.title == "Renamed"
        assert neighbour.title == "Neighbour"
        _assert_locks_address(queries, target)
