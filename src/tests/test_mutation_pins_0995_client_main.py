"""Regression checks for person selection through the create views."""

import uuid

import pytest
from django.urls import reverse

from core.models import Client

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("url_name", ["core:event_create", "core:case_create", "core:workitem_create"])
class TestPersonPrefill:
    def test_requested_person_is_selected_among_two(self, client, facility, staff_user, url_name):
        first = Client.objects.create(facility=facility, pseudonym="First", created_by=staff_user)
        second = Client.objects.create(facility=facility, pseudonym="Second", created_by=staff_user)
        client.force_login(staff_user)
        for person in (second, first):
            response = client.get(reverse(url_name), {"client": str(person.pk)})
            assert response.status_code == 200
            assert response.context["client_id"] == str(person.pk)
            assert response.context["client_pseudonym"] == person.pseudonym
        response = client.get(reverse(url_name), {"client": str(uuid.uuid4())})
        assert response.context["client_id"] == ""
        assert response.context["client_pseudonym"] == ""

    def test_unknown_id_does_not_select_the_only_person(self, client, client_identified, staff_user, url_name):
        client.force_login(staff_user)
        response = client.get(reverse(url_name), {"client": str(uuid.uuid4())})
        assert response.status_code == 200
        assert response.context["client_id"] == ""
        assert response.context["client_pseudonym"] == ""
        response = client.get(reverse(url_name), {"client": str(client_identified.pk)})
        assert response.context["client_id"] == str(client_identified.pk)
        assert response.context["client_pseudonym"] == client_identified.pseudonym

    @pytest.mark.parametrize("params", [{}, {"client": ""}, {"client": "not-a-uuid"}])
    def test_missing_or_invalid_id_leaves_selection_empty(self, client, staff_user, url_name, params):
        client.force_login(staff_user)
        response = client.get(reverse(url_name), params)
        assert response.status_code == 200
        assert response.context["client_id"] == ""
        assert response.context["client_pseudonym"] == ""

    def test_foreign_person_is_not_disclosed(self, client, client_identified, second_facility_user, url_name):
        client.force_login(second_facility_user)
        response = client.get(reverse(url_name), {"client": str(client_identified.pk)})
        assert response.status_code == 200
        assert response.context["client_id"] == ""
        assert response.context["client_pseudonym"] == ""
        assert client_identified.pseudonym not in response.content.decode()
