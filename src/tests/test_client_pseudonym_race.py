"""Two requests claiming the same pseudonym: the loser sees a form error, not a 500.

The form check and the database constraint are two steps. The race is
simulated by letting the form check pass (as it does when both requests
validate before either writes) while the pseudonym is already taken, so the
real unique constraint rejects the write.
"""

from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.urls import reverse

from core.forms.clients import _FacilityScopedPseudonymMixin
from core.models import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def taken_pseudonym(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Taken-Race-01",
        created_by=staff_user,
    ).pseudonym


@pytest.fixture
def form_check_passes():
    """Both racing requests passed the form check before either one wrote."""
    with patch.object(_FacilityScopedPseudonymMixin, "clean_pseudonym", lambda self: self.cleaned_data["pseudonym"]):
        yield


def _create_payload(pseudonym):
    return {"pseudonym": pseudonym, "contact_stage": "identified", "age_cluster": "unknown"}


def _inline_payload(pseudonym):
    return {"pseudonym": pseudonym, "contact_stage": "identified", "min_stage": ""}


class TestCreateRace:
    def test_create_shows_exists_message_instead_of_server_error(
        self, client, staff_user, taken_pseudonym, form_check_passes
    ):
        client.force_login(staff_user)

        response = client.post(reverse("core:client_create"), _create_payload(taken_pseudonym))

        assert response.status_code == 200
        assert "existiert bereits" in response.content.decode()
        assert Client.objects.filter(pseudonym=taken_pseudonym).count() == 1

    def test_inline_create_shows_exists_message_instead_of_server_error(
        self, client, staff_user, taken_pseudonym, form_check_passes
    ):
        client.force_login(staff_user)

        response = client.post(reverse("core:client_inline_create"), _inline_payload(taken_pseudonym))

        assert response.status_code == 200
        assert "existiert bereits" in response.content.decode()
        assert Client.objects.filter(pseudonym=taken_pseudonym).count() == 1

    def test_unrelated_integrity_error_is_not_swallowed(self, client, staff_user):
        client.force_login(staff_user)

        with (
            patch("core.views.clients.create_client", side_effect=IntegrityError("some other constraint")),
            pytest.raises(IntegrityError),
        ):
            client.post(reverse("core:client_create"), _create_payload("Fresh-Race-01"))

    def test_unrelated_integrity_error_on_inline_create_is_not_swallowed(self, client, staff_user):
        client.force_login(staff_user)

        with (
            patch("core.views.clients.create_client", side_effect=IntegrityError("some other constraint")),
            pytest.raises(IntegrityError),
        ):
            client.post(reverse("core:client_inline_create"), _inline_payload("Fresh-Race-02"))

    def test_free_pseudonym_is_still_created(self, client, staff_user):
        client.force_login(staff_user)

        response = client.post(reverse("core:client_create"), _create_payload("Fresh-Race-03"))

        assert response.status_code == 302
        assert Client.objects.filter(pseudonym="Fresh-Race-03").exists()


class TestUpdateRace:
    def test_rename_to_taken_pseudonym_shows_exists_message(
        self, client, staff_user, client_identified, taken_pseudonym, form_check_passes
    ):
        client.force_login(staff_user)
        original = client_identified.pseudonym

        response = client.post(
            reverse("core:client_update", kwargs={"pk": client_identified.pk}),
            _create_payload(taken_pseudonym),
        )

        assert response.status_code == 200
        assert "existiert bereits" in response.content.decode()
        client_identified.refresh_from_db()
        assert client_identified.pseudonym == original

    def test_unrelated_integrity_error_on_update_is_not_swallowed(self, client, staff_user, client_identified):
        client.force_login(staff_user)

        with (
            patch("core.views.clients.update_client", side_effect=IntegrityError("some other constraint")),
            pytest.raises(IntegrityError),
        ):
            client.post(
                reverse("core:client_update", kwargs={"pk": client_identified.pk}),
                _create_payload(client_identified.pseudonym),
            )
