"""Tests for Episode model methods and views."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client
from core.models.episode import Episode
from core.services.case.episodes import episode_basis_token


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
            started_at=timezone.localdate(),
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
                started_at=timezone.localdate(),
            )

    def test_create_episode_default_started_at(self, case_open, staff_user):
        episode = case_open.create_episode(user=staff_user, title="Default-Datum")
        assert episode.started_at == timezone.localdate()


@pytest.mark.django_db
class TestEpisodeClose:
    """``Episode.close`` ersetzt ``services/episodes.close_episode`` (Refs #958)."""

    def test_close_sets_ended_at_to_today(self, episode):
        closed = episode.close()
        assert closed.ended_at == timezone.localdate()

    def test_close_with_explicit_date(self, episode):
        episode.started_at = datetime.date(2025, 6, 1)
        custom_date = datetime.date(2025, 6, 15)
        closed = episode.close(ended_at=custom_date)
        assert closed.ended_at == custom_date

    def test_close_is_idempotent(self, episode):
        episode.started_at = datetime.date(2025, 6, 1)
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
                "basis_token": episode_basis_token(episode),
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
            started_at=timezone.localdate(),
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
        assert episode.ended_at == timezone.localdate()

    def test_episode_close_facility_scoping(self, client, staff_user, other_facility):
        other_case = _other_facility_case(other_facility)
        other_episode = Episode.objects.create(
            case=other_case,
            title="Andere Episode",
            started_at=timezone.localdate(),
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


@pytest.mark.django_db
class TestUpdateEpisodeService:
    """Episode edits audit actual changes against a signed current baseline."""

    def test_writes_audit_with_changed_fields(self, episode, staff_user, case_open):
        from core.models import AuditLog
        from core.services.case import update_episode

        update_episode(episode, staff_user, basis_token=episode_basis_token(episode), title="Neue Episode")

        episode.refresh_from_db()
        assert episode.title == "Neue Episode"
        log = AuditLog.objects.get(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk))
        assert log.user == staff_user
        assert log.target_type == "Episode"
        assert log.facility == case_open.facility
        assert log.detail["changed_fields"] == ["title"]
        assert log.detail["case_id"] == str(case_open.pk)

    def test_detail_carries_no_freetext(self, episode, staff_user):
        """Titel und Beschreibung sind klientenbezogener Freitext (Refs #1751)."""
        from core.models import AuditLog
        from core.services.case import update_episode

        update_episode(
            episode,
            staff_user,
            basis_token=episode_basis_token(episode),
            title="Klartext-Titel",
            description="Klartext-Verlauf",
        )

        log = AuditLog.objects.get(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk))
        assert set(log.detail) == {"case_id", "changed_fields"}
        assert "Klartext" not in str(log.detail)

    def test_identical_values_write_nothing(self, episode, staff_user):
        from core.models import AuditLog
        from core.services.case import update_episode

        update_episode(
            episode,
            staff_user,
            basis_token=episode_basis_token(episode),
            title=episode.title,
            description=episode.description,
        )

        assert not AuditLog.objects.filter(action=AuditLog.Action.EPISODE_UPDATE).exists()

    def test_without_fields_writes_nothing(self, episode, staff_user):
        from core.models import AuditLog
        from core.services.case import update_episode

        update_episode(episode, staff_user, basis_token=episode_basis_token(episode))

        assert not AuditLog.objects.filter(action=AuditLog.Action.EPISODE_UPDATE).exists()

    def test_unknown_field_is_rejected(self, episode, staff_user):
        """Allowlist gegen Mass-Assignment (Muster ``update_case``, Refs #734)."""
        from core.services.case import update_episode

        with pytest.raises(ValueError, match="case"):
            update_episode(episode, staff_user, basis_token=episode_basis_token(episode), case=episode.case)

    def test_only_changed_columns_are_written(self, episode, staff_user):
        """``save(update_fields=…)`` — ein Vollsave überschriebe die parallel
        laufende Redaktions-Kaskade mit dem stalen Klartext (Refs #1789)."""
        from core.services.case import update_episode

        Episode.objects.filter(pk=episode.pk).update(description="Aus der Redaktion")
        update_episode(
            episode,
            staff_user,
            basis_token=episode_basis_token(Episode.objects.get(pk=episode.pk)),
            title="Nur der Titel",
        )

        episode.refresh_from_db()
        assert episode.title == "Nur der Titel"
        assert episode.description == "Aus der Redaktion"
