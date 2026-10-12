"""Pins from the mutation delta of the release-0.99.7 checkpoint, 24 mutants.

Groups: current protection at CAS (3), row binding (2), ordered history
continuation (2), disappearance (1), public search (2), related history
loading (1), snapshot exclusion (1), snapshot protection (5), and file
marker preservation (7).
Only the ordered-loop tests call a private helper: the public API builds a
set of protected slugs and cannot guarantee the stopper comes first.
No test requires rls_test_role or timing-based synchronization.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentTypeField, Event, EventHistory, FieldTemplate
from core.services import dynamic_protection
from core.services.file_vault import encrypt_field, is_encrypted_value, rotation, safe_decrypt
from tests.test_reencrypt_fields import raw_event_data_json, raw_history_payloads
from tests.test_sensitivity_transitions import make_record

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_rotation_rechecks_protection_after_candidate_preparation(facility, staff_user, monkeypatch):
    """A newly protected value is encrypted under the current write policy.

    x__locked_compare_and_swap__mutmut_21,
    x__locked_compare_and_swap__mutmut_22,
    x__locked_compare_and_swap__mutmut_23.
    """
    event, field = make_record(facility, staff_user, "event")
    Event.objects.filter(pk=event.pk).update(data_json={field.slug: "SecretToken", "old": encrypt_field("OldToken")})
    original = dynamic_protection.lock_dynamic_protection
    changed = False

    def change_before_lock(facility_id):
        nonlocal changed
        if not changed:
            FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=True)
            changed = True
        return original(facility_id)

    monkeypatch.setattr(dynamic_protection, "lock_dynamic_protection", change_before_lock)
    counts = rotation.rotate_all(dry_run=False)
    stored = raw_event_data_json(event)[field.slug]
    assert counts["events"] == 1
    assert is_encrypted_value(stored)
    assert safe_decrypt(stored) == "SecretToken"


@pytest.mark.parametrize("kind", ["event", "history"])
def test_rotation_changes_only_the_current_locked_row(facility, staff_user, monkeypatch, kind):
    """A sibling with identical CAS values stays untouched until its own turn.

    x__locked_compare_and_swap__mutmut_45,
    x__locked_compare_and_swap__mutmut_57.
    """
    event, field = make_record(facility, staff_user, "event")
    payload = {field.slug: encrypt_field("SharedToken")}
    if kind == "event":
        sibling = Event.objects.create(
            facility=facility,
            document_type=event.document_type,
            occurred_at=timezone.now(),
            created_by=staff_user,
            data_json={},
        )
        Event.objects.filter(pk__in=[event.pk, sibling.pk]).update(data_json=payload, updated_at=timezone.now())
        model, rows, column = Event, [event, sibling], "data_json"
    else:
        rows = [EventHistory.objects.create(event=event, action="update", data_after=payload) for _ in range(2)]
        model, column = EventHistory, "data_after"
    original = rotation._locked_compare_and_swap
    observed = []

    def inspect_sibling(target_model, pk, *, expected, updates):
        result = original(target_model, pk, expected=expected, updates=updates)
        if target_model is model and not observed:
            sibling_pk = next(row.pk for row in rows if row.pk != pk)
            untouched = model.objects.filter(pk=sibling_pk).values_list(column, flat=True).get()
            assert untouched == payload
            observed.append(pk)
        return result

    monkeypatch.setattr(rotation, "_locked_compare_and_swap", inspect_sibling)
    counts = rotation.rotate_all(dry_run=False)
    assert observed
    assert counts["events" if kind == "event" else "event_history"] == 2


@pytest.mark.parametrize("current_template", [False, True])
def test_rotation_heals_snapshot_protection_in_legacy_history(facility, staff_user, current_template):
    """Legacy plaintext remains protected by a removed or downgraded snapshot.

    x__reencrypt_event_history__mutmut_34,
    x__reencrypt_event_history__mutmut_35,
    x__reencrypt_event_history__mutmut_36,
    x__reencrypt_event_history__mutmut_37,
    x__reencrypt_event_history__mutmut_38.
    """
    event, field = make_record(facility, staff_user, "event")
    slug = field.slug if current_template else "removed-secret"
    history = EventHistory(
        event=event,
        action="update",
        data_before={slug: "BeforeSecret"},
        data_after={slug: "AfterSecret"},
        field_metadata={slug: {"is_encrypted": True}},
    )
    # Legacy inserts bypass save(), which otherwise protects snapshots up front.
    EventHistory.objects.bulk_create([history])
    assert raw_history_payloads(history) == ({slug: "BeforeSecret"}, {slug: "AfterSecret"})
    assert rotation.rotate_all(dry_run=False)["event_history"] == 1
    before, after = raw_history_payloads(history)
    for payload, plaintext in ((before, "BeforeSecret"), (after, "AfterSecret")):
        assert is_encrypted_value(payload[slug])
        assert safe_decrypt(payload[slug]) == plaintext


@pytest.mark.parametrize("stopper", ["missing", "encrypted", "file"])
def test_ordered_history_loop_continues_after_skipped_values(stopper):
    """Skipped values preceding plaintext never stop encryption of later keys.

    x__reencrypt_history_payload__mutmut_11,
    x__reencrypt_history_payload__mutmut_20.
    """
    first = {"encrypted": encrypt_field("AlreadyProtected"), "file": {"__file__": "attachment-id"}}
    payload = {"later": "LaterSecret"}
    if stopper in first:
        payload["first"] = first[stopper]
    rotated, changed = rotation._reencrypt_history_payload(payload, ("first", "later"))
    assert changed
    assert is_encrypted_value(rotated["later"])
    assert safe_decrypt(rotated["later"]) == "LaterSecret"
    if stopper == "file":
        assert rotated["first"] == payload["first"]
    assert payload["later"] == "LaterSecret"


def test_disappearance_after_facility_lookup_counts_as_contention(facility, staff_user, monkeypatch):
    """A row deleted before its lock is counted as contention, never success.

    x__locked_compare_and_swap__mutmut_18.
    """
    event, field = make_record(facility, staff_user, "event")
    Event.objects.filter(pk=event.pk).update(data_json={field.slug: encrypt_field("Candidate")})
    original = dynamic_protection.lock_dynamic_protection
    deleted = False

    def delete_before_lock(facility_id):
        nonlocal deleted
        if not deleted:
            Event.objects.filter(pk=event.pk).delete()
            deleted = True
        return original(facility_id)

    monkeypatch.setattr(dynamic_protection, "lock_dynamic_protection", delete_before_lock)
    counts = rotation.rotate_all(dry_run=False)
    assert deleted
    assert counts["events"] == 0
    assert counts["contended"] == 1


def test_rotation_keeps_public_normal_values_in_search(facility, staff_user):
    """Public values remain searchable alongside a rotated encrypted field.

    x__locked_compare_and_swap__mutmut_37,
    x__locked_compare_and_swap__mutmut_38.
    """
    event, field = make_record(facility, staff_user, "event", value="PublicToken")
    secret = FieldTemplate.objects.create(facility=facility, name="Private field", is_encrypted=True)
    DocumentTypeField.objects.create(document_type=event.document_type, field_template=secret)
    Event.objects.filter(pk=event.pk).update(
        data_json={field.slug: "PublicToken", secret.slug: encrypt_field("Secret")}
    )
    assert rotation.rotate_all(dry_run=False)["events"] == 1
    event.refresh_from_db()
    assert event.search_text == "PublicToken"
    assert safe_decrypt(raw_event_data_json(event)[secret.slug]) == "Secret"


def test_history_rotation_avoids_separate_related_row_fetches(facility, staff_user):
    """History traversal does not repeatedly fetch the same related rows.

    x__reencrypt_event_history__mutmut_14.
    """
    event, _ = make_record(facility, staff_user, "event")
    for _ in range(2):
        EventHistory.objects.create(event=event, action="update", data_after={"old": encrypt_field("HistoryToken")})
    with CaptureQueriesContext(connection) as captured:
        counts = rotation.rotate_all(dry_run=False)
    assert counts["event_history"] == 2
    # Repeated row fetches expose N+1 loading; caching or batching is allowed.
    lazy_fetches = [
        query["sql"]
        for query in captured.captured_queries
        if query["sql"].startswith('SELECT "core_facility".')
        or (
            query["sql"].startswith('SELECT "core_event".')
            and '"core_event"."facility_id"' in query["sql"]
            and 'JOIN "core_documenttype"' not in query["sql"]
            and not any(
                mode in query["sql"] for mode in ("FOR UPDATE", "FOR NO KEY UPDATE", "FOR SHARE", "FOR KEY SHARE")
            )
        )
    ]
    seen = set()
    repeated_fetches = []
    for sql in lazy_fetches:
        if sql in seen:
            repeated_fetches.append(sql)
        seen.add(sql)
    assert not repeated_fetches


@pytest.mark.parametrize("metadata", [{"is_encrypted": False}, "legacy-metadata"])
def test_unprotected_history_snapshot_stays_plaintext(facility, staff_user, metadata):
    """Removed unprotected fields stay plaintext and non-dict metadata is safe.

    x__reencrypt_event_history__mutmut_33.
    """
    event, _ = make_record(facility, staff_user, "event")
    history = EventHistory.objects.create(
        event=event,
        action="update",
        data_before={"removed": "PublicBefore"},
        data_after={"removed": "PublicAfter"},
        field_metadata={"removed": metadata},
    )
    assert rotation.rotate_all(dry_run=False)["event_history"] == 0
    assert raw_history_payloads(history) == ({"removed": "PublicBefore"}, {"removed": "PublicAfter"})


@pytest.mark.parametrize("marker", [{"__file__": "attachment-id"}, {"__files__": ["attachment-id"]}])
def test_history_rotation_preserves_file_marker_structures(facility, staff_user, marker):
    """Protected file marker dictionaries remain structured in both payloads.

    x__reencrypt_history_payload__mutmut_13,
    x__reencrypt_history_payload__mutmut_14,
    x__reencrypt_history_payload__mutmut_15,
    x__reencrypt_history_payload__mutmut_16,
    x__reencrypt_history_payload__mutmut_17,
    x__reencrypt_history_payload__mutmut_18,
    x__reencrypt_history_payload__mutmut_19.
    """
    event, _ = make_record(facility, staff_user, "event")
    history = EventHistory.objects.create(
        event=event,
        action="update",
        data_before={"file": marker, "secret": "BeforeSecret"},
        data_after={"file": marker, "secret": "AfterSecret"},
        field_metadata={"file": {"is_encrypted": True}, "secret": {"is_encrypted": True}},
    )
    assert rotation.rotate_all(dry_run=False)["event_history"] == 1
    before, after = raw_history_payloads(history)
    for payload, plaintext in ((before, "BeforeSecret"), (after, "AfterSecret")):
        assert payload["file"] == marker
        assert is_encrypted_value(payload["secret"])
        assert safe_decrypt(payload["secret"]) == plaintext
