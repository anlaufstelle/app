"""Pins for facility-scoped free-text review contracts.

Source: mutation delta of the release-0.99.9 checkpoint, 71 mutants.
Groups: authorization, content identity, field visibility, pagination, row locks,
review isolation, audit metadata, filtering and recovery messages.
"""

import hashlib
import hmac
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, FreeTextReview
from core.services import free_text_review as service

pytestmark = pytest.mark.django_db
ROW_LOCK = re.compile(r"\bFOR (?:NO KEY UPDATE|UPDATE|SHARE)\b")


@pytest.fixture
def event_factory(facility, staff_user, doc_type_contact):
    """Create readable events through the ordinary model API."""

    def create(**overrides):
        values = {
            "facility": facility,
            "document_type": doc_type_contact,
            "created_by": staff_user,
            "occurred_at": timezone.now(),
            "data_json": {"notiz": "Neutral text"},
        }
        values.update(overrides)
        return Event.objects.create(**values)

    return create


def _mark(facility, user, event, key="notiz"):
    """Mark the current text through the public service API."""
    entry = service.get_entry(facility, user, "Event", event.pk, key)
    return service.mark_review(facility, user, "Event", event.pk, key, timezone.localdate(), entry.fingerprint)


def _seed_review(facility, user, event, *, target_type="Event", key="notiz", fingerprint="f" * 64):
    """Store historical review metadata independently of fingerprint generation."""
    return FreeTextReview.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=event.pk,
        field_key=key,
        reviewed_by=user,
        reviewed_until=timezone.localdate(),
        content_fingerprint=fingerprint,
    )


@pytest.mark.parametrize("state", ["system_admin", "inactive", "missing_facility"])
@pytest.mark.parametrize("operation", ["list", "lookup"])
def test_authorization_rejects_each_invalid_context(
    facility, staff_user, super_admin_user, event_factory, state, operation
):
    """Both read APIs reject each missing prerequisite independently.

    x__authorize__mutmut_2, x__authorize__mutmut_3, x__authorize__mutmut_4.
    """
    event = event_factory()
    user = staff_user
    scope = facility
    if state == "system_admin":
        user = super_admin_user
        user.facility = facility
    elif state == "inactive":
        user.is_active = False
    else:
        scope = None
    with pytest.raises(PermissionDenied):
        if operation == "list":
            service.list_page(scope, user, "Event")
        else:
            service.get_entry(scope, user, "Event", event.pk, "notiz")


def test_historical_unicode_fingerprint_stays_valid(facility, staff_user, event_factory):
    """Stored v1 Unicode fingerprints remain valid for display and marking.

    x__entry__mutmut_5, x__entry__mutmut_6, x__entry__mutmut_8,
    x__entry__mutmut_10, x__entry__mutmut_16, x__entry__mutmut_17.
    """
    text = "Zoë’s résumé: zoe@example.org"
    event = event_factory(data_json={"notiz": text})
    payload = f'["{facility.pk}", "Event", "{event.pk}", "notiz", "{text}"]'.encode()
    key = hashlib.sha256(b"core.free_text_review.content.v1" + settings.SECRET_KEY.encode()).digest()
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    _seed_review(facility, staff_user, event, fingerprint=fingerprint)
    entry = service.get_entry(facility, staff_user, "Event", event.pk, "notiz")
    assert entry.fingerprint == fingerprint
    assert entry.is_reviewed is True
    marked = service.mark_review(facility, staff_user, "Event", event.pk, "notiz", timezone.localdate(), fingerprint)
    assert marked.content_fingerprint == fingerprint


def test_identical_text_cannot_reuse_another_event_token(facility, staff_user, event_factory):
    """A valid token for identical text on another event is rejected.

    x__entry__mutmut_7.
    """
    first, second = event_factory(), event_factory()
    token = service.get_entry(facility, staff_user, "Event", first.pk, "notiz").fingerprint
    with pytest.raises(ValidationError):
        service.mark_review(facility, staff_user, "Event", second.pk, "notiz", timezone.localdate(), token)
    assert not FreeTextReview.objects.for_facility(facility).filter(target_id=second.pk).exists()


