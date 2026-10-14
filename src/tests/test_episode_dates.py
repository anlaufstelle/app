"""Episode end dates survive creation and obey chronological ordering."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Episode
from core.services.case.episodes import close_episode, create_episode, episode_basis_token, update_episode

pytestmark = pytest.mark.django_db


def url(name, case, episode=None):
    kwargs = {"case_pk": case.pk}
    if episode:
        kwargs["pk"] = episode.pk
    return reverse(f"core:episode_{name}", kwargs=kwargs)


@pytest.mark.parametrize("end", ["2026-01-02", ""])
def test_create_end_survives_reopening(client, staff_user, case_open, end):
    client.force_login(staff_user)
    data = {"title": "Phase", "description": "Draft", "started_at": "2026-01-01", "ended_at": end}
    response = client.post(url("create", case_open), data)
    assert response.status_code == 302
    episode = Episode.objects.get(case=case_open)
    assert episode.ended_at == (date(2026, 1, 2) if end else None)
    form = client.get(url("update", case_open, episode)).context["form"]
    assert form.initial["started_at"] == date(2026, 1, 1)
    assert form.initial["ended_at"] == episode.ended_at
    assert form.initial["title"] == data["title"]
    assert form.initial["description"] == data["description"]
    listing = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk})).content.decode()
    assert ("abgeschlossen" if end else "aktiv") in listing


@pytest.mark.parametrize("editing", [False, True])
def test_invalid_dates_retain_every_input(client, staff_user, case_open, episode, editing):
    client.force_login(staff_user)
    data = {"title": "Draft title", "description": "Draft body", "started_at": "2026-02-02", "ended_at": "2026-02-01"}
    if editing:
        data["basis_token"] = episode_basis_token(episode)
    count = Episode.objects.count()
    original = (episode.title, episode.started_at, episode.ended_at)
    response = client.post(url("update" if editing else "create", case_open, episode if editing else None), data)
    assert response.status_code == 200
    form = response.context["form"]
    assert "ended_at" in form.errors
    assert all(form[key].value() == value for key, value in data.items())
    assert Episode.objects.count() == count
    episode.refresh_from_db()
    assert (episode.title, episode.started_at, episode.ended_at) == original


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("invalid", [True, False])
def test_service_checks_resulting_dates(case_open, episode, staff_user, operation, invalid):
    start = date(2026, 2, 2)
    end = start - timedelta(days=1) if invalid else start

    def write():
        if operation == "create":
            return create_episode(case_open, staff_user, title="Phase", started_at=start, ended_at=end)
        return update_episode(
            episode, staff_user, basis_token=episode_basis_token(episode), started_at=start, ended_at=end
        )

    original = (episode.title, episode.started_at, episode.ended_at)
    count = Episode.objects.count()
    if invalid:
        with pytest.raises(ValidationError) as caught:
            write()
        assert "ended_at" in caught.value.message_dict
        episode.refresh_from_db()
        assert (episode.title, episode.started_at, episode.ended_at) == original
        assert Episode.objects.count() == count
    else:
        assert write().ended_at == start


@pytest.mark.parametrize("direct", [False, True])
def test_future_start_cannot_close(episode, staff_user, direct):
    episode.started_at = timezone.now().date() + timedelta(days=1)
    episode.save(update_fields=["started_at"])
    with pytest.raises(ValidationError, match="Beginn"):
        episode.close() if direct else close_episode(episode, staff_user)
    episode.refresh_from_db()
    assert episode.ended_at is None


@pytest.mark.parametrize("editing", [False, True])
def test_service_date_rejection_is_a_field_error(client, staff_user, case_open, episode, editing):
    client.force_login(staff_user)
    data = {"title": "Draft", "description": "Retained", "started_at": "2026-02-02", "ended_at": "2026-02-02"}
    if editing:
        data["basis_token"] = episode_basis_token(episode)
    service = "update_episode" if editing else "create_episode"
    with patch(f"core.views.case_episodes.{service}", side_effect=ValidationError({"ended_at": "Date rejected"})):
        response = client.post(url("update" if editing else "create", case_open, episode if editing else None), data)
    assert response.status_code == 200
    assert "ended_at" in response.context["form"].errors
    assert response.context["form"]["description"].value() == "Retained"


def test_future_start_close_view_reports_rejection(client, episode, staff_user):
    client.force_login(staff_user)
    episode.started_at = timezone.now().date() + timedelta(days=1)
    episode.save(update_fields=["started_at"])
    response = client.post(url("close", episode.case, episode), follow=True)
    assert response.status_code == 200
    assert "Das Ende darf nicht vor dem Beginn liegen." in response.content.decode()
    episode.refresh_from_db()
    assert episode.ended_at is None


def test_invalid_existing_dates_can_be_corrected(client, episode, staff_user):
    client.force_login(staff_user)
    Episode.objects.filter(pk=episode.pk).update(started_at=date(2026, 2, 2), ended_at=date(2026, 2, 1))
    episode.refresh_from_db()
    data = {
        "title": episode.title,
        "description": "Correction",
        "started_at": "2026-02-02",
        "ended_at": "2026-02-01",
        "basis_token": episode_basis_token(episode),
    }
    response = client.post(url("update", episode.case, episode), data)
    assert "ended_at" in response.context["form"].errors
    episode.refresh_from_db()
    assert episode.ended_at == date(2026, 2, 1)
    data["ended_at"] = "2026-02-02"
    assert client.post(url("update", episode.case, episode), data).status_code == 302
    episode.refresh_from_db()
    assert episode.ended_at == date(2026, 2, 2)


def test_partial_service_update_uses_locked_result(episode, staff_user):
    Episode.objects.filter(pk=episode.pk).update(started_at=date(2026, 2, 2), ended_at=date(2026, 2, 3))
    episode.refresh_from_db()
    token = episode_basis_token(episode)
    episode.ended_at = date(2026, 2, 10)
    with pytest.raises(ValidationError):
        update_episode(episode, staff_user, basis_token=token, started_at=date(2026, 2, 4))
    episode.refresh_from_db()
    assert episode.started_at == date(2026, 2, 2)


def test_future_end_remains_allowed(case_open, staff_user):
    start = timezone.now().date() + timedelta(days=1)
    episode = create_episode(
        case_open, staff_user, title="Future", started_at=start, ended_at=start + timedelta(days=1)
    )
    assert episode.ended_at > timezone.now().date()
