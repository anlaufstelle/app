"""Tests for Episode model methods and views."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client
from core.models.episode import Episode


def _other_facility_case(other_facility):
    """Create a Case in ``other_facility`` together with a matching Client.

    Refs #748: Case.client is now mandatory, so cross-facility scoping
    tests need a client in the other facility too.
    """
    other_client = Client.objects.create(
        facility=other_facility,
        pseudonym="Fremd-Person-01",
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    return Case.objects.create(
        facility=other_facility,
        client=other_client,
        title="Anderer Fall",
        status=Case.Status.OPEN,
    )


@pytest.mark.django_db
class TestCaseCreateEpisode:
    """``Case.create_episode`` ersetzt ``services/episodes.create_episode`` (Refs #958)."""

    def test_create_episode(self, case_open, staff_user):
        episode = case_open.create_episode(
            user=staff_user,
            title="Erste Episode",
            description="Beschreibung",
            started_at=timezone.now().date(),
        )
        assert episode.pk is not None
        assert episode.title == "Erste Episode"
        assert episode.case == case_open
        assert episode.created_by == staff_user
        assert episode.ended_at is None

    def test_create_episode_validates_case_open(self, case_closed, staff_user):
        with pytest.raises(ValueError, match="offene Fälle"):
            case_closed.create_episode(
                user=staff_user,
                title="Ungültig",
                started_at=timezone.now().date(),
            )

    def test_create_episode_default_started_at(self, case_open, staff_user):
        episode = case_open.create_episode(user=staff_user, title="Default-Datum")
        assert episode.started_at == timezone.now().date()


@pytest.mark.django_db
class TestEpisodeClose:
    """``Episode.close`` ersetzt ``services/episodes.close_episode`` (Refs #958)."""

    def test_close_sets_ended_at_to_today(self, episode):
        closed = episode.close()
        assert closed.ended_at == timezone.now().date()

    def test_close_with_explicit_date(self, episode):
        custom_date = datetime.date(2025, 6, 15)
        closed = episode.close(ended_at=custom_date)
        assert closed.ended_at == custom_date

    def test_close_is_idempotent(self, episode):
        first = episode.close(ended_at=datetime.date(2025, 6, 1))
        second = first.close(ended_at=datetime.date(2025, 7, 1))
        assert second.ended_at == datetime.date(2025, 6, 1)


@pytest.mark.django_db
class TestEpisodeCreateView:
    def test_episode_create_form_renders(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:episode_create", kwargs={"case_pk": case_open.pk}))
        assert response.status_code == 200
        assert "Neue Episode" in response.content.decode()

    def test_episode_create_post(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:episode_create", kwargs={"case_pk": case_open.pk}),
            {
                "title": "Neue Episode",
                "description": "Testbeschreibung",
                "started_at": "2025-01-15",
            },
        )
        assert response.status_code == 302
        assert Episode.objects.filter(title="Neue Episode").exists()

    def test_episode_create_auth_required(self, client, assistant_user, case_open):
        client.force_login(assistant_user)
        response = client.get(reverse("core:episode_create", kwargs={"case_pk": case_open.pk}))
        assert response.status_code == 403

    def test_episode_create_facility_scoping(self, client, staff_user, other_facility):
        other_case = _other_facility_case(other_facility)
        client.force_login(staff_user)
        response = client.get(reverse("core:episode_create", kwargs={"case_pk": other_case.pk}))
        assert response.status_code == 404

    def test_episode_create_closed_case_redirects(self, client, staff_user, case_closed):
        client.force_login(staff_user)
        response = client.get(reverse("core:episode_create", kwargs={"case_pk": case_closed.pk}))
        assert response.status_code == 302


@pytest.mark.django_db
class TestEpisodeUpdateView:
    def test_episode_update_form_renders(self, client, staff_user, case_open, episode):
        client.force_login(staff_user)
        response = client.get(
            reverse(
                "core:episode_update",
                kwargs={"case_pk": case_open.pk, "pk": episode.pk},
            )
        )
        assert response.status_code == 200
        assert "Episode bearbeiten" in response.content.decode()

    def test_episode_update_post(self, client, staff_user, case_open, episode):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:episode_update",
                kwargs={"case_pk": case_open.pk, "pk": episode.pk},
            ),
            {
                "title": "Aktualisierte Episode",
                "description": "Neue Beschreibung",
                "started_at": "2025-03-01",
            },
        )
        assert response.status_code == 302
        episode.refresh_from_db()
        assert episode.title == "Aktualisierte Episode"

    def test_episode_update_facility_scoping(self, client, staff_user, other_facility):
        other_case = _other_facility_case(other_facility)
        other_episode = Episode.objects.create(
            case=other_case,
            title="Andere Episode",
            started_at=timezone.now().date(),
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(
            reverse(
                "core:episode_update",
                kwargs={"case_pk": other_case.pk, "pk": other_episode.pk},
            )
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestEpisodeCloseView:
    def test_episode_close_post(self, client, staff_user, case_open, episode):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:episode_close",
                kwargs={"case_pk": case_open.pk, "pk": episode.pk},
            )
        )
        assert response.status_code == 302
        episode.refresh_from_db()
        assert episode.ended_at is not None

    def test_episode_close_sets_ended_at(self, client, staff_user, case_open, episode):
        client.force_login(staff_user)
        client.post(
            reverse(
                "core:episode_close",
                kwargs={"case_pk": case_open.pk, "pk": episode.pk},
            )
        )
        episode.refresh_from_db()
        assert episode.ended_at == timezone.now().date()

    def test_episode_close_facility_scoping(self, client, staff_user, other_facility):
        other_case = _other_facility_case(other_facility)
        other_episode = Episode.objects.create(
            case=other_case,
            title="Andere Episode",
            started_at=timezone.now().date(),
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:episode_close",
                kwargs={"case_pk": other_case.pk, "pk": other_episode.pk},
            )
        )
        assert response.status_code == 404
