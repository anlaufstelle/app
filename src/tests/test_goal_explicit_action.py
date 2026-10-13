"""Goal buttons express an idempotent desired state across stale tabs."""

import re
from datetime import date

import pytest
from django.urls import reverse

from core.models.outcome import OutcomeGoal

pytestmark = pytest.mark.django_db


def goal_url(goal):
    return reverse("core:goal_toggle", kwargs={"case_pk": goal.case_id, "pk": goal.pk})


def rendered_action(client, goal):
    response = client.get(reverse("core:case_detail", args=[goal.case_id]))
    assert response.status_code == 200
    form = re.search(r'hx-post="' + re.escape(goal_url(goal)) + r'".*?</form>', response.content.decode(), re.DOTALL)
    assert form is not None
    action = re.search(r'name="action" value="([a-z]+)"', form.group())
    assert action is not None
    return {"action": action.group(1)}


def test_two_old_achieve_requests_preserve_original_date(client, staff_user, outcome_goal):
    client.force_login(staff_user)
    url = goal_url(outcome_goal)
    tab_a, tab_b = rendered_action(client, outcome_goal), rendered_action(client, outcome_goal)
    assert tab_a == tab_b == {"action": "achieve"}
    assert client.post(url, tab_a).status_code == 200
    OutcomeGoal.objects.filter(pk=outcome_goal.pk).update(achieved_at=date(2025, 1, 1))
    response = client.post(url, tab_b)
    assert response.status_code == 200
    outcome_goal.refresh_from_db()
    assert outcome_goal.is_achieved
    assert outcome_goal.achieved_at == date(2025, 1, 1)
    assert 'name="action" value="unachieve"' in response.content.decode()


def test_explicit_undo_and_old_achieve_follow_the_requested_state(client, staff_user, outcome_goal):
    client.force_login(staff_user)
    url = goal_url(outcome_goal)
    old_achieve = rendered_action(client, outcome_goal)
    assert client.post(url, old_achieve).status_code == 200
    undo_a, undo_b = rendered_action(client, outcome_goal), rendered_action(client, outcome_goal)
    assert undo_a == undo_b == {"action": "unachieve"}
    for data, achieved in [(undo_a, False), (undo_b, False), (old_achieve, True)]:
        response = client.post(url, data)
        assert response.status_code == 200
        outcome_goal.refresh_from_db()
        assert outcome_goal.is_achieved == achieved
        assert (outcome_goal.achieved_at is not None) == achieved
        expected = "unachieve" if achieved else "achieve"
        assert f'name="action" value="{expected}"' in response.content.decode()


@pytest.mark.parametrize("data", [{}, {"action": "toggle"}, {"action": ""}])
def test_goal_action_is_required(client, staff_user, outcome_goal, data):
    client.force_login(staff_user)
    assert client.post(goal_url(outcome_goal), data).status_code == 400
    outcome_goal.refresh_from_db()
    assert not outcome_goal.is_achieved
