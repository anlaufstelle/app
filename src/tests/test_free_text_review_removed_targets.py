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
    "target,fixture,field",
    [
        ("Client", "client_identified", "notes"),
        ("Case", "case_open", "description"),
        ("Episode", "episode", "description"),
        ("WorkItem", "sample_workitem", "description"),
    ],
)
def test_removed_review_targets_return_not_found(request, client, staff_user, target, fixture, field):
    owner = request.getfixturevalue(fixture)
    client.force_login(staff_user)
    url = reverse(
        "core:free_text_review_target", kwargs={"target_type": target, "target_id": owner.pk, "field_key": field}
    )
    assert client.get(url).status_code == 404
    assert client.post(url, {"action": "revoke"}).status_code == 404


@pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user", "admin_user"])
def test_review_defaults_use_a_remaining_allowed_target(request, client, role, sample_event):
    client.force_login(request.getfixturevalue(role))
    response = client.get(reverse("core:free_text_review_list"))
    assert response.status_code == 200
    assert response.context["filter_form"].cleaned_data["model"] == "Event"
    assert [entry.target_id for entry in response.context["entries"]] == [sample_event.pk]


@pytest.mark.parametrize("partial", [False, True])
def test_review_without_remaining_targets_returns_an_empty_page(client, assistant_user, monkeypatch, partial):
    from core.services.free_text_review import TARGET_LABELS

    monkeypatch.setattr("core.views.free_text_review.TARGET_LABELS", {})
    monkeypatch.setattr("core.forms.free_text_review.TARGET_LABELS", {})
    assert "Event" in TARGET_LABELS
    client.force_login(assistant_user)
    headers = {"HTTP_HX_REQUEST": "true"} if partial else {}
    response = client.get(reverse("core:free_text_review_list"), **headers)
    assert response.status_code == 200
    assert response.context["entries"] == []
    assert not response.context["page_obj"].has_next()


@pytest.mark.parametrize("target", ["Client", "Case", "Episode", "WorkItem"])
def test_removed_review_list_target_is_not_found(client, staff_user, target):
    client.force_login(staff_user)
    assert client.get(reverse("core:free_text_review_list"), {"model": target}).status_code == 404


def test_review_list_describes_only_event_texts(client, staff_user):
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"))
    assert response.status_code == 200
    assert list(response.context["filter_form"].fields["model"].choices) == [("Event", "Ereignis-Freitexte")]
    assert "unverschlüsselte Ereignis-Freitexte" in response.content.decode()
    assert "Bei Episoden zählt" not in response.content.decode()
