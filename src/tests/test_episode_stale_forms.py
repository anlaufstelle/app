"""Episode editing rejects obsolete state while retaining the submitted draft."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, Episode
from core.services.case import update_episode

pytestmark = pytest.mark.django_db


def episode_data(episode):
    return {
        "title": "My title",
        "description": "My submitted draft",
        "started_at": episode.started_at.isoformat(),
        "ended_at": "",
    }


def episode_url(episode):
    return reverse("core:episode_update", kwargs={"case_pk": episode.case_id, "pk": episode.pk})


def episode_token(client, episode):
    return client.get(episode_url(episode)).context["form"].initial.get("basis_token", "")


@pytest.mark.parametrize("changed", [{"description": "Other draft"}, {"ended_at": date(2026, 10, 13)}])
def test_stale_episode_retains_inputs_without_overwriting(client, staff_user, episode, changed):
    client.force_login(staff_user)
    data = episode_data(episode) | {"basis_token": episode_token(client, episode)}
    Episode.objects.filter(pk=episode.pk).update(**changed)
    before = AuditLog.objects.count()
    response = client.post(episode_url(episode), data)
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert response.context["form"]["description"].value() == "My submitted draft"
    assert "My submitted draft" in response.content.decode()
    episode.refresh_from_db()
    assert all(getattr(episode, key) == value for key, value in changed.items())
    assert AuditLog.objects.count() == before
    data["basis_token"] = response.context["form"]["basis_token"].value()
    assert client.post(episode_url(episode), data).status_code == 302
    episode.refresh_from_db()
    assert episode.description == "My submitted draft"
    assert episode.ended_at is None


@pytest.mark.parametrize("kind", ["missing", "tampered", "other_episode"])
def test_invalid_episode_basis_is_rejected(client, staff_user, episode, kind):
    client.force_login(staff_user)
    token = episode_token(client, episode)
    if kind == "missing":
        token = ""
    elif kind == "tampered":
        token += "broken"
    else:
        other = Episode.objects.create(
            case=episode.case, title="Other", started_at=episode.started_at, created_by=staff_user
        )
        token = episode_token(client, other)
    original = episode.description
    response = client.post(episode_url(episode), episode_data(episode) | {"basis_token": token})
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    episode.refresh_from_db()
    assert episode.description == original


def test_episode_service_requires_basis(episode, staff_user):
    with pytest.raises(ValidationError):
        update_episode(episode, staff_user, title="Rejected")


def test_episode_service_checks_locked_state(client, episode, staff_user):
    client.force_login(staff_user)
    token = episode_token(client, episode)
    Episode.objects.filter(pk=episode.pk).update(ended_at=date(2026, 10, 13))
    with pytest.raises(ValidationError):
        update_episode(episode, staff_user, basis_token=token, title="Rejected", ended_at=None)
    episode.refresh_from_db()
    assert episode.ended_at == date(2026, 10, 13)


def test_episode_service_returns_current_locked_object(client, episode, staff_user):
    client.force_login(staff_user)
    token = episode_token(client, episode)
    episode.description = "Unsaved value outside requested fields"
    updated = update_episode(episode, staff_user, basis_token=token, title="Saved")
    assert updated.description != episode.description
    assert updated.title == "Saved"
