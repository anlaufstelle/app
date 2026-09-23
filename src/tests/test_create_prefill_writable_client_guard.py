"""GET-Vorbelegung darf nur weiterhin beschreibbare Personen übernehmen.

Die drei Anlegeformulare müssen für ``?client=`` denselben Maßstab wie ihre
Formularprüfung verwenden. Insbesondere darf eine inzwischen eingeschränkte
Person weder als rohe ID noch mit ihrem Pseudonym im Neuformular erscheinen.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Client
from core.services.client import approve_client_deletion, request_client_deletion
from core.services.client.restriction import restrict_processing

CREATE_URLS = (
    "core:event_create",
    "core:case_create",
    "core:workitem_create",
)


def _assert_empty_prefill(response, person):
    assert response.status_code == 200
    html = response.content.decode()
    assert response.context["client_id"] == ""
    assert response.context["client_pseudonym"] == ""
    assert str(person.pk) not in html
    assert person.pseudonym not in html


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", CREATE_URLS)
def test_valid_person_stays_prefilled(client, staff_user, client_identified, url_name):
    client.force_login(staff_user)

    response = client.get(reverse(url_name), {"client": str(client_identified.pk)})

    assert response.status_code == 200
    assert response.context["client_id"] == str(client_identified.pk)
    assert response.context["client_pseudonym"] == client_identified.pseudonym


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", CREATE_URLS)
def test_restricted_person_is_not_prefilled(client, staff_user, client_identified, url_name):
    restrict_processing(
        client_identified,
        staff_user,
        reason=Client.RestrictionReason.ACCURACY_DISPUTED,
    )
    client.force_login(staff_user)

    response = client.get(reverse(url_name), {"client": str(client_identified.pk)})

    _assert_empty_prefill(response, client_identified)


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", CREATE_URLS)
def test_trashed_person_is_not_prefilled(client, staff_user, lead_user, client_identified, url_name):
    deletion = request_client_deletion(client_identified, staff_user, "Doppelt angelegt")
    approve_client_deletion(deletion, lead_user)
    client.force_login(staff_user)

    response = client.get(reverse(url_name), {"client": str(client_identified.pk)})

    _assert_empty_prefill(response, client_identified)
