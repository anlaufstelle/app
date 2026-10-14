"""User labels consistently follow User.__str__ and remain escaped."""

from html import escape

import pytest
from django.urls import reverse

from core.models import WorkItem

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "display,first,last,expected",
    [
        ("Display <&>", "Given", "Family", "Display <&>"),
        ("", "Given", "Family", "Given Family"),
        ("", "", "", "testadmin"),
    ],
)
def test_rendered_user_labels(client, admin_user, facility, import_job, display, first, last, expected):
    admin_user.display_name = display
    admin_user.first_name = first
    admin_user.last_name = last
    admin_user.save()
    item = WorkItem.objects.create(facility=facility, created_by=admin_user, assigned_to=admin_user, title="Label task")
    client.force_login(admin_user)
    label = escape(expected)
    for url in [
        reverse("core:account_profile"),
        reverse("core:workitem_inbox"),
        reverse("core:workitem_detail", args=[item.pk]),
        reverse("core:import_detail", args=[import_job.pk]),
        reverse("core:import_list"),
    ]:
        response = client.get(url)
        assert response.status_code == 200
        assert label in response.content.decode(), url
    inbox = client.get(reverse("core:workitem_inbox")).content.decode()
    assert f">{label}</option>" in inbox
    if display:
        assert "Display <&>" not in inbox
        assert ">Given Family</option>" not in inbox


def test_name_changed_through_access_hub_is_used_by_all_owned_views(client, admin_user, facility, import_job):
    from core.forms.workitems import WorkItemForm

    item = WorkItem.objects.create(facility=facility, created_by=admin_user, assigned_to=admin_user, title="Hub label")
    client.force_login(admin_user)
    edit_url = reverse("core:user_access_update", args=[admin_user.pk])
    form = client.get(edit_url).context["form"]
    data = {
        name: getattr(admin_user, name)
        for name in (
            "username",
            "email",
            "role",
            "is_active",
            "mfa_required",
            "can_confirm_deletion",
        )
    }
    data.update(display_name="Updated <&> label", basis_token=form.initial["basis_token"])
    assert client.post(edit_url, data).status_code == 302
    admin_user.refresh_from_db()
    label = escape(admin_user.display_name)
    for route, args in [
        ("core:account_profile", []),
        ("core:workitem_inbox", []),
        ("core:workitem_detail", [item.pk]),
        ("core:import_detail", [import_job.pk]),
        ("core:import_list", []),
    ]:
        response = client.get(reverse(route, args=args))
        assert response.status_code == 200
        assert label in response.content.decode()
    labels = {str(value): text for value, text in WorkItemForm(facility=facility).fields["assigned_to"].choices}
    assert labels[str(admin_user.pk)] == admin_user.display_name
