"""Goal and milestone titles are bounded before database writes."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Milestone, OutcomeGoal
from core.services.case.goals import create_goal, create_milestone, update_goal

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("operation", ["create", "update", "milestone"])
@pytest.mark.parametrize("length", [200, 201])
def test_service_title_limit(case_open, outcome_goal, staff_user, operation, length):
    title = " " + "x" * length + " "

    def write():
        if operation == "create":
            return create_goal(case_open, staff_user, title)
        if operation == "update":
            return update_goal(outcome_goal, staff_user, title=title)
        return create_milestone(outcome_goal, staff_user, title)

    if length == 201:
        with pytest.raises(ValidationError, match="200"):
            write()
    else:
        assert write().title == title.strip()


@pytest.mark.parametrize("operation", ["create", "update", "milestone"])
@pytest.mark.parametrize("length", [200, 201])
def test_view_title_limit_preserves_section(client, staff_user, case_open, outcome_goal, operation, length):
    client.force_login(staff_user)
    kwargs = {"case_pk": case_open.pk}
    name = "goal_create"
    if operation == "update":
        name = "goal_update"
        kwargs["pk"] = outcome_goal.pk
    elif operation == "milestone":
        name = "milestone_create"
        kwargs["goal_pk"] = outcome_goal.pk
    before = list(OutcomeGoal.objects.values())
    milestones = list(Milestone.objects.values())
    response = client.post(reverse(f"core:{name}", kwargs=kwargs), {"title": "x" * length}, HTTP_HX_REQUEST="true")
    if length == 201:
        assert response.status_code == 409
        assert response["HX-Reswap"] == "none"
        assert "200" in response.content.decode()
        assert list(OutcomeGoal.objects.values()) == before
        assert list(Milestone.objects.values()) == milestones
    else:
        assert response.status_code == 200
        assert response.content.decode().count('maxlength="200"') == (5 if operation == "create" else 3)


def test_title_limit_is_explained_at_the_fields(client, staff_user, case_open):
    client.force_login(staff_user)
    response = client.post(
        reverse("core:goal_create", kwargs={"case_pk": case_open.pk}), {"title": "Goal"}, HTTP_HX_REQUEST="true"
    )
    content = response.content.decode()
    assert "Titel dürfen höchstens 200 Zeichen enthalten." in content
    assert content.count('aria-describedby="goal-title-limit"') == 3


@pytest.mark.parametrize("title", ["", "   "])
@pytest.mark.parametrize("operation", ["create", "update"])
def test_empty_goal_title_is_rejected_without_writes(client, staff_user, case_open, outcome_goal, operation, title):
    client.force_login(staff_user)
    kwargs = {"case_pk": case_open.pk}
    if operation == "update":
        kwargs["pk"] = outcome_goal.pk
    before = list(OutcomeGoal.objects.values())
    response = client.post(
        reverse(f"core:goal_{operation}", kwargs=kwargs),
        {"title": title, "description": "Unsaved description"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 409
    assert response["HX-Reswap"] == "none"
    assert "Der Titel darf nicht leer sein." in response.content.decode()
    assert list(OutcomeGoal.objects.values()) == before


@pytest.mark.parametrize("title", ["", "   "])
@pytest.mark.parametrize("operation", ["create", "update", "milestone"])
def test_service_rejects_empty_titles(case_open, outcome_goal, staff_user, operation, title):
    before = list(OutcomeGoal.objects.values())
    milestones = list(Milestone.objects.values())
    with pytest.raises(ValidationError, match="Der Titel darf nicht leer sein"):
        if operation == "create":
            create_goal(case_open, staff_user, title)
        elif operation == "update":
            update_goal(outcome_goal, staff_user, title=title, description="Unsaved")
        else:
            create_milestone(outcome_goal, staff_user, title)
    assert list(OutcomeGoal.objects.values()) == before
    assert list(Milestone.objects.values()) == milestones
