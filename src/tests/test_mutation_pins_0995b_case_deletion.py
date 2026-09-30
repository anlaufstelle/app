"""Pins for the refusal message of a case that changed during the lock chain (``core.services.case.deletion``).

Source: mutation delta of the checkpoint release-0.99.5, 4 mutants.
Groups:

* ``create_or_get_case_deletion_request`` — the refusal of a case that is no
  longer in the facility or was moved to another person while the locks
  were taken loses its message (``None`` instead of the text, or a lazy
  translation of ``None``).
* ``restore_case`` — the same refusal on restoring a trashed case.

The race is replayed deterministically: right after the first observed row
lock on the person table (``tests._row_lock_helpers``, ``nach_sperre``) the
case moves to another person, as a merge committed between the unlocked read
and the lock would. The pins compare the complete message and observe that
nothing was written.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, Case, Client
from core.models.workitem import DeletionRequest
from core.services.case.deletion import create_or_get_case_deletion_request, restore_case
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

CLIENT_TABLE = "core_client"
CASE_CHANGED_TEXT = "Der Fall wurde zwischenzeitlich geändert — bitte die Ansicht neu laden."


@pytest.fixture
def other_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Other person", created_by=staff_user)


def _move_case_after_person_lock(queries, case, person):
    queries.nach_sperre(CLIENT_TABLE, lambda: Case.objects.filter(pk=case.pk).update(client_id=person.pk))


class TestCreateOrGetCaseDeletionRequest:
    """``create_or_get_case_deletion_request``: refusal when the locked case row no longer matches."""

    def test_case_moved_during_the_locks_is_refused_with_its_message(self, case_open, other_person, staff_user):
        """x_create_or_get_case_deletion_request__mutmut_19,
        x_create_or_get_case_deletion_request__mutmut_20: the refusal lost its
        message; the request itself is still refused."""
        requests = DeletionRequest.objects.count()
        audit_rows = AuditLog.objects.count()
        with mitschnitt() as queries, pytest.raises(ValidationError) as excinfo:
            _move_case_after_person_lock(queries, case_open, other_person)
            create_or_get_case_deletion_request(case_open, staff_user, "Recorded twice")
        assert excinfo.value.messages == [CASE_CHANGED_TEXT]
        assert queries.offene_schranken() == []
        assert DeletionRequest.objects.count() == requests
        assert AuditLog.objects.count() == audit_rows


class TestRestoreCase:
    """``restore_case``: refusal when the locked case row no longer matches."""

    def test_case_moved_during_the_locks_is_refused_with_its_message(self, case_trashed, other_person, lead_user):
        """x_restore_case__mutmut_19, x_restore_case__mutmut_20: the refusal
        lost its message; the case stays in the trash."""
        audit_rows = AuditLog.objects.count()
        with mitschnitt() as queries, pytest.raises(ValidationError) as excinfo:
            _move_case_after_person_lock(queries, case_trashed, other_person)
            restore_case(case_trashed, lead_user)
        assert excinfo.value.messages == [CASE_CHANGED_TEXT]
        assert queries.offene_schranken() == []
        assert Case.objects.filter(pk=case_trashed.pk, is_deleted=True).exists()
        assert AuditLog.objects.count() == audit_rows
