"""Public write/search/maintenance contracts for effective sensitivity (Refs #2525)."""

from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, EventHistory, FieldTemplate
from core.services.dashboard import search_clients_and_events
from core.services.events import create_event, update_event

pytestmark = pytest.mark.django_db


def make_event(facility, user, type_level="normal", field_level="", *, encrypted=False, client=None):
    document_type = DocumentType.objects.create(
        facility=facility, name=f"Search type {uuid4()}", sensitivity=type_level
    )
    field = FieldTemplate.objects.create(
        facility=facility,
        name=f"Search field {uuid4()}",
        field_type=FieldTemplate.FieldType.TEXT,
        sensitivity=field_level,
        is_encrypted=encrypted,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    event = create_event(
        facility=facility,
        user=user,
        document_type=document_type,
        client=client,
        occurred_at=timezone.now(),
        data_json={field.slug: "NeedleToken"},
    )
    return event, field


@pytest.mark.parametrize("type_level", ["normal", "elevated", "high"])
@pytest.mark.parametrize(
    "field_level", ["", "normal", "elevated", "high"], ids=["inherit", "normal", "elevated", "high"]
)
def test_write_and_search_use_effective_level(facility, lead_user, client_identified, type_level, field_level):
    event, field = make_event(facility, lead_user, type_level, field_level, client=client_identified)
    indexed = type_level == "normal" and field_level in {"", "normal"}
    for token in ("NeedleToken", "UpdatedToken"):
        event.refresh_from_db()
        assert event.search_text == (token if indexed else "")
        _, matches = search_clients_and_events(facility, lead_user, token)
        assert (event in matches) is indexed
        _, pseudonym_matches = search_clients_and_events(facility, lead_user, client_identified.pseudonym)
        assert event in pseudonym_matches
        if token == "NeedleToken":
            update_event(event=event, user=lead_user, data_json={field.slug: "UpdatedToken"})


def test_encrypted_normal_field_is_never_indexed(facility, lead_user):
    event, _ = make_event(facility, lead_user, field_level="normal", encrypted=True)
    event.refresh_from_db()
    assert event.search_text == ""
    _, matches = search_clients_and_events(facility, lead_user, "NeedleToken")
    assert event not in matches


@pytest.mark.parametrize("position", ["type", "field"])
def test_unknown_level_is_not_indexed(facility, lead_user, position):
    event, field = make_event(facility, lead_user, field_level="normal")
    if position == "type":
        DocumentType.objects.filter(pk=event.document_type_id).update(sensitivity="unknown")
    else:
        FieldTemplate.objects.filter(pk=field.pk).update(sensitivity="unknown")
    event.refresh_from_db()
    event.save()
    event.refresh_from_db()
    assert event.search_text == ""


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
def test_rebuild_is_scoped_idempotent_and_preserves_source(facility, lead_user, foreign_event, dry_run):
    event, _ = make_event(facility, lead_user, "elevated")
    normal, _ = make_event(facility, lead_user)
    Event.objects.for_facility(facility).update(search_text="stale")
    Event.objects.for_facility(foreign_event.facility).filter(pk=foreign_event.pk).update(search_text="foreign stale")
    event.refresh_from_db()
    source = event.data_json.copy()
    updated_at = event.updated_at
    history = list(EventHistory.objects.filter(event=event).values())
    output = StringIO()
    call_command("rebuild_search_text", facility=str(facility.pk), dry_run=dry_run, stdout=output)
    event.refresh_from_db()
    normal.refresh_from_db()
    foreign_event.refresh_from_db()
    assert event.search_text == ("stale" if dry_run else "")
    assert normal.search_text == ("stale" if dry_run else "NeedleToken")
    assert foreign_event.search_text == "foreign stale"
    assert event.data_json == source
    assert event.updated_at == updated_at
    assert list(EventHistory.objects.filter(event=event).values()) == history
    assert "checked=2, changed=2" in output.getvalue()
    repeated = StringIO()
    call_command("rebuild_search_text", facility=str(facility.pk), dry_run=dry_run, stdout=repeated)
    assert f"changed={2 if dry_run else 0}" in repeated.getvalue()


def test_rebuild_without_filter_includes_other_facilities_and_deleted_events(facility, lead_user, foreign_event):
    event, _ = make_event(facility, lead_user, "high", "normal")
    Event.objects.for_facility(facility).filter(pk=event.pk).update(search_text="stale", is_deleted=True)
    Event.objects.for_facility(foreign_event.facility).filter(pk=foreign_event.pk).update(search_text="stale")
    call_command("rebuild_search_text", stdout=StringIO())
    event.refresh_from_db()
    foreign_event.refresh_from_db()
    assert event.search_text == ""
    assert foreign_event.search_text == ""


def test_rebuild_rejects_unknown_facility():
    with pytest.raises(CommandError, match="Facility .* not found"):
        call_command("rebuild_search_text", facility=str(uuid4()), stdout=StringIO())


def test_rebuild_requires_rls_bypass_context(monkeypatch):
    from core.management.commands import rebuild_search_text

    monkeypatch.setattr(rebuild_search_text, "has_rls_bypass_context", lambda: False)
    with pytest.raises(CommandError, match="RLS"):
        call_command("rebuild_search_text", stdout=StringIO())