@pytest.mark.parametrize("kind", ["foreign", "encrypted", "nontext"])
def test_ineligible_field_plaintext_never_enters_reviews(
    facility, staff_user, second_facility, event_factory, doc_type_contact, kind
):
    """Even legacy plaintext is excluded for foreign, encrypted and numeric fields.

    x__event_fields__mutmut_11, x__event_fields__mutmut_12, x__event_fields__mutmut_13.
    """
    field = FieldTemplate.objects.create(
        facility=second_facility if kind == "foreign" else facility,
        name="Excluded field",
        field_type="number" if kind == "nontext" else "textarea",
        is_encrypted=kind == "encrypted",
        sensitivity="normal",
    )
    DocumentTypeField.objects.bulk_create([DocumentTypeField(document_type=doc_type_contact, field_template=field)])
    event = event_factory()
    # Raw SQL represents legacy residue that ordinary writes would normalize.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core_event SET data_json = jsonb_build_object(%s, %s::text, %s, %s::text) WHERE id = %s",
            ["notiz", "Visible text", field.slug, "Legacy plaintext", event.pk],
        )
    assert [(e.target_id, e.field_key) for e in service.list_page(facility, staff_user, "Event").entries] == [
        (event.pk, "notiz")
    ]
    with pytest.raises(Http404):
        service.get_entry(facility, staff_user, "Event", event.pk, field.slug)


@pytest.mark.parametrize("collision", ["target_type", "reviewer"])
def test_editor_filter_removes_collisions_before_paging(facility, staff_user, lead_user, event_factory, collision):
    """Newer marks for another target type or reviewer cannot displace a match.

    x__editor_condition__mutmut_5, x__editor_condition__mutmut_6.
    """
    wanted = event_factory()
    Event.objects.filter(pk=wanted.pk).update(updated_at=timezone.now() - timedelta(days=2))
    _mark(facility, lead_user, wanted)
    for _ in range(3):
        other = event_factory()
        _seed_review(
            facility,
            lead_user if collision == "target_type" else staff_user,
            other,
            target_type="Client" if collision == "target_type" else "Event",
        )
    page = service.list_page(facility, staff_user, "Event", editor_id=str(lead_user.pk), per_page=2)
    assert [e.target_id for e in page.entries] == [wanted.pk]
    assert page.has_next() is False


def test_equal_timestamps_have_stable_descending_identity_order(facility, staff_user, event_factory):
    """Equal update times retain descending UUID order across page boundaries.

    x__limited_targets__mutmut_27.
    """
    events = [event_factory(id=UUID(int=index)) for index in (1, 2, 3, 4, 5)]
    Event.objects.filter(pk__in=[event.pk for event in events]).update(updated_at=timezone.now())
    seen = [
        entry.target_id
        for page in (1, 2, 3)
        for entry in service.list_page(facility, staff_user, "Event", page=page, per_page=2).entries
    ]
    assert seen == [event.pk for event in reversed(events)]


@pytest.mark.postgres
def test_parent_lock_is_scoped_and_precedes_event_lock(facility, staff_user, sample_event):
    """Marking locks the exact facility-scoped parent before the source event.

    x__locked_entry__mutmut_21, x__locked_entry__mutmut_22.
    """
    with CaptureQueriesContext(connection) as queries:
        _mark(facility, staff_user, sample_event)
    locks = [(index, q["sql"]) for index, q in enumerate(queries) if ROW_LOCK.search(q["sql"].upper())]
    parents = [(i, sql) for i, sql in locks if 'FROM "core_client"' in sql]
    sources = [(i, sql) for i, sql in locks if 'FROM "core_event"' in sql]
    assert parents and sources
    for index, sql in parents:
        where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0].replace("-", "")
        assert str(sample_event.client_id).replace("-", "") in where
        assert str(facility.pk).replace("-", "") in where
        assert index < min(i for i, _ in sources)


def test_editor_choices_ignore_other_target_types(facility, staff_user, lead_user, event_factory):
    """A colliding non-event review cannot contribute an event reviewer choice.

    x_list_editor_ids__mutmut_24.
    """
    event = event_factory()
    _seed_review(facility, lead_user, event, target_type="Client")
    assert service.list_editor_ids(facility, staff_user, "Event") == {staff_user.pk}


def test_changed_before_is_applied_before_the_page_slice(facility, staff_user, event_factory):
    """Newer excluded events cannot displace an older marked entry.

    x_list_page__mutmut_14, x_list_page__mutmut_20.
    """
    old = event_factory()
    cutoff = timezone.localdate() - timedelta(days=2)
    Event.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=3))
    _mark(facility, staff_user, old)
    for _ in range(3):
        event_factory()
    page = service.list_page(facility, staff_user, "Event", changed_before=cutoff, per_page=2)
    assert [e.target_id for e in page.entries] == [old.pk]
    assert page.entries[0].is_reviewed is True
    assert page.has_next() is False


