"""Refs #2338: Die approve-Services des Vier-Augen-Prinzips ziehen die Einrichtungsgrenze selbst.

Die vier Reject-Services pruefen seit Refs #2321 ausdruecklich, dass der
Reviewer zur Einrichtung des Antrags gehoert. Die destruktiveren
Gegenstuecke — Genehmigen legt Ereignis, Fall oder Person in den Papierkorb
bzw. fuehrt zwei Personen zusammen — prueften nur Antragsteller und Recht.
Ueber HTTP unerreichbar (die View scopet), in Produktion von RLS getragen;
der Service ist aber die Single Source of Truth, und dieselbe Zusage fehlte.

Je Service: ein Reviewer aus einer fremden Einrichtung (RLS im Test nicht
aktiv, der Service ist also die einzige Grenze) wird mit derselben Meldung
abgewiesen wie auf der Reject-Seite; weder Antrag noch Ziel aendern sich,
es entsteht keine Audit-Zeile. Der Gegen-Check mit dem regulaeren Reviewer
geht durch.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, Case, Client, DeletionRequest, Event

pytestmark = pytest.mark.django_db

_MELDUNG = "Antrag gehört zu einer anderen Einrichtung."


@pytest.fixture
def foreign_reviewer(second_facility):
    from core.models import User

    return User.objects.create_user(
        username="fremdeleitung-approve",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )


def _request(facility, staff_user, target_type, target_id, **extra):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        status=DeletionRequest.Status.PENDING,
        requested_by=staff_user,
        reason="Pruefsonde",
        **extra,
    )


def _assert_refused(callable_, deletion_request):
    audit_vorher = AuditLog.objects.count()
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert excinfo.value.messages == [_MELDUNG]
    deletion_request.refresh_from_db()
    assert deletion_request.status == DeletionRequest.Status.PENDING
    assert deletion_request.reviewed_by_id is None
    assert deletion_request.reviewed_at is None
    assert AuditLog.objects.count() == audit_vorher


class TestForeignReviewerIsRefused:
    def test_event_approve(self, facility, staff_user, foreign_reviewer, sample_event):
        from core.services.events.deletion import approve_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT, sample_event.pk)
        _assert_refused(lambda: approve_deletion(dr, foreign_reviewer), dr)
        assert Event.objects.get(pk=sample_event.pk).is_deleted is False

    def test_case_approve(self, facility, staff_user, foreign_reviewer, case_open):
        from core.services.case.deletion import approve_case_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE, case_open.pk)
        _assert_refused(lambda: approve_case_deletion(dr, foreign_reviewer), dr)
        assert Case.objects.get(pk=case_open.pk).is_deleted is False

    def test_client_approve(self, facility, staff_user, foreign_reviewer, client_identified):
        from core.services.client.main import approve_client_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CLIENT, client_identified.pk)
        _assert_refused(lambda: approve_client_deletion(dr, foreign_reviewer), dr)
        assert Client.objects.get(pk=client_identified.pk).is_deleted is False

    def test_merge_approve(self, facility, staff_user, foreign_reviewer, client_identified, client_qualified):
        from core.services.client.merge import approve_client_merge

        dr = _request(
            facility,
            staff_user,
            DeletionRequest.TargetType.CLIENT,
            client_qualified.pk,
            merge_into_id=client_identified.pk,
        )
        _assert_refused(lambda: approve_client_merge(dr, foreign_reviewer), dr)
        quelle = Client.objects.get(pk=client_qualified.pk)
        assert quelle.merged_into_id is None
        assert quelle.is_deleted is False


class TestOwnFacilityReviewerStillPasses:
    """Gegen-Check: die Grenze darf den regulaeren Weg nicht zumauern."""

    def test_event_approve_passes(self, facility, staff_user, lead_user, sample_event):
        from core.services.events.deletion import approve_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT, sample_event.pk)
        approve_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert Event.objects.get(pk=sample_event.pk).is_deleted is True

    def test_case_approve_passes(self, facility, staff_user, lead_user, case_open):
        from core.services.case.deletion import approve_case_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE, case_open.pk)
        approve_case_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert Case.objects.get(pk=case_open.pk).is_deleted is True

    def test_client_approve_passes(self, facility, staff_user, lead_user, client_identified):
        from core.services.client.main import approve_client_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CLIENT, client_identified.pk)
        approve_client_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert Client.objects.get(pk=client_identified.pk).is_deleted is True

    def test_merge_approve_passes(self, facility, staff_user, lead_user, client_identified, client_qualified):
        from core.services.client.merge import approve_client_merge

        dr = _request(
            facility,
            staff_user,
            DeletionRequest.TargetType.CLIENT,
            client_qualified.pk,
            merge_into_id=client_identified.pk,
        )
        approve_client_merge(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert Client.objects.get(pk=client_qualified.pk).merged_into_id == client_identified.pk


class TestNormalpfadMeldungenUnveraendert:
    """Die bestehenden Guards behalten Reihenfolge und Wortlaut — auch fuer den fremden Reviewer."""

    def test_selbstgenehmigung_bleibt_die_erste_absage(self, facility, staff_user, case_open):
        from core.services.case.deletion import approve_case_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE, case_open.pk)
        with pytest.raises(ValidationError) as excinfo:
            approve_case_deletion(dr, staff_user)
        assert excinfo.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_fehlendes_recht_bleibt_vor_der_einrichtungsgrenze(self, facility, staff_user, foreign_reviewer, case_open):
        from core.services.case.deletion import approve_case_deletion

        foreign_reviewer.can_confirm_deletion = False
        foreign_reviewer.save(update_fields=["can_confirm_deletion"])
        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE, case_open.pk)
        with pytest.raises(ValidationError) as excinfo:
            approve_case_deletion(dr, foreign_reviewer)
        assert excinfo.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]

    def test_bereits_entschieden_bleibt_fuer_den_eigenen_reviewer(self, facility, staff_user, lead_user, case_open):
        from core.services.case.deletion import approve_case_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE, case_open.pk)
        DeletionRequest.objects.filter(pk=dr.pk).update(status=DeletionRequest.Status.REJECTED)
        with pytest.raises(ValidationError) as excinfo:
            approve_case_deletion(dr, lead_user)
        assert excinfo.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]
