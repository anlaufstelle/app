"""Removing fixed text targets must keep the remaining review list usable."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_staff_review_list_defaults_to_an_existing_target(client, staff_user):
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"))
    assert response.status_code == 200
    assert response.context["filter_form"].cleaned_data["model"] == "Event"


@pytest.mark.parametrize(
    "target,fixture,field", [("Client", "client_identified", "notes"), ("Case", "case_open", "description")]
)
def test_removed_review_targets_return_not_found(request, client, staff_user, target, fixture, field):
    owner = request.getfixturevalue(fixture)
    client.force_login(staff_user)
    url = reverse(
        "core:free_text_review_target", kwargs={"target_type": target, "target_id": owner.pk, "field_key": field}
    )
    assert client.get(url).status_code == 404
    assert client.post(url, {"action": "revoke"}).status_code == 404