@pytest.mark.parametrize("risk", ["", "email"])
def test_list_preserves_source_identity_and_review_metadata(facility, staff_user, event_factory, risk):
    """Both list paths preserve the reviewed target, timestamp and source link.

    x_list_page__mutmut_53, x_list_page__mutmut_78,
    x__entry__mutmut_24, x__entry__mutmut_28, x_list_page__mutmut_102.
    """
    event = event_factory(data_json={"notiz": "person@example.org"})
    _mark(facility, staff_user, event)
    event.refresh_from_db()
    expected = service.get_entry(facility, staff_user, "Event", event.pk, "notiz")
    page = service.list_page(facility, staff_user, "Event", risk=risk)
    assert len(page.entries) == 1
    entry = page.entries[0]
    assert entry.target_type == "Event"
    assert entry.fingerprint == expected.fingerprint
    assert entry.is_reviewed is True
    assert entry.changed_at == event.updated_at
    assert entry.source_url == reverse("core:event_detail", kwargs={"pk": event.pk})
    assert page.more is False
    assert page.has_next() is False


def test_risk_scan_reaches_the_third_batch(facility, staff_user, event_factory, monkeypatch):
    """A marked match beyond two full batches is returned without repeating a slice.

    x_list_page__mutmut_99, x_list_page__mutmut_107, x_list_page__mutmut_108.
    """
    monkeypatch.setattr(service, "SCAN_BATCH", 2)
    wanted = event_factory(data_json={"notiz": "person@example.org"})
    Event.objects.filter(pk=wanted.pk).update(updated_at=timezone.now() - timedelta(days=1))
    _mark(facility, staff_user, wanted)
    for _ in range(4):
        event_factory()
    offsets = set()

    def reject_repeated_slice(execute, sql, params, many, context):
        if 'FROM "core_event"' in sql and "LIMIT" in sql:
            offset = re.search(r"\bOFFSET (\d+)", sql)
            if offset:
                value = int(offset.group(1))
                assert value not in offsets, "A source slice must not repeat during a risk scan"
                offsets.add(value)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(reject_repeated_slice):
        page = service.list_page(facility, staff_user, "Event", risk="email", per_page=2)
    assert [e.target_id for e in page.entries] == [wanted.pk]
    assert page.entries[0].is_reviewed is True
    assert page.has_next() is False


def test_revocation_preserves_other_types_targets_and_fields(facility, staff_user, event_factory, doc_type_contact):
    """Revoking one mark leaves colliding types, other events and sibling fields intact.

    x_revoke_review__mutmut_15, x_revoke_review__mutmut_16, x_revoke_review__mutmut_17.
    """
    extra = FieldTemplate.objects.create(facility=facility, name="Second text", field_type="textarea")
    DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=extra)
    first = event_factory(data_json={"notiz": "First text", extra.slug: "Second text"})
    second = event_factory()
    removed = _mark(facility, staff_user, first)
    kept = [
        _mark(facility, staff_user, second),
        _mark(facility, staff_user, first, extra.slug),
        _seed_review(facility, staff_user, first, target_type="Client"),
    ]
    service.revoke_review(facility, staff_user, "Event", first.pk, "notiz")
    assert not FreeTextReview.objects.for_facility(facility).filter(pk=removed.pk).exists()
    assert set(FreeTextReview.objects.for_facility(facility).values_list("pk", flat=True)) == {r.pk for r in kept}
    assert service.get_entry(facility, staff_user, "Event", second.pk, "notiz").is_reviewed is True
    assert service.get_entry(facility, staff_user, "Event", first.pk, extra.slug).is_reviewed is True


def test_readable_type_without_text_fields_returns_not_found(facility, staff_user, event_factory):
    """A readable type with no reviewable fields yields an empty list and a 404.

    x__entries__mutmut_12, x__entries__mutmut_14.
    """
    document_type = DocumentType.objects.create(facility=facility, name="No text fields")
    event = event_factory(document_type=document_type)
    assert service.list_page(facility, staff_user, "Event").entries == []
    with pytest.raises(Http404):
        service.get_entry(facility, staff_user, "Event", event.pk, "notiz")


