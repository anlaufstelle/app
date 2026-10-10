"""Condition references remain valid across concurrent option changes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as Signal

import pytest
from django.core.exceptions import ValidationError
from django.db import connections
from django.db.models.signals import pre_save
from django.urls import reverse

from core.forms.config import FieldConditionForm
from core.models import AuditLog, DocumentType, DocumentTypeField, FieldCondition, FieldTemplate
from core.services.config.conditions import create_field_condition, update_field_condition
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db
WRITE_PATHS = ["service_create", "service_update", "model_create", "model_update"]


@pytest.fixture(params=["select", "multi_select"])
def selection(facility, request):
    document_type = DocumentType.objects.create(facility=facility, name="Condition target")
    template = FieldTemplate.objects.create(
        facility=facility,
        name="Condition selection",
        field_type=request.param,
        options_json=[{"slug": "chosen", "label": "Chosen"}, {"slug": "retained", "label": "Retained"}],
    )
    dependent = FieldTemplate.objects.create(facility=facility, name="Dependent field")
    source = DocumentTypeField.objects.create(document_type=document_type, field_template=template)
    target = DocumentTypeField.objects.create(document_type=document_type, field_template=dependent)
    operator = FieldCondition.Operator.EQUALS if request.param == "select" else FieldCondition.Operator.CONTAINS
    return document_type, template, source, target, operator


def condition_request(rf, user, facility):
    request = rf.post("/config/")
    request.user = user
    request.current_facility = facility
    return request


def write_condition(request, selection, path, *, value="chosen", existing=None, group_index=0):
    document_type, _template, source, target, operator = selection
    values = dict(target_field=target, depends_on_field=source, operator=operator, value=value, group_index=group_index)
    if path == "service_create":
        return create_field_condition(request, document_type, **values)
    if path == "service_update":
        return update_field_condition(request, existing, **values)
    if path == "model_create":
        return FieldCondition.objects.create(**values)
    for key, item in values.items():
        setattr(existing, key, item)
    existing.save(update_fields=frozenset(values))
    return existing


def remove_choice(template):
    template = FieldTemplate.objects.get(pk=template.pk)
    template.options_json = [{"slug": "retained", "label": "Retained"}]
    template.save(update_fields=["options_json"])


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_condition_writes_reject_removed_option(facility, admin_user, rf, selection, path):
    request = condition_request(rf, admin_user, facility)
    existing = (
        write_condition(request, selection, "model_create", value="retained") if path.endswith("update") else None
    )
    remove_choice(selection[1])
    audit_count = AuditLog.objects.count()

    with pytest.raises(ValidationError) as caught:
        write_condition(request, selection, path, existing=existing)

    assert "value" in caught.value.message_dict
    assert AuditLog.objects.count() == audit_count
    if existing:
        existing.refresh_from_db()
        assert existing.value == "retained"
    else:
        assert not FieldCondition.objects.filter(target_field=selection[3]).exists()


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_condition_writes_accept_fresh_and_inactive_options(facility, admin_user, rf, selection, path):
    request = condition_request(rf, admin_user, facility)
    existing = (
        write_condition(request, selection, "model_create", value="retained") if path.endswith("update") else None
    )
    current = FieldTemplate.objects.get(pk=selection[1].pk)
    current.options_json.append({"slug": "added", "label": "Added", "is_active": False})
    current.save(update_fields=["options_json"])

    condition = write_condition(request, selection, path, value="added", existing=existing)

    condition.refresh_from_db()
    assert condition.value == "added"
    if path.startswith("service"):
        assert AuditLog.objects.filter(target_id=str(condition.pk)).count() == 1


@pytest.mark.parametrize("path", ["service_update", "model_update"])
def test_condition_writes_preserve_unchanged_legacy_value(facility, admin_user, rf, selection, path):
    request = condition_request(rf, admin_user, facility)
    template = selection[1]
    template.field_type = FieldTemplate.FieldType.TEXT
    template.save(update_fields=["field_type"])
    remove_choice(template)
    existing = write_condition(request, selection, "model_create")
    template.field_type = selection[2].field_template.field_type = (
        FieldTemplate.FieldType.SELECT
        if selection[4] == FieldCondition.Operator.EQUALS
        else FieldTemplate.FieldType.MULTI_SELECT
    )
    template.save(update_fields=["field_type"])

    condition = write_condition(request, selection, path, existing=existing, group_index=1)

    condition.refresh_from_db()
    assert condition.value == "chosen"
    assert condition.group_index == 1


@pytest.mark.parametrize("path", ["service_update", "model_update"])
def test_condition_source_change_rechecks_same_value(facility, admin_user, rf, selection, path):
    request = condition_request(rf, admin_user, facility)
    existing = write_condition(request, selection, "model_create")
    document_type, template, source, target, operator = selection
    other_template = FieldTemplate.objects.create(
        facility=facility, name="Other selection", field_type=template.field_type, options_json=[]
    )
    other_source = DocumentTypeField.objects.create(document_type=document_type, field_template=other_template)

    with pytest.raises(ValidationError) as caught:
        write_condition(
            request, (document_type, other_template, other_source, target, operator), path, existing=existing
        )

    assert "value" in caught.value.message_dict
    existing.refresh_from_db()
    assert existing.depends_on_field_id == source.pk


def test_condition_partial_save_uses_persisted_reference(facility, admin_user, rf, selection):
    request = condition_request(rf, admin_user, facility)
    existing = write_condition(request, selection, "model_create", value="retained")
    remove_choice(selection[1])
    existing.value = "chosen"
    existing.operator = FieldCondition.Operator.IS_EMPTY
    existing.group_index = 1

    existing.save(update_fields=iter(["group_index"]))

    existing.refresh_from_db()
    assert existing.value == "retained"
    assert existing.operator == selection[4]
    assert existing.group_index == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first", ["configuration", "condition"])
@pytest.mark.parametrize("path", WRITE_PATHS)
def test_concurrent_option_removal_and_condition_write(facility, admin_user, rf, selection, first, path):
    request = condition_request(rf, admin_user, facility)
    existing = (
        write_condition(request, selection, "model_create", value="retained") if path.endswith("update") else None
    )
    template = selection[1]
    ready, release, second_started = Signal(), Signal(), Signal()
    pids = {}

    def pause_first(sender, instance, **kwargs):
        if (sender is FieldTemplate and instance.pk != template.pk) or (
            sender is FieldCondition and instance.target_field_id != selection[3].pk
        ):
            return
        ready.set()
        assert release.wait(10), "First writer was not released"

    def identify_second(kind):
        if first != kind:
            pids["second"] = connections["default"].connection.info.backend_pid
            second_started.set()

    def persist_condition():
        identify_second("condition")
        try:
            return write_condition(request, selection, path, existing=existing).pk
        except ValidationError as error:
            assert "value" in error.message_dict
            return "blocked"

    def persist_configuration():
        identify_second("configuration")
        try:
            remove_choice(template)
            return "removed"
        except ValidationError as error:
            assert "options_json" in error.message_dict
            return "blocked"

    sender = FieldTemplate if first == "configuration" else FieldCondition
    pre_save.connect(pause_first, sender=sender, weak=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            initial = pool.submit(
                run_connection, persist_configuration if first == "configuration" else persist_condition
            )
            try:
                assert ready.wait(5), "First writer did not reach persistence"
                second = pool.submit(
                    run_connection, persist_condition if first == "configuration" else persist_configuration
                )
                assert second_started.wait(5), "Second writer did not start"
                assert wait_for_completion_or_lock(second, pids["second"]), (
                    "Second writer did not wait on a database lock"
                )
            finally:
                release.set()
            initial_result = initial.result(timeout=12)
            second_result = second.result(timeout=12)
    finally:
        pre_save.disconnect(pause_first, sender=sender)

    template.refresh_from_db()
    assert second_result == "blocked"
    if first == "configuration":
        assert initial_result == "removed"
        assert {option["slug"] for option in template.options_json} == {"retained"}
        if existing:
            existing.refresh_from_db()
            assert existing.value == "retained"
        else:
            assert not FieldCondition.objects.filter(target_field=selection[3]).exists()
    else:
        condition = FieldCondition.objects.get(pk=initial_result)
        assert condition.value in {option["slug"] for option in template.options_json}


@pytest.mark.parametrize("path", ["create", "update"])
def test_condition_ui_shows_removed_option_after_form_validation(client, admin_user, rf, selection, monkeypatch, path):
    document_type, template, source, target, operator = selection
    request = condition_request(rf, admin_user, document_type.facility)
    existing = write_condition(request, selection, "model_create", value="retained") if path == "update" else None
    original = FieldConditionForm.is_valid

    def validate_then_remove(form):
        valid = original(form)
        assert valid, form.errors
        remove_choice(template)
        return valid

    monkeypatch.setattr(FieldConditionForm, "is_valid", validate_then_remove)
    client.force_login(admin_user)
    kwargs = {"pk": document_type.pk}
    if existing:
        kwargs["condition_pk"] = existing.pk
    audit_count = AuditLog.objects.count()

    response = client.post(
        reverse(f"core:config_condition_{path}", kwargs=kwargs),
        dict(
            target_field=str(target.pk),
            depends_on_field=str(source.pk),
            operator=operator,
            value="chosen",
            group_index=0,
        ),
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert "value" in response.context["condition_form"].errors
    assert "Der Vergleichswert muss ein gültiger Options-Slug sein." in response.content.decode()
    assert AuditLog.objects.count() == audit_count
    if existing:
        existing.refresh_from_db()
        assert existing.value == "retained"
    else:
        assert not FieldCondition.objects.filter(target_field=target).exists()
