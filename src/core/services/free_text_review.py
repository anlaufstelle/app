"""Resolve reviewable text through current facility, person and role boundaries."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Case, Client, Episode, Event, FreeTextReview, WorkItem
from core.services.audit import audit_event
from core.services.compliance.sensitivity import user_can_see_field
from core.services.dynamic_protection import lock_dynamic_protection
from core.services.workitem_visibility import readable_person_filter, workitems_for_read

TARGET_LABELS = {
    "Client": _("Personen-Notizen"),
    "Case": _("Fallbeschreibungen"),
    "Episode": _("Episodenbeschreibungen"),
    "WorkItem": _("Aufgabenbeschreibungen"),
    "Event": _("Ereignis-Freitexte"),
}
STATIC_FIELDS = {"Client": "notes", "Case": "description", "Episode": "description", "WorkItem": "description"}


@dataclass
class ReviewEntry:
    target_type: str
    target_id: UUID
    field_key: str
    text: str
    changed_at: datetime
    author_id: object
    fingerprint: str
    review: FreeTextReview | None

    @property
    def is_reviewed(self) -> bool:
        return bool(
            self.review
            and self.review.reviewed_until >= timezone.localdate()
            and constant_time_compare(self.review.content_fingerprint, self.fingerprint)
        )


def _authorize(facility, user):
    if (
        facility is None
        or not user.is_authenticated
        or not user.is_active
        or not user.is_assistant_or_above
        or user.facility_id != facility.pk
    ):
        raise PermissionDenied


def _targets(facility, user, target_type):
    _authorize(facility, user)
    if target_type == "Client":
        qs = Client.objects.for_facility(facility).filter(is_active=True, is_deleted=False)
    elif target_type == "Case":
        qs = Case.objects.for_facility(facility).filter(
            is_deleted=False, client__is_deleted=False, client__is_active=True, client__facility=facility
        )
    elif target_type == "Episode":
        qs = Episode.objects.filter(
            case__facility=facility,
            case__is_deleted=False,
            case__client__facility=facility,
            case__client__is_deleted=False,
            case__client__is_active=True,
            is_deleted=False,
        ).select_related("case")
    elif target_type == "WorkItem":
        return workitems_for_read(facility).filter(is_deleted=False)
    elif target_type == "Event":
        return (
            Event.objects.for_facility(facility)
            .visible_to(user)
            .filter(readable_person_filter(), is_deleted=False, document_type__facility=facility)
            .select_related("document_type")
        )
    else:
        raise Http404("Review target not found")
    return qs if user.is_staff_or_above else qs.none()


def _entry(facility, target_type, target, key, text, reviews):
    payload = json.dumps([str(facility.pk), target_type, str(target.pk), key, text], ensure_ascii=False)
    fingerprint = salted_hmac("core.free_text_review.content.v1", payload, algorithm="sha256").hexdigest()
    return ReviewEntry(
        target_type,
        target.pk,
        key,
        text,
        getattr(target, "updated_at", target.created_at),
        target.created_by_id,
        fingerprint,
        reviews.get((target.pk, key)),
    )


def _entries(facility, user, target_type, targets):
    reviews = {
        (review.target_id, review.field_key): review
        for review in FreeTextReview.objects.for_facility(facility).filter(target_type=target_type)
    }
    entries = []
    for target in targets:
        if target_type == "Event":
            fields = target.document_type.fields.select_related("field_template").filter(
                field_template__facility=facility,
                field_template__is_encrypted=False,
                field_template__field_type__in=["text", "textarea"],
            )
            values = (
                (field.field_template.slug, target.data_json.get(field.field_template.slug))
                for field in fields
                if user_can_see_field(user, target.document_type.sensitivity, field.field_template.sensitivity)
            )
        else:
            key = STATIC_FIELDS[target_type]
            values = [(key, getattr(target, key))]
        for key, text in values:
            # Marker dictionaries, unknown keys and non-text values never enter a review.
            if isinstance(text, str) and text.strip():
                entries.append(_entry(facility, target_type, target, key, text, reviews))
    return entries


def list_entries(facility, user, target_type):
    """Return only currently readable nonempty source fields, newest first."""
    targets = _targets(facility, user, target_type)
    return sorted(_entries(facility, user, target_type, targets), key=lambda entry: entry.changed_at, reverse=True)


def get_entry(facility, user, target_type, target_id, field_key):
    """Resolve the target and field afresh; hidden and missing values share a 404."""
    targets = _targets(facility, user, target_type).filter(pk=target_id)
    for entry in _entries(facility, user, target_type, targets):
        if entry.field_key == field_key:
            return entry
    raise Http404("Review target not found")


def _locked_entry(facility, user, target_type, target_id, field_key):
    """Stabilize schema and parent visibility before locking the source row."""
    _authorize(facility, user)
    lock_dynamic_protection(facility.pk)
    qs = _targets(facility, user, target_type)
    snapshot = qs.filter(pk=target_id).first()
    if snapshot is None:
        raise Http404("Review target not found")
    if target_type == "Client":
        client_id = snapshot.pk
    elif target_type == "Episode":
        client_id = snapshot.case.client_id
    else:
        client_id = snapshot.client_id
    if client_id:
        Client.objects.for_facility(facility).select_for_update().filter(pk=client_id).first()
    if target_type == "Episode":
        Case.objects.for_facility(facility).select_for_update().filter(pk=snapshot.case_id).first()
    target = qs.select_for_update(of=("self",)).filter(pk=target_id).first()
    if target is None:
        raise Http404("Review target not found")
    current_client = target.case.client_id if target_type == "Episode" else getattr(target, "client_id", target.pk)
    if current_client != client_id or (target_type == "Episode" and target.case_id != snapshot.case_id):
        raise ValidationError(_("Die Zuordnung wurde geändert. Bitte laden Sie die Liste neu."))
    return get_entry(facility, user, target_type, target_id, field_key)


@transaction.atomic
def mark_review(facility, user, target_type, target_id, field_key, reviewed_until, expected_fingerprint):
    """Mark exactly the content seen by the reviewer and audit metadata only."""
    entry = _locked_entry(facility, user, target_type, target_id, field_key)
    if not isinstance(reviewed_until, date) or reviewed_until < timezone.localdate():
        raise ValidationError(_("Bitte wählen Sie heute oder ein zukünftiges Datum."))
    if not constant_time_compare(entry.fingerprint, expected_fingerprint):
        raise ValidationError(_("Der Text wurde geändert. Bitte laden Sie die Liste neu und prüfen Sie ihn erneut."))
    review, _created = FreeTextReview.objects.for_facility(facility).update_or_create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        field_key=field_key,
        defaults={"reviewed_until": reviewed_until, "reviewed_by": user, "content_fingerprint": entry.fingerprint},
    )
    audit_event(
        AuditLog.Action.FREE_TEXT_REVIEWED,
        user=user,
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        detail={"review_field": field_key, "reviewed_until": reviewed_until.isoformat()},
    )
    return review


@transaction.atomic
def revoke_review(facility, user, target_type, target_id, field_key):
    """Remove a mark without changing its source text; recheck current visibility."""
    _locked_entry(facility, user, target_type, target_id, field_key)
    deleted, _counts = (
        FreeTextReview.objects.for_facility(facility)
        .filter(
            target_type=target_type,
            target_id=target_id,
            field_key=field_key,
        )
        .delete()
    )
    if deleted:
        audit_event(
            AuditLog.Action.FREE_TEXT_REVIEW_REVOKED,
            user=user,
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            detail={"review_field": field_key},
        )


def remove_target_reviews(instance):
    """Remove metadata for a removed target and its no-longer-readable descendants."""
    target_type = instance.__class__.__name__
    facility_id = instance.case.facility_id if target_type == "Episode" else instance.facility_id
    targets = Q(target_type=target_type, target_id=instance.pk)
    if target_type == "Client":
        cases = Case.objects.for_facility(facility_id).filter(client_id=instance.pk).values("pk")
        targets |= Q(target_type="Case", target_id__in=cases)
        targets |= Q(target_type="Episode", target_id__in=Episode.objects.filter(case_id__in=cases).values("pk"))
        targets |= Q(
            target_type="WorkItem",
            target_id__in=WorkItem.objects.for_facility(facility_id).filter(client_id=instance.pk).values("pk"),
        )
        targets |= Q(
            target_type="Event",
            target_id__in=Event.objects.for_facility(facility_id).filter(client_id=instance.pk).values("pk"),
        )
    elif target_type == "Case":
        targets |= Q(target_type="Episode", target_id__in=Episode.objects.filter(case_id=instance.pk).values("pk"))
    FreeTextReview.objects.for_facility(facility_id).filter(targets).delete()
