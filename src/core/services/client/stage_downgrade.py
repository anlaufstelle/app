"""Four-eyes contact-stage downgrade using the existing request workflow."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import AuditLog, Client, DeletionRequest
from core.retention.legal_holds import client_has_active_hold
from core.services.audit import audit_client_event
from core.services.client.locking import lock_clients
from core.services.client.restriction import assert_processing_allowed


def _lock_person(client, user):
    if user.facility_id is None or user.facility_id != client.facility_id:
        raise ValidationError(_("Die Person gehört zu einer anderen Einrichtung."))
    locked = lock_clients([client.pk], facility=user.facility_id).get(client.pk)
    if locked is None or locked.is_deleted or not locked.is_active or locked.merged_into_id is not None:
        raise ValidationError(_("Diese Person kann nicht herabgestuft werden."))
    assert_processing_allowed(locked, lock=True)
    if locked.contact_stage != Client.ContactStage.QUALIFIED:
        raise ValidationError(_("Die Person hat bereits die Kontaktstufe „Nur Pseudonym“."))
    if client_has_active_hold(locked):
        raise ValidationError(_("Die Person steht unter einem aktiven Legal Hold."))
    return locked


@transaction.atomic
def request_stage_downgrade(client, user, reason):
    """Request a downgrade without changing the record; serialize the pending slot."""
    if not user.is_active or not user.is_lead_or_admin:
        raise ValidationError(_("Nur Leitung und Anwendungsbetreuung dürfen eine Herabstufung beantragen."))
    if not reason.strip():
        raise ValidationError(_("Begründung ist erforderlich."))
    locked = _lock_person(client, user)
    existing = (
        DeletionRequest.objects.for_facility(locked.facility)
        .filter(target_type="Client", target_id=locked.pk, status="pending")
        .first()
    )
    if existing:
        if existing.stage_downgrade:
            return existing
        raise ValidationError(_("Für diese Person liegt bereits ein anderer Antrag vor."))
    dr = DeletionRequest.objects.create(
        facility=locked.facility,
        target_type="Client",
        target_id=locked.pk,
        stage_downgrade=True,
        requested_by=user,
        reason=reason.strip(),
    )
    audit_client_event(locked, user, AuditLog.Action.CLIENT_STAGE_DOWNGRADE_REQUESTED, deletion_request=str(dr.pk))
    return dr


@transaction.atomic
def review_stage_downgrade(dr, reviewer, *, approve):
    """Review under person -> request locks; confirmation grants no reading rights."""
    if reviewer.facility_id is None or reviewer.facility_id != dr.facility_id:
        raise ValidationError(_("Antrag gehört zu einer anderen Einrichtung."))
    if not reviewer.is_active or not reviewer.can_confirm_deletion:
        raise ValidationError(_("Für die Freigabe ist das Recht „Löschbestätigung“ erforderlich."))
    # Read the target in the actor's facility before choosing the lock anchor.
    current = DeletionRequest.objects.for_facility(reviewer.facility).get(pk=dr.pk)
    person = lock_clients([current.target_id], facility=reviewer.facility_id).get(current.target_id)
    current = DeletionRequest.objects.for_facility(reviewer.facility).select_for_update().get(pk=dr.pk)
    if not current.stage_downgrade or current.target_type != "Client" or current.merge_into_id is not None:
        raise ValidationError(_("Dies ist kein Herabstufungsantrag."))
    if current.status != DeletionRequest.Status.PENDING:
        raise ValidationError(_("Der Antrag wurde bereits bearbeitet."))
    if current.requested_by_id == reviewer.pk or current.requested_by_id is None:
        raise ValidationError(_("Die Freigabe erfordert eine andere Person als die antragstellende."))
    if person is None or person.pk != current.target_id:
        raise ValidationError(_("Die Person ist nicht mehr verfügbar."))
    if approve:
        person = _lock_person(person, reviewer)
        person.contact_stage = Client.ContactStage.IDENTIFIED
        person.save(update_fields=["contact_stage", "updated_at"])
    current.status = DeletionRequest.Status.APPROVED if approve else DeletionRequest.Status.REJECTED
    current.reviewed_by = reviewer
    current.reviewed_at = timezone.now()
    current.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    audit_client_event(
        person,
        reviewer,
        AuditLog.Action.CLIENT_STAGE_DOWNGRADED if approve else AuditLog.Action.CLIENT_STAGE_DOWNGRADE_REJECTED,
        deletion_request=str(current.pk),
    )
    return current
