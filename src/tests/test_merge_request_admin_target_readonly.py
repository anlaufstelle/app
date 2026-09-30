"""The merge target of a merge request cannot be changed in the admin change form.

A merge request is a ``DeletionRequest`` with ``merge_into`` set: the approval
merges the source (``target_id``) into ``merge_into``. The request goes through
the four-eyes review, so the target a person requested must be the target the
reviewer approves. The admin change form therefore shows the target read-only,
like the source, and a POST that names another target leaves it as requested.
"""

from __future__ import annotations

import pytest

from core.models import Client, DeletionRequest

pytestmark = pytest.mark.django_db


def _person(facility, created_by, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym=pseudonym,
        created_by=created_by,
    )


@pytest.fixture
def merge_request(facility, staff_user):
    """A pending merge request as ``create_or_get_client_merge_request`` stores it."""
    source = _person(facility, staff_user, "Merge-Source-01")
    target = _person(facility, staff_user, "Merge-Target-01")
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=source.pk,
        merge_into=target,
        reason="Duplicate record",
        requested_by=staff_user,
    )


@pytest.fixture
def other_person(facility, staff_user):
    """A person of the same facility that the form could otherwise offer as a new target."""
    return _person(facility, staff_user, "Merge-Other-01")


@pytest.fixture(params=["admin_user", "super_admin_user"])
def admin_client(request, client):
    """The test client logged in as a facility admin or as a super admin."""
    client.force_login(request.getfixturevalue(request.param))
    return client


def _change_url(deletion_request):
    return f"/admin-mgmt/core/deletionrequest/{deletion_request.pk}/change/"


class TestMergeTargetIsReadOnlyInTheAdminForm:
    def test_change_form_offers_no_editable_merge_target(self, admin_client, merge_request, other_person):
        response = admin_client.get(_change_url(merge_request))

        assert response.status_code == 200
        assert "merge_into" not in response.context["adminform"].form.fields
        assert b'name="merge_into"' not in response.content
        # No choice list of persons: the other person of the facility is not offered.
        assert other_person.pseudonym.encode() not in response.content

    def test_change_form_still_shows_the_requested_target(self, admin_client, merge_request):
        response = admin_client.get(_change_url(merge_request))

        assert response.status_code == 200
        assert merge_request.merge_into.pseudonym.encode() in response.content

    def test_post_with_another_target_leaves_the_requested_target(self, admin_client, merge_request, other_person):
        requested_target_id = merge_request.merge_into_id

        response = admin_client.post(
            _change_url(merge_request),
            {"merge_into": str(other_person.pk), "_save": "Save"},
        )

        # The form is accepted (saved and redirected), it just does not take the target.
        assert response.status_code == 302
        merge_request.refresh_from_db()
        assert merge_request.merge_into_id == requested_target_id
        assert merge_request.status == DeletionRequest.Status.PENDING
