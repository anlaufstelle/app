"""Online standard fields replace the former episode and task text columns."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, Episode, WorkItem
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.services.case.episodes import episode_basis_token
from core.services.case.workitems import duplicate_recurring_workitem
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests._factories import make_document_type

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture(params=["episode", "workitem"])
def standard_record(request, facility, staff_user, case_open):
    load_package(facility, parse_catalog_package("default"))
    kind = request.param
    doc_type = DocumentType.objects.for_facility(facility).get(entity_type=kind)
    field = doc_type.fields.get().field_template
    kwargs = {"case": case_open, "started_at": timezone.localdate()} if kind == "episode" else {"facility": facility}
    model = Episode if kind == "episode" else WorkItem
    record = model.objects.create(
        **kwargs,
        created_by=staff_user,
        title="Protected record",
        document_type=doc_type,
        data_json={field.slug: "Stored standard value"},
    )
    return kind, record, field


def edit_url(kind, record):
    kwargs = {"pk": record.pk}
    if kind == "episode":
        kwargs["case_pk"] = record.case_id
    return reverse(f"core:{kind}_update", kwargs=kwargs)


def edit_payload(kind, record):
    payload = {"title": record.title, "document_type": str(record.document_type_id)}
    if kind == "episode":
        payload |= {"started_at": record.started_at.isoformat(), "basis_token": episode_basis_token(record)}
    else:
        payload |= {"item_type": "task", "priority": "normal", "expected_updated_at": record.updated_at.isoformat()}
    return payload


def test_render_default_dynamic_block_and_save(client, staff_user, standard_record):
    kind, record, field = standard_record
    client.force_login(staff_user)
    response = client.get(edit_url(kind, record))
    html = response.content.decode()
    assert 'name="description"' not in html
    assert f'name="{field.slug}"' in html
    assert "Stored standard value" in html
    assert "doc-type-switch-hint" in html
    assert "hx-get=" in html
    payload = edit_payload(kind, record) | {field.slug: "Changed standard value"}
    assert client.post(edit_url(kind, record), payload).status_code == 302
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == "Changed standard value"


@pytest.mark.parametrize("include_type", [False, True])
def test_create_view_stores_encrypted_standard_value(client, staff_user, standard_record, include_type):
    kind, existing, field = standard_record
    client.force_login(staff_user)
    payload = {"title": "Created through the editor", field.slug: "New protected value"}
    if include_type:
        payload["document_type"] = str(existing.document_type_id)
    if kind == "episode":
        url = reverse("core:episode_create", kwargs={"case_pk": existing.case_id})
        payload["started_at"] = timezone.localdate().isoformat()
        records = Episode.objects.filter(case=existing.case)
    else:
        url = reverse("core:workitem_create")
        payload |= {"item_type": "task", "priority": "normal"}
        records = WorkItem.objects.for_facility(existing.facility)
    assert client.post(url, payload).status_code == 302
    record = records.get(title=payload["title"])
    assert record.document_type_id == existing.document_type_id
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == "New protected value"


@pytest.mark.parametrize("actor", ["staff_user", "assistant_user"])
def test_task_inbox_row_shows_protected_standard_value(request, client, facility, staff_user, actor):
    user = request.getfixturevalue(actor)
    load_package(facility, parse_catalog_package("default"))
    doc_type = DocumentType.objects.for_facility(facility).get(entity_type="workitem")
    field = doc_type.fields.get().field_template
    WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        assigned_to=user,
        title="Visible task row",
        due_date=timezone.localdate(),
        priority="urgent",
        document_type=doc_type,
        data_json={field.slug: "Protected row value"},
    )
    client.force_login(user)
    response = client.get(reverse("core:workitem_inbox"))
    assert response.status_code == 200
    assert "Protected row value" in response.content.decode()


def test_confirmed_type_change_counts_loss(client, staff_user, facility, standard_record):
    kind, record, _field = standard_record
    client.force_login(staff_user)
    other = make_document_type(facility=facility, entity_type=kind, name="Other type")
    payload = edit_payload(kind, record) | {"document_type": str(other.pk), "confirm_data_loss": "1"}
    assert client.post(edit_url(kind, record), payload).status_code == 302
    record.refresh_from_db()
    assert record.document_type == other
    assert record.data_json == {}
    audit = AuditLog.objects.filter(target_id=str(record.pk), action=f"{kind}_update").latest("pk")
    assert audit.detail["discarded_field_count"] == 1


def test_read_standard_fields_for_staff(client, staff_user, standard_record):
    kind, record, _field = standard_record
    client.force_login(staff_user)
    url = (
        reverse("core:case_detail", args=[record.case_id])
        if kind == "episode"
        else reverse("core:workitem_detail", args=[record.pk])
    )
    assert "Stored standard value" in client.get(url).content.decode()


def test_assistant_reads_task_details_only(client, assistant_user, standard_record):
    kind, record, _field = standard_record
    client.force_login(assistant_user)
    url = (
        reverse("core:case_detail", args=[record.case_id])
        if kind == "episode"
        else reverse("core:workitem_detail", args=[record.pk])
    )
    response = client.get(url)
    assert ("Stored standard value" in response.content.decode()) is (kind == "workitem")


def test_dynamic_partial_preserves_stored_values(client, staff_user, standard_record):
    kind, record, field = standard_record
    client.force_login(staff_user)
    response = client.get(
        reverse(f"core:{kind}_fields_partial"), {"record": record.pk, "document_type": record.document_type_id}
    )
    assert response.status_code == 200
    assert f'name="{field.slug}"' in response.content.decode()
    assert "Stored standard value" in response.content.decode()


def test_unreadable_stored_value_still_offers_loss_confirmation(client, staff_user, standard_record):
    kind, record, field = standard_record
    record.data_json = {field.slug: {"__encrypted__": True, "value": "invalid"}}
    record.save(update_fields=["data_json"])
    client.force_login(staff_user)
    response = client.get(edit_url(kind, record))
    assert response.context["dynamic_values_at_risk"]["field_values"] == 1
    assert 'x-data="confirmDynamicDataLoss"' in response.content.decode()


@pytest.mark.parametrize("invalid_key", ["record", "document_type"])
def test_dynamic_partial_rejects_malformed_identifiers(client, staff_user, standard_record, invalid_key):
    kind, record, _field = standard_record
    client.force_login(staff_user)
    query = {"record": record.pk, "document_type": record.document_type_id}
    query[invalid_key] = "invalid"
    response = client.get(reverse(f"core:{kind}_fields_partial"), query)
    assert response.status_code == 404


def test_episode_conflict_shows_current_details_and_retains_draft(client, staff_user, case_open, facility):
    from tests._dynamic_test_data import entity_details

    record = Episode.objects.create(
        case=case_open,
        created_by=staff_user,
        title="Concurrent episode",
        started_at=timezone.localdate(),
        **entity_details(facility, "episode", "Original detail"),
    )
    payload = edit_payload("episode", record) | {"test-details-episode": "Retained draft"}
    record.data_json = {"test-details-episode": "Current server detail"}
    record.save(update_fields=["data_json"])
    client.force_login(staff_user)
    response = client.post(edit_url("episode", record), payload)
    assert response.status_code == 200
    assert "Retained draft" in response.content.decode()
    assert "Current server detail" in response.content.decode()
    record.refresh_from_db()
    assert safe_decrypt(record.data_json["test-details-episode"]) == "Current server detail"


def test_recurring_task_copies_protected_fields(facility, staff_user, case_open):
    load_package(facility, parse_catalog_package("default"))
    doc_type = DocumentType.objects.for_facility(facility).get(entity_type="workitem")
    field = doc_type.fields.get().field_template
    source = WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        title="Recurring task",
        recurrence="weekly",
        document_type=doc_type,
        data_json={field.slug: "Recurring protected detail"},
    )
    followup = duplicate_recurring_workitem(source, staff_user)
    assert followup.document_type_id == source.document_type_id
    followup.refresh_from_db()
    assert safe_decrypt(followup.data_json[field.slug]) == "Recurring protected detail"


@pytest.mark.parametrize("operation", ["direct", "status", "bulk"])
@pytest.mark.parametrize("with_client", [False, True])
def test_recurrence_locks_configuration_before_data_rows(facility, staff_user, case_open, operation, with_client):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from core.services.case.workitems import bulk_update_workitem_status, update_workitem_status
    from tests._dynamic_test_data import entity_details

    source = WorkItem.objects.create(
        facility=facility,
        client=case_open.client if with_client else None,
        created_by=staff_user,
        title="Recurring protected task",
        recurrence="weekly",
        **entity_details(facility, "workitem", "Protected recurring detail"),
    )
    with CaptureQueriesContext(connection) as queries:
        if operation == "direct":
            duplicate_recurring_workitem(source, staff_user)
        elif operation == "status":
            update_workitem_status(source, WorkItem.Status.DONE, staff_user)
        else:
            bulk_update_workitem_status([source], staff_user, WorkItem.Status.DONE)
    statements = [query["sql"].lower() for query in queries]
    gate = next(index for index, sql in enumerate(statements) if "pg_advisory_xact_lock_shared(2525" in sql)
    row_lock = next(index for index, sql in enumerate(statements) if "for update" in sql)
    assert gate < row_lock