def test_direct_filter_defaults_preserve_entries(facility, staff_user, event_factory):
    """The direct filtering API has no implicit risk restriction.

    x_filter_entries__mutmut_1.
    """
    event = event_factory()
    _mark(facility, staff_user, event)
    entry = service.get_entry(facility, staff_user, "Event", event.pk, "notiz")
    assert service.filter_entries([entry]) == [entry]


def test_direct_cutoff_uses_entry_date_inclusively(facility, staff_user, event_factory, monkeypatch):
    """Historical entries changed on the selected date survive the inclusive cutoff.

    x_filter_entries__mutmut_6, x_filter_entries__mutmut_7.
    """
    event = event_factory()
    _mark(facility, staff_user, event)
    entry = service.get_entry(facility, staff_user, "Event", event.pk, "notiz")
    past = datetime(2020, 2, 4, 12, tzinfo=UTC)
    monkeypatch.setattr(timezone, "now", lambda: past + timedelta(days=30))
    entry = replace(entry, changed_at=past)
    assert service.filter_entries([entry], changed_before=timezone.localtime(past).date()) == [entry]


def test_direct_editor_filter_requires_the_selected_reviewer(facility, staff_user, lead_user, event_factory):
    """Another author's entries match only an actual mark by the selected reviewer.

    x_filter_entries__mutmut_14.
    """
    matching, other, unmarked = event_factory(), event_factory(), event_factory()
    _mark(facility, lead_user, matching)
    _mark(facility, staff_user, other)
    entries = [service.get_entry(facility, staff_user, "Event", e.pk, "notiz") for e in (matching, other, unmarked)]
    assert service.filter_entries(entries, editor_id=str(lead_user.pk)) == entries[:1]


def test_editor_filter_excludes_unreviewed_sibling_fields(
    facility, staff_user, lead_user, event_factory, doc_type_contact
):
    """A reviewer's one marked field does not admit unreviewed sibling fields.

    x_list_page__mutmut_37, x_list_page__mutmut_40, x_list_page__mutmut_51.
    """
    field = FieldTemplate.objects.create(facility=facility, name="Sibling text", field_type="textarea")
    DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=field)
    event = event_factory(data_json={"notiz": "Reviewed text", field.slug: "Unreviewed text"})
    _mark(facility, lead_user, event)
    page = service.list_page(facility, staff_user, "Event", editor_id=str(lead_user.pk))
    assert [(e.target_id, e.field_key) for e in page.entries] == [(event.pk, "notiz")]


def test_full_final_page_has_no_next_link(facility, staff_user, event_factory):
    """Exactly filling the last page does not advertise another page.

    x_list_page__mutmut_66.
    """
    event_factory()
    page = service.list_page(facility, staff_user, "Event", per_page=1)
    assert len(page.entries) == 1
    assert page.has_next() is False


def test_mark_audit_preserves_actor_target_and_details(facility, staff_user, event_factory):
    """Marking records the actor, exact event, field and ISO review deadline.

    x_mark_review__mutmut_49, x_mark_review__mutmut_51, x_mark_review__mutmut_52,
    x_mark_review__mutmut_53, x_mark_review__mutmut_57, x_mark_review__mutmut_58, x_mark_review__mutmut_59.
    """
    event = event_factory()
    review = _mark(facility, staff_user, event)
    audit = AuditLog.objects.for_facility(facility).get(action=AuditLog.Action.FREE_TEXT_REVIEWED)
    assert audit.user_id == staff_user.pk
    assert audit.target_type == "Event"
    assert audit.target_id == str(event.pk)
    assert audit.detail == {"review_field": "notiz", "reviewed_until": review.reviewed_until.isoformat()}


def test_revoke_audit_preserves_actor_target_and_field(facility, staff_user, event_factory):
    """Revoking records the actor, exact event and revoked field.

    x_revoke_review__mutmut_20, x_revoke_review__mutmut_22, x_revoke_review__mutmut_23,
    x_revoke_review__mutmut_24, x_revoke_review__mutmut_28, x_revoke_review__mutmut_29, x_revoke_review__mutmut_30.
    """
    event = event_factory()
    _mark(facility, staff_user, event)
    service.revoke_review(facility, staff_user, "Event", event.pk, "notiz")
    audit = AuditLog.objects.for_facility(facility).get(action=AuditLog.Action.FREE_TEXT_REVIEW_REVOKED)
    assert audit.user_id == staff_user.pk
    assert audit.target_type == "Event"
    assert audit.target_id == str(event.pk)
    assert audit.detail == {"review_field": "notiz"}


