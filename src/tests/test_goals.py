"""Tests for OutcomeGoal and Milestone models, service layer, and HTMX views."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client
from core.models.outcome import Milestone, OutcomeGoal
from core.services.goals import (
    achieve_goal,
    create_goal,
    create_milestone,
    delete_milestone,
    toggle_milestone,
    unachieve_goal,
    update_goal,
)


def _other_facility_case(other_facility):
    """Refs #748: Case.client is mandatory — create matching client too."""
    other_client = Client.objects.create(
        facility=other_facility,
        pseudonym="Fremd-Goal-01",
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    return Case.objects.create(
        facility=other_facility,
        client=other_client,
        title="Anderer Fall",
        status=Case.Status.OPEN,
    )


@pytest.mark.django_db
class TestGoalService:
    def test_create_goal(self, case_open, staff_user):
        goal = create_goal(
            case=case_open,
            user=staff_user,
            title="Stabile Wohnsituation",
            description="Langfristiges Ziel",
        )
        assert goal.pk is not None
        assert goal.title == "Stabile Wohnsituation"
        assert goal.description == "Langfristiges Ziel"
        assert goal.case == case_open
        assert goal.created_by == staff_user
        assert goal.is_achieved is False
        assert goal.achieved_at is None

    def test_create_goal_default_description(self, case_open, staff_user):
        goal = create_goal(case=case_open, user=staff_user, title="Ziel")
        assert goal.description == ""

    def test_update_goal_title(self, outcome_goal, staff_user):
        updated = update_goal(outcome_goal, staff_user, title="Neuer Titel")
        assert updated.title == "Neuer Titel"

    def test_update_goal_description(self, outcome_goal, staff_user):
        updated = update_goal(outcome_goal, staff_user, description="Neue Beschreibung")
        assert updated.description == "Neue Beschreibung"
        # Title should remain unchanged
        assert updated.title == "Stabile Wohnsituation"

    def test_update_goal_none_values_unchanged(self, outcome_goal, staff_user):
        original_title = outcome_goal.title
        updated = update_goal(outcome_goal, staff_user, title=None, description=None)
        assert updated.title == original_title

    def test_achieve_goal(self, outcome_goal, staff_user):
        achieved = achieve_goal(outcome_goal, staff_user)
        assert achieved.is_achieved is True
        assert achieved.achieved_at == timezone.localdate()

    def test_achieve_goal_idempotent(self, outcome_goal, staff_user):
        achieve_goal(outcome_goal, staff_user)
        outcome_goal.refresh_from_db()
        original_date = outcome_goal.achieved_at
        # Call again — should be idempotent
        result = achieve_goal(outcome_goal, staff_user)
        assert result.achieved_at == original_date

    def test_unachieve_goal(self, outcome_goal, staff_user):
        achieve_goal(outcome_goal, staff_user)
        unachieved = unachieve_goal(outcome_goal, staff_user)
        assert unachieved.is_achieved is False
        assert unachieved.achieved_at is None

    def test_create_milestone(self, outcome_goal, staff_user):
        ms = create_milestone(goal=outcome_goal, user=staff_user, title="Schritt 1")
        assert ms.pk is not None
        assert ms.title == "Schritt 1"
        assert ms.goal == outcome_goal
        assert ms.is_completed is False
        assert ms.sort_order == 0

    def test_create_milestone_with_sort_order(self, outcome_goal, staff_user):
        ms = create_milestone(goal=outcome_goal, user=staff_user, title="Schritt 2", sort_order=5)
        assert ms.sort_order == 5

    def test_toggle_milestone_complete(self, milestone, staff_user):
        toggled = toggle_milestone(milestone, staff_user)
        assert toggled.is_completed is True
        assert toggled.completed_at == timezone.localdate()

    def test_toggle_milestone_uncomplete(self, milestone, staff_user):
        toggle_milestone(milestone, staff_user)  # complete
        toggled = toggle_milestone(milestone, staff_user)  # uncomplete
        assert toggled.is_completed is False
        assert toggled.completed_at is None

    def test_delete_milestone(self, milestone, staff_user):
        from core.models import AuditLog

        pk = milestone.pk
        title = milestone.title
        case_id = milestone.goal.case.pk
        delete_milestone(milestone, staff_user)
        assert not Milestone.objects.filter(pk=pk).exists()
        log = AuditLog.objects.get(target_id=str(pk))
        assert log.action == AuditLog.Action.MILESTONE_DELETE
        assert log.user == staff_user
        assert log.target_type == "Milestone"
        assert log.detail["title"] == title
        assert log.detail["case_id"] == str(case_id)


@pytest.mark.django_db
class TestGoalCreateView:
    def test_create_goal_post(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_create", kwargs={"case_pk": case_open.pk}),
            {"title": "Neues Wirkungsziel"},
        )
        assert response.status_code == 200
        assert OutcomeGoal.objects.filter(title="Neues Wirkungsziel").exists()

    def test_create_goal_returns_htmx_partial(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_create", kwargs={"case_pk": case_open.pk}),
            {"title": "Neues Ziel"},
        )
        content = response.content.decode()
        assert 'id="goals-section"' in content
        assert "Neues Ziel" in content

    def test_create_goal_auth_required(self, client, assistant_user, case_open):
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:goal_create", kwargs={"case_pk": case_open.pk}),
            {"title": "Ziel"},
        )
        assert response.status_code == 403

    def test_create_goal_facility_scoping(self, client, staff_user, other_facility):
        other_case = _other_facility_case(other_facility)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_create", kwargs={"case_pk": other_case.pk}),
            {"title": "Ziel"},
        )
        assert response.status_code == 404

    def test_create_goal_empty_title_ignored(self, client, staff_user, case_open):
        client.force_login(staff_user)
        client.post(
            reverse("core:goal_create", kwargs={"case_pk": case_open.pk}),
            {"title": "  "},
        )
        assert not OutcomeGoal.objects.filter(case=case_open).exists()


