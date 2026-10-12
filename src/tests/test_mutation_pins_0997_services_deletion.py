"""Pins for deletion-target annotation privacy and lookup scope.

Source: mutation delta of the release-0.99.7 checkpoint, 7 mutants.
Groups: person and merge-target roles (2), requested/live case scope (3),
event metadata roles (2). All observations use annotate_deletion_targets.
"""

import pytest
from django.db.models.signals import post_init
from django.utils.translation import gettext as _

from core.models import Case, Client, DeletionRequest
from core.services.deletion import annotate_deletion_targets

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("actor", ["system_admin", "missing"])
def test_person_and_merge_labels_require_assistant_role(facility, client_identified, super_admin_user, actor):
    """Without the assistant role, neither person in a merge is disclosed.

    Kills x_annotate_deletion_targets__mutmut_16 and
    x_annotate_deletion_targets__mutmut_17.
    """
    target = Client.objects.create(facility=facility, pseudonym="Merge destination")
    request = DeletionRequest(
        facility=facility, target_type="Client", target_id=client_identified.pk, merge_into=target
    )
    user = super_admin_user if actor == "system_admin" else None
    assert user is None or not user.is_assistant_or_above

    annotate_deletion_targets([request], facility, user)

    assert request.target_label == _("Person (nicht mehr auffindbar)")
    assert request.merge_target_label == _("Person (nicht mehr auffindbar)")
    assert request.target_url == request.merge_target_url == ""


def test_case_lookup_does_not_materialize_unrequested_cases(facility, case_open, client_identified, staff_user):
    """Target annotation must not load an unrelated case's free-text content.

    Kills x_annotate_deletion_targets__mutmut_23. A public model signal observes
    materialized cases; queries returning values instead of models also pass.
    """
    unrelated = Case.objects.create(facility=facility, client=client_identified, title="Unrequested private case")
    request = DeletionRequest(facility=facility, target_type="Case", target_id=case_open.pk)
    loaded = set()

    def observe_case(sender, instance, **kwargs):
        loaded.add(instance.pk)

    post_init.connect(observe_case, sender=Case, weak=False)
    try:
        annotate_deletion_targets([request], facility, staff_user)
    finally:
        post_init.disconnect(observe_case, sender=Case)

    assert request.target_label == case_open.title
    assert request.target_url
    assert unrelated.pk not in loaded
    assert loaded <= {case_open.pk}


@pytest.mark.parametrize("state", ["deleted", "inactive"])
def test_case_of_an_unavailable_person_has_no_title_or_link(facility, case_open, client_identified, staff_user, state):
    """A deleted or inactive person makes its case target generic.

    Kills x_annotate_deletion_targets__mutmut_24 and
    x_annotate_deletion_targets__mutmut_25.
    """
    Client.objects.filter(pk=client_identified.pk).update(
        **({"is_deleted": True} if state == "deleted" else {"is_active": False})
    )
    request = DeletionRequest(facility=facility, target_type="Case", target_id=case_open.pk)

    annotate_deletion_targets([request], facility, staff_user)

    assert request.target_label == _("Fall")
    assert request.target_url == ""


def test_system_admin_without_assistant_role_gets_no_event_metadata(facility, sample_event, super_admin_user):
    """System administration alone cannot disclose event type, date or URL.

    Kills x_annotate_deletion_targets__mutmut_41 and
    x_annotate_deletion_targets__mutmut_42.
    """
    assert not super_admin_user.is_assistant_or_above
    request = DeletionRequest(facility=facility, target_type="Event", target_id=sample_event.pk)

    annotate_deletion_targets([request], facility, super_admin_user)

    assert request.target_label == _("Ereignis")
    assert request.target_url == ""
