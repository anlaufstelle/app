"""Selection references are rechecked after acquiring the configuration gate."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as Signal

import pytest
from django.core.exceptions import ValidationError
from django.db import connections
from django.db.models.signals import pre_save
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client, DocumentTypeField, Event, EventHistory, FieldTemplate, QuickTemplate
from core.services.case import create_case, update_case
from core.services.client import create_client, update_client
from core.services.events import create_event, update_event
from core.services.file_vault import encrypt_field, safe_decrypt
from tests._factories import make_document_type
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["select", "multi_select"])
def selection(facility, request):
    target = make_document_type(facility=facility, name="Selection target")
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Selection field",
        field_type=request.param,
        options_json=[{"slug": "chosen", "label": "Chosen"}, {"slug": "retained", "label": "Retained"}],
    )
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    value = "chosen" if request.param == "select" else ["chosen"]
    return target, field, value


def event_instance(facility, user, target, data):
    return Event(facility=facility, created_by=user, document_type=target, occurred_at=timezone.now(), data_json=data)


def remove_choice(field):
    field.options_json = [{"slug": "retained", "label": "Retained"}]
    field.save(update_fields=frozenset({"options_json"}))


@pytest.mark.parametrize(
    "path", ["service_create", "service_update", "model_create", "model_update", "bulk_create", "bulk_update"]
)
@pytest.mark.parametrize("encrypted", [False, True])
def test_event_writes_reject_removed_option(facility, lead_user, selection, _encryption_key, path, encrypted):
    target, field, value = selection
    field.is_encrypted = encrypted
    field.save(update_fields=["is_encrypted"])
    existing = create_event(facility, lead_user, target, timezone.now(), {})
    incoming = encrypt_field(value) if encrypted else value
    remove_choice(field)
    payload = {field.slug: incoming}
    history_count = EventHistory.objects.filter(event=existing).count()
    with pytest.raises(ValidationError) as caught:
        if path == "service_create":
            create_event(facility, lead_user, target, timezone.now(), payload)
        elif path == "service_update":
            update_event(existing, lead_user, payload)
        elif path == "model_create":
            event_instance(facility, lead_user, target, payload).save()
        elif path == "bulk_create":
            Event.objects.bulk_create([event_instance(facility, lead_user, target, payload)])
        else:
            existing.data_json = payload
            if path == "model_update":
                existing.save(update_fields=["data_json"])
            else:
                Event.objects.bulk_update([existing], ["data_json"])
    assert field.slug in caught.value.message_dict
    assert Event.objects.for_facility(facility).count() == 1
    existing.refresh_from_db()
    assert existing.data_json == {}
    assert EventHistory.objects.filter(event=existing).count() == history_count


@pytest.mark.parametrize("entity", ["client", "case"])
@pytest.mark.parametrize(
    "path", ["service_create", "service_update", "model_create", "model_update", "bulk_create", "bulk_update"]
)
def test_person_and_case_writes_reject_removed_option(facility, lead_user, client_identified, selection, entity, path):
    target, field, value = selection
    target = make_document_type(facility=facility, name="Entity selection target", entity_type=entity)
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    model = Client if entity == "client" else Case
    if entity == "client":
        existing = create_client(facility, lead_user, pseudonym="Existing selection", document_type=target)
        instance = Client(facility=facility, created_by=lead_user, pseudonym="New selection", document_type=target)
    else:
        existing = create_case(facility, lead_user, client_identified, "Existing selection", document_type=target)
        instance = Case(
            facility=facility,
            created_by=lead_user,
            client=client_identified,
            title="New selection",
            document_type=target,
        )
    remove_choice(field)
    payload = {field.slug: value}
    with pytest.raises(ValidationError) as caught:
        if path == "service_create":
            if entity == "client":
                create_client(
                    facility, lead_user, pseudonym="Rejected selection", document_type=target, data_json=payload
                )
            else:
                create_case(
                    facility,
                    lead_user,
                    client_identified,
                    "Rejected selection",
                    document_type=target,
                    data_json=payload,
                )
        elif path == "service_update":
            if entity == "client":
                update_client(existing, lead_user, data_json=payload)
            else:
                update_case(existing, lead_user, data_json=payload)
        elif path in {"model_create", "bulk_create"}:
            instance.data_json = payload
            if path == "model_create":
                instance.save()
            else:
                model.objects.bulk_create([instance])
        else:
            existing.data_json = payload
            if path == "model_update":
                existing.save(update_fields=["data_json"])
            else:
                model.objects.bulk_update([existing], ["data_json"])
    assert field.slug in caught.value.message_dict
    existing.refresh_from_db()
    assert existing.data_json == {}
    assert model.objects.for_facility(facility).filter(document_type=target).count() == 1


@pytest.mark.parametrize("path", ["create", "update"])
def test_quick_template_writes_reject_removed_option(facility, selection, path):
    target, field, value = selection
    existing = QuickTemplate.objects.create(facility=facility, document_type=target, name="Existing")
    remove_choice(field)
    with pytest.raises(ValidationError) as caught:
        if path == "create":
            QuickTemplate.objects.create(
                facility=facility, document_type=target, name="Rejected", prefilled_data={field.slug: value}
            )
        else:
            existing.prefilled_data = {field.slug: value}
            existing.save(update_fields=["prefilled_data"])
    assert field.slug in caught.value.message_dict
    existing.refresh_from_db()
    assert existing.prefilled_data == {}
    assert QuickTemplate.objects.for_facility(facility).count() == 1


@pytest.mark.parametrize("path", ["service_update", "model_update", "bulk_update", "quick_update"])
@pytest.mark.parametrize("legacy", [False, True])
def test_existing_selections_remain_writable(facility, lead_user, selection, _encryption_key, path, legacy):
    target, field, value = selection
    field.is_encrypted = path != "quick_update"
    if legacy:
        field.field_type = "text"
        field.options_json = []
    field.save()
    if path == "quick_update":
        existing = QuickTemplate.objects.create(
            facility=facility, document_type=target, name="Preserved", prefilled_data={field.slug: value}
        )
    else:
        existing = create_event(facility, lead_user, target, timezone.now(), {field.slug: value})
    if legacy:
        field.field_type = "select" if isinstance(value, str) else "multi_select"
        # Refs #2572: construct a historical selection without a new type change.
        FieldTemplate.objects.for_facility(facility).filter(pk=field.pk).update(field_type=field.field_type)
    else:
        field.options_json[0]["is_active"] = False
        field.save()
    if path == "quick_update":
        existing.save(update_fields=["prefilled_data"])
    elif path == "service_update":
        update_event(existing, lead_user, {field.slug: value})
    elif path == "model_update":
        existing.data_json = {field.slug: value}
        existing.save(update_fields=["data_json"])
    else:
        existing.data_json = {field.slug: value}
        Event.objects.bulk_update([existing], ["data_json"])
    existing.refresh_from_db()
    stored = existing.prefilled_data if path == "quick_update" else existing.data_json
    assert safe_decrypt(stored[field.slug]) == value


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first", ["configuration", "reference"])
@pytest.mark.parametrize("path", ["service_create", "service_update", "quick_create"])
def test_concurrent_option_removal_and_reference_write(facility, lead_user, selection, first, path):
    target, field, value = selection
    existing = create_event(facility, lead_user, target, timezone.now(), {}) if path == "service_update" else None
    ready, release, second_started = Signal(), Signal(), Signal()
    pids = {}

    def pause_first(sender, instance, **kwargs):
        if instance.facility_id == facility.pk:
            ready.set()
            assert release.wait(10), "First writer was not released"

    def identify_second(kind):
        if first != kind:
            pids["second"] = connections["default"].connection.info.backend_pid
            second_started.set()

    def write_reference():
        identify_second("reference")
        try:
            if path == "quick_create":
                return QuickTemplate.objects.create(
                    facility=facility, document_type=target, name="Concurrent", prefilled_data={field.slug: value}
                ).pk
            if path == "service_update":
                return update_event(existing, lead_user, {field.slug: value}).pk
            return create_event(facility, lead_user, target, timezone.now(), {field.slug: value}).pk
        except ValidationError as error:
            assert field.slug in error.message_dict
            return "blocked"

    def write_configuration():
        identify_second("configuration")
        try:
            remove_choice(field)
            return "removed"
        except ValidationError as error:
            assert "options_json" in error.message_dict
            return "blocked"

    sender = FieldTemplate if first == "configuration" else QuickTemplate if path == "quick_create" else Event
    pre_save.connect(pause_first, sender=sender, weak=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            initial = pool.submit(run_connection, write_configuration if first == "configuration" else write_reference)
            try:
                assert ready.wait(5), "First writer did not reach persistence"
                second = pool.submit(
                    run_connection, write_reference if first == "configuration" else write_configuration
                )
                assert second_started.wait(5), "Second writer did not start"
                assert wait_for_completion_or_lock(second, pids["second"]), (
                    "Second writer did not wait on the database lock"
                )
            finally:
                release.set()
            initial_result = initial.result(timeout=12)
            second_result = second.result(timeout=12)
    finally:
        pre_save.disconnect(pause_first, sender=sender)
    field.refresh_from_db()
    assert second_result == "blocked"
    if first == "configuration":
        assert initial_result == "removed"
        assert "chosen" not in {option["slug"] for option in field.options_json}
        if existing is not None:
            existing.refresh_from_db()
            assert existing.data_json == {}
        else:
            model = QuickTemplate if path == "quick_create" else Event
            assert not model.objects.for_facility(facility).exists()
    else:
        assert "chosen" in {option["slug"] for option in field.options_json}


@pytest.mark.parametrize("path", ["create", "update"])
def test_event_view_maps_option_removal_after_form_validation_to_field_error(
    client, lead_user, facility, selection, monkeypatch, path
):
    target, field, value = selection
    client.force_login(lead_user)
    if path == "create":
        public_writer = create_event
        url = reverse("core:event_create")
    else:
        existing = create_event(facility, lead_user, target, timezone.now(), {})
        public_writer = update_event
        url = reverse("core:event_update", kwargs={"pk": existing.pk})

    def remove_then_write(*args, **kwargs):
        remove_choice(field)
        return public_writer(*args, **kwargs)

    monkeypatch.setattr(f"core.views.events.{path}_event", remove_then_write)
    payload = {
        "document_type": str(target.pk),
        "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        field.slug: value,
    }
    if path == "update":
        payload["expected_updated_at"] = existing.updated_at.isoformat()
    response = client.post(url, payload, HTTP_ACCEPT="application/json")
    assert response.status_code == 422
    assert field.slug in response.json()["errors"]
    assert not Event.objects.for_facility(facility).exclude(data_json={}).exists()


@pytest.mark.parametrize(
    "path", ["service_create", "service_update", "model_create", "model_update", "bulk_create", "bulk_update"]
)
def test_event_tuple_selection_rejects_removed_option(facility, lead_user, selection, _encryption_key, path):
    target, field, _ = selection
    field.field_type = FieldTemplate.FieldType.MULTI_SELECT
    field.save(update_fields=["field_type"])
    test_event_writes_reject_removed_option(
        facility, lead_user, (target, field, ("chosen",)), _encryption_key, path, False
    )


@pytest.mark.parametrize("path", ["create", "update"])
def test_quick_template_tuple_selection_rejects_removed_option(facility, selection, path):
    target, field, _ = selection
    field.field_type = FieldTemplate.FieldType.MULTI_SELECT
    field.save(update_fields=["field_type"])
    test_quick_template_writes_reject_removed_option(facility, (target, field, ("chosen",)), path)