@pytest.mark.django_db
class TestGoalUpdateView:
    def test_goal_update_changes_title(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:goal_update",
                kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk},
            ),
            {"title": "Aktualisierter Titel"},
        )
        assert response.status_code == 200
        outcome_goal.refresh_from_db()
        assert outcome_goal.title == "Aktualisierter Titel"

    def test_goal_update_auth(self, client, assistant_user, case_open, outcome_goal):
        client.force_login(assistant_user)
        response = client.post(
            reverse(
                "core:goal_update",
                kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk},
            ),
            {"title": "Verbotener Titel"},
        )
        assert response.status_code in (302, 403)

    def test_goal_update_facility_scoping(self, client, staff_user, other_facility, outcome_goal):
        other_case = _other_facility_case(other_facility)
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:goal_update",
                kwargs={"case_pk": other_case.pk, "pk": outcome_goal.pk},
            ),
            {"title": "Fremdes Ziel"},
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestGoalToggleView:
    def test_toggle_goal_achieve(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:goal_toggle",
                kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk},
            )
        )
        assert response.status_code == 200
        outcome_goal.refresh_from_db()
        assert outcome_goal.is_achieved is True

    def test_toggle_goal_unachieve(self, client, staff_user, case_open, outcome_goal):
        achieve_goal(outcome_goal, staff_user)
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:goal_toggle",
                kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk},
            )
        )
        assert response.status_code == 200
        outcome_goal.refresh_from_db()
        assert outcome_goal.is_achieved is False

    def test_toggle_goal_facility_scoping(self, client, staff_user, other_facility, outcome_goal):
        other_case = _other_facility_case(other_facility)
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:goal_toggle",
                kwargs={"case_pk": other_case.pk, "pk": outcome_goal.pk},
            )
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestMilestoneCreateView:
    def test_create_milestone_post(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_create",
                kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk},
            ),
            {"title": "Neuer Meilenstein"},
        )
        assert response.status_code == 200
        assert Milestone.objects.filter(title="Neuer Meilenstein").exists()

    def test_create_milestone_returns_htmx_partial(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_create",
                kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk},
            ),
            {"title": "Neuer MS"},
        )
        content = response.content.decode()
        assert 'id="goals-section"' in content
        assert "Neuer MS" in content

    def test_create_milestone_empty_title_ignored(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        client.post(
            reverse(
                "core:milestone_create",
                kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk},
            ),
            {"title": ""},
        )
        assert not Milestone.objects.filter(goal=outcome_goal).exists()

    def test_create_milestone_facility_scoping(self, client, staff_user, other_facility, outcome_goal):
        other_case = _other_facility_case(other_facility)
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_create",
                kwargs={"case_pk": other_case.pk, "goal_pk": outcome_goal.pk},
            ),
            {"title": "MS"},
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestMilestoneToggleView:
    def test_toggle_milestone_complete(self, client, staff_user, case_open, milestone):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_toggle",
                kwargs={"case_pk": case_open.pk, "pk": milestone.pk},
            )
        )
        assert response.status_code == 200
        milestone.refresh_from_db()
        assert milestone.is_completed is True
        assert milestone.completed_at is not None

    def test_toggle_milestone_uncomplete(self, client, staff_user, case_open, milestone):
        toggle_milestone(milestone, staff_user)  # complete first
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_toggle",
                kwargs={"case_pk": case_open.pk, "pk": milestone.pk},
            )
        )
        assert response.status_code == 200
        milestone.refresh_from_db()
        assert milestone.is_completed is False

    def test_toggle_milestone_facility_scoping(self, client, staff_user, other_facility, milestone):
        other_case = _other_facility_case(other_facility)
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_toggle",
                kwargs={"case_pk": other_case.pk, "pk": milestone.pk},
            )
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestMilestoneDeleteView:
    def test_delete_milestone(self, client, staff_user, case_open, milestone):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_delete",
                kwargs={"case_pk": case_open.pk, "pk": milestone.pk},
            )
        )
        assert response.status_code == 200
        assert not Milestone.objects.filter(pk=milestone.pk).exists()

    def test_delete_milestone_returns_partial(self, client, staff_user, case_open, milestone):
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_delete",
                kwargs={"case_pk": case_open.pk, "pk": milestone.pk},
            )
        )
        content = response.content.decode()
        assert 'id="goals-section"' in content

    def test_delete_milestone_facility_scoping(self, client, staff_user, other_facility, milestone):
        other_case = _other_facility_case(other_facility)
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_delete",
                kwargs={"case_pk": other_case.pk, "pk": milestone.pk},
            )
        )
        assert response.status_code == 404
