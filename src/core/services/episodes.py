"""Service layer for Episode CRUD."""

import logging

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.case import Case
from core.models.episode import Episode

logger = logging.getLogger(__name__)


@transaction.atomic
def create_episode(case, user, title, description="", started_at=None):
    """Create a new Episode. Case must be OPEN."""
    if case.status != Case.Status.OPEN:
        raise ValueError(_("Episoden können nur für offene Fälle erstellt werden."))

    if started_at is None:
        started_at = timezone.now().date()

    episode = Episode(
        case=case,
        title=title,
        description=description,
        started_at=started_at,
        created_by=user,
    )
    episode.save()
    return episode


@transaction.atomic
def update_episode(episode, user, **fields):
    """Update mutable fields on an episode (title, description, started_at, ended_at)."""
    allowed = {"title", "description", "started_at", "ended_at"}
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")
        setattr(episode, key, value)
    episode.save()
    return episode


@transaction.atomic
def close_episode(episode, user, ended_at=None):
    """Close an episode by setting ended_at. Idempotent: no-op if already closed."""
    if episode.ended_at is not None:
        return episode  # already closed — idempotent
    if ended_at is None:
        ended_at = timezone.now().date()
    episode.ended_at = ended_at
    episode.save()
    return episode