def test_navigation_links_move_one_page(facility, staff_user, event_factory):
    """The first page has no predecessor and navigation advances one page at a time.

    xǁReviewPageǁhas_previous__mutmut_1,
    xǁReviewPageǁnext_page_number__mutmut_1, xǁReviewPageǁnext_page_number__mutmut_2,
    xǁReviewPageǁprevious_page_number__mutmut_1, xǁReviewPageǁprevious_page_number__mutmut_2.
    """
    for _ in range(3):
        event_factory()
    first, middle, last = [service.list_page(facility, staff_user, "Event", page=p, per_page=1) for p in (1, 2, 3)]
    assert first.has_previous() is False
    assert first.next_page_number() == 2
    assert middle.has_previous() is True
    assert middle.previous_page_number() == 1
    assert middle.next_page_number() == 3
    assert last.previous_page_number() == 2


@pytest.mark.parametrize("missing", ["target_type", "field", "target"])
def test_missing_target_errors_keep_their_explanation(facility, staff_user, event_factory, missing):
    """Unknown types, missing fields and missing lock snapshots explain the 404.

    x__targets__mutmut_6, x_get_entry__mutmut_24, x__locked_entry__mutmut_16.
    """
    event = event_factory()
    with pytest.raises(Http404) as error:
        if missing == "target_type":
            service.get_entry(facility, staff_user, "Unknown", event.pk, "notiz")
        elif missing == "field":
            service.get_entry(facility, staff_user, "Event", event.pk, "missing")
        else:
            service.mark_review(facility, staff_user, "Event", uuid4(), "notiz", timezone.localdate(), "stale")
    assert str(error.value) == "Review target not found"


@pytest.mark.postgres
@pytest.mark.parametrize("change", ["disappear", "reassign"])
def test_changes_before_source_lock_explain_recovery(facility, staff_user, sample_event, change):
    """A source disappearing or changing parent before its lock gives the recovery message.

    x__locked_entry__mutmut_29, x__locked_entry__mutmut_34, x__locked_entry__mutmut_35.
    """
    entry = service.get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    changed = False

    def change_before_lock(execute, sql, params, many, context):
        nonlocal changed
        if not changed and 'FROM "core_event"' in sql and ROW_LOCK.search(sql):
            changed = True
            with connection.cursor() as cursor:
                if change == "disappear":
                    cursor.execute("UPDATE core_event SET is_deleted = TRUE WHERE id = %s", [sample_event.pk])
                else:
                    cursor.execute("UPDATE core_event SET client_id = NULL WHERE id = %s", [sample_event.pk])
        return execute(sql, params, many, context)

    expected_error = Http404 if change == "disappear" else ValidationError
    with connection.execute_wrapper(change_before_lock), pytest.raises(expected_error) as error:
        service.mark_review(
            facility, staff_user, "Event", sample_event.pk, "notiz", timezone.localdate(), entry.fingerprint
        )
    assert changed is True
    if change == "disappear":
        assert str(error.value) == "Review target not found"
    else:
        assert error.value.messages == [gettext("Die Zuordnung wurde geändert. Bitte laden Sie die Liste neu.")]
    assert not FreeTextReview.objects.for_facility(facility).exists()


@pytest.mark.parametrize("invalid", ["date", "fingerprint"])
def test_invalid_mark_explains_how_to_retry(facility, staff_user, event_factory, invalid):
    """Invalid deadlines and changed text retain their exact translated recovery advice.

    x_mark_review__mutmut_15, x_mark_review__mutmut_16, x_mark_review__mutmut_25.
    """
    event = event_factory()
    entry = service.get_entry(facility, staff_user, "Event", event.pk, "notiz")
    until, token = timezone.localdate(), entry.fingerprint
    if invalid == "date":
        until -= timedelta(days=1)
        message = gettext("Bitte wählen Sie heute oder ein zukünftiges Datum.")
    else:
        token = "stale"
        message = gettext("Der Text wurde geändert. Bitte laden Sie die Liste neu und prüfen Sie ihn erneut.")
    with pytest.raises(ValidationError) as error:
        service.mark_review(facility, staff_user, "Event", event.pk, "notiz", until, token)
    assert error.value.messages == [message]
    assert not FreeTextReview.objects.for_facility(facility).exists()
