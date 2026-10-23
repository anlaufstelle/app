"""Resolve reviewable text through current facility, person and role boundaries."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import CharField, Exists, Func, OuterRef, Q
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.lookups import Exact, Regex
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Case, Client, DocumentTypeField, Episode, Event, FreeTextReview, WorkItem
from core.services.audit import audit_event
from core.services.compliance.sensitivity import user_can_see_field
from core.services.dynamic_protection import lock_dynamic_protection
from core.services.sensitive_patterns import PATTERN_LABELS, detect_patterns
from core.services.workitem_visibility import readable_person_filter, workitems_for_read

TARGET_LABELS = {
    "Client": _("Personen-Notizen"),
    "Case": _("Fallbeschreibungen"),
    "Episode": _("Episodenbeschreibungen"),
    "WorkItem": _("Aufgabenbeschreibungen"),
    "Event": _("Ereignis-Freitexte"),
}
PAGE_SIZE = 50
# Far beyond any realistic table; keeps the SQL offset inside the database integer range.
MAX_PAGE = 1_000_000
SCAN_BATCH = 200
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
    source_url: str
    qualified_client_id: UUID | None = None

    @property
    def pattern_labels(self):
        return [PATTERN_LABELS[key] for key in detect_patterns(self.text)]

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
        reverse(
            {
                "Client": "core:client_detail",
                "Case": "core:case_detail",
                "Episode": "core:case_detail",
                "WorkItem": "core:workitem_detail",
                "Event": "core:event_detail",
            }[target_type],
            kwargs={"pk": target.case_id if target_type == "Episode" else target.pk},
        ),
        # Only the person detail page audits reads of qualified persons; mirror exactly that scope.
        target.pk if target_type == "Client" and target.contact_stage == Client.ContactStage.QUALIFIED else None,
    )


def _event_fields(facility, user):
    """Map document type id to the text slugs the user may see; one query for all types."""
    fields = DocumentTypeField.objects.filter(
        document_type__facility=facility,
        field_template__facility=facility,
        field_template__is_encrypted=False,
        field_template__field_type__in=["text", "textarea"],
    ).select_related("field_template", "document_type")
    visible = {}
    for field in fields:
        if user_can_see_field(user, field.document_type.sensitivity, field.field_template.sensitivity):
            visible.setdefault(field.document_type_id, []).append(field.field_template.slug)
    return visible


def _entries(facility, target_type, targets, event_fields):
    """Build entries for an already limited list of targets; marks are loaded in one query."""
    reviews = {
        (review.target_id, review.field_key): review
        for review in FreeTextReview.objects.for_facility(facility).filter(
            target_type=target_type, target_id__in=[target.pk for target in targets]
        )
    }
    entries = []
    for target in targets:
        if target_type == "Event":
            values = [(key, target.data_json.get(key)) for key in event_fields.get(target.document_type_id, ())]
        else:
            key = STATIC_FIELDS[target_type]
            values = [(key, getattr(target, key))]
        for key, text in values:
            # Marker dictionaries, unknown keys and non-text values never enter a review.
            if isinstance(text, str) and text.strip():
                entries.append(_entry(facility, target_type, target, key, text, reviews))
    return entries


def _changed_column(target_type):
    # Episodes carry no modification timestamp; the creation time stands in for it.
    return "created_at" if target_type == "Episode" else "updated_at"


def _text_value(key):
    """Condition: the JSON value at ``key`` is a string with at least one non-whitespace character."""
    kind = Func(KeyTransform(key, "data_json"), function="jsonb_typeof", output_field=CharField())
    return Q(Exact(kind, "string")) & Q(Regex(KeyTextTransform(key, "data_json"), r"\S"))


def _event_condition(event_fields, extra=None):
    """Events holding a nonblank value in a field the user may see; ``extra(key)`` narrows per field."""
    condition = Q(pk__in=[])
    for document_type_id, keys in event_fields.items():
        for key in keys:
            field = _text_value(key)
            condition |= Q(document_type_id=document_type_id) & (field & extra(key) if extra else field)
    return condition


def _has_text(target_type, event_fields):
    """Database prefilter: rows that can yield at least one nonblank text entry."""
    if target_type != "Event":
        return Q(**{f"{STATIC_FIELDS[target_type]}__regex": r"\S"})
    return _event_condition(event_fields)


def _editor_condition(facility, target_type, event_fields, editor_id):
    """Rows whose built entries include one the editor wrote or reviewed, decided before paging."""
    reviews = FreeTextReview.objects.for_facility(facility).filter(
        target_type=target_type, reviewed_by_id=editor_id, target_id=OuterRef("pk")
    )
    if target_type != "Event":
        return Q(created_by_id=editor_id) | Exists(reviews.filter(field_key=STATIC_FIELDS[target_type]))
    # Authorship counts for any event that yields an entry; a review only for the field it marks.
    return Q(created_by_id=editor_id) | _event_condition(
        event_fields, lambda key: Exists(reviews.filter(field_key=key))
    )


def _limited_targets(facility, user, target_type, event_fields, *, changed_before=None, editor_id=""):
    """Visible targets that can carry text, narrowed and ordered in the database."""
    qs = _targets(facility, user, target_type).filter(_has_text(target_type, event_fields))
    column = _changed_column(target_type)
    if changed_before is not None:
        qs = qs.filter(**{f"{column}__date__lte": changed_before})
    if editor_id:
        qs = qs.filter(_editor_condition(facility, target_type, event_fields, editor_id))
    return qs.order_by(f"-{column}", "-pk")


@dataclass
class ReviewPage:
    """One page of entries; the total is deliberately unknown (no full count)."""

    entries: list
    number: int
    more: bool

    def has_next(self):
        return self.more

    def has_previous(self):
        return self.number > 1

    def next_page_number(self):
        return self.number + 1

    def previous_page_number(self):
        return self.number - 1


def list_page(facility, user, target_type, *, page=1, per_page=PAGE_SIZE, changed_before=None, editor_id="", risk=""):
    """Return one page of readable nonempty source fields, newest first.

    Without a pattern filter the page is cut in the database and only its rows are
    built. A pattern filter is heuristic and cannot run in SQL, so the ordered rows
    are scanned in bounded batches; only the requested window is kept and the scan
    stops as soon as one more match proves that another page exists.
    """
    event_fields = _event_fields(facility, user) if target_type == "Event" else {}
    qs = _limited_targets(facility, user, target_type, event_fields, changed_before=changed_before, editor_id=editor_id)
    page = max(1, page)
    start = (page - 1) * per_page
    keep = dict(changed_before=changed_before, editor_id=editor_id, risk=risk)
    if not risk:
        rows = list(qs[start : start + per_page + 1])
        entries = filter_entries(_entries(facility, target_type, rows[:per_page], event_fields), **keep)
        return ReviewPage(entries, page, len(rows) > per_page)
    window, matched, offset = [], 0, 0
    while True:
        batch = list(qs[offset : offset + SCAN_BATCH])
        for entry in filter_entries(_entries(facility, target_type, batch, event_fields), **keep):
            if matched >= start + per_page:
                return ReviewPage(window, page, True)
            if matched >= start:
                window.append(entry)
            matched += 1
        if len(batch) < SCAN_BATCH:
            return ReviewPage(window, page, False)
        offset += SCAN_BATCH


def list_editor_ids(facility, user, target_type):
    """Authors and reviewers of the currently visible, nonblank entries of one model."""
    event_fields = _event_fields(facility, user) if target_type == "Event" else {}
    visible = _limited_targets(facility, user, target_type, event_fields)
    reviews = FreeTextReview.objects.for_facility(facility).filter(
        target_type=target_type, target_id__in=visible.order_by().values("pk")
    )
    if target_type == "Event":
        reviews = reviews.filter(field_key__in={key for keys in event_fields.values() for key in keys})
    ids = set(visible.order_by().values_list("created_by_id", flat=True).distinct())
    ids |= set(reviews.values_list("reviewed_by_id", flat=True).distinct())
    ids.discard(None)
    return ids


def get_entry(facility, user, target_type, target_id, field_key):
    """Resolve the target and field afresh; hidden and missing values share a 404."""
    event_fields = _event_fields(facility, user) if target_type == "Event" else {}
    targets = list(_targets(facility, user, target_type).filter(pk=target_id))
    for entry in _entries(facility, target_type, targets, event_fields):
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


def filter_entries(entries, *, changed_before=None, editor_id=None, risk=""):
    """Filter already-authorized entries without copying text to another store."""
    return [
        entry
        for entry in entries
        if (changed_before is None or timezone.localtime(entry.changed_at).date() <= changed_before)
        and (
            not editor_id
            or str(entry.author_id) == str(editor_id)
            or (entry.review is not None and str(entry.review.reviewed_by_id) == str(editor_id))
        )
        and (not risk or risk in detect_patterns(entry.text))
    ]
