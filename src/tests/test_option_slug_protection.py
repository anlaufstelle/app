"""Public mutation paths preserve referenced option identifiers."""

import copy
import json
import re

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditLog,
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    EventHistory,
    FieldCondition,
    FieldTemplate,
    QuickTemplate,
)
from core.packages.loader import PackageLoadError, load_package
from core.packages.schema import parse_package
from core.services.file_vault.encryption import encrypt_field, generate_key
from core.services.option_usage import option_slugs_in_use

pytestmark = pytest.mark.django_db


@pytest.fixture
def option_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Selection",
        field_type=FieldTemplate.FieldType.MULTI_SELECT,
        options_json=[{"slug": "used", "label": "Used"}, {"slug": "unused", "label": "Unused"}],
    )


@pytest.fixture
def referenced_template(option_template, doc_type_contact):
    QuickTemplate.objects.create(
        facility=option_template.facility,
        document_type=doc_type_contact,
        name="Inactive reference",
        is_active=False,
        prefilled_data={option_template.slug: ["used"]},
    )
    return option_template


def _assert_protected(error):
    message = " ".join(error.messages)
    assert "used" in message
    assert "deaktivieren" in message
    assert "entfernen oder umbenennen" in message
    assert "options_json" in error.message_dict


def _post(template, options, **overrides):
    data = {
        "name": template.name,
        "field_type": template.field_type,
        "options_json": json.dumps(options),
        "default_value": template.default_value,
        "is_active": "on",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("source", ["quick_active", "quick_inactive", "default_single", "default_multiple"])
def test_usage_includes_saved_templates_and_valid_defaults(option_template, doc_type_contact, source):
    if source.startswith("quick"):
        QuickTemplate.objects.create(
            facility=option_template.facility,
            document_type=doc_type_contact,
            name="Reference",
            is_active=source == "quick_active",
            prefilled_data={option_template.slug: ["used"]},
        )
    else:
        option_template.field_type = "select" if source == "default_single" else "multi_select"
        option_template.default_value = "used" if source == "default_single" else "used, unused"
        option_template.save()
    expected = {"used", "unused"} if source == "default_multiple" else {"used"}
    assert option_slugs_in_use(option_template) == expected


@pytest.mark.parametrize("raw_default", ["missing", "used, missing", ""])
def test_usage_ignores_invalid_or_empty_defaults(option_template, raw_default):
    option_template.default_value = raw_default
    option_template.save()
    assert option_slugs_in_use(option_template) == set()


def test_quick_template_usage_is_facility_scoped(option_template, other_facility):
    document_type = DocumentType.objects.create(facility=other_facility, name="Other type")
    QuickTemplate.objects.create(
        facility=other_facility,
        document_type=document_type,
        name="Foreign reference",
        prefilled_data={option_template.slug: ["used"]},
    )
    assert option_slugs_in_use(option_template) == set()
    option_template.options_json = []
    option_template.save()


@pytest.mark.parametrize("path", ["save", "clean", "full_clean", "update_or_create", "save_existing_pk"])
@pytest.mark.parametrize("replacement", [[], [{"slug": "renamed", "label": "Used"}]])
def test_model_paths_reject_removal_or_rename(referenced_template, path, replacement):
    template = referenced_template
    previous = copy.deepcopy(template.options_json)
    template.options_json = replacement
    with pytest.raises(ValidationError) as caught:
        if path == "update_or_create":
            FieldTemplate.objects.update_or_create(
                facility=template.facility, pk=template.pk, defaults={"options_json": replacement}
            )
        elif path == "save_existing_pk":
            FieldTemplate(
                pk=template.pk,
                facility=template.facility,
                name=template.name,
                slug=template.slug,
                field_type=template.field_type,
                options_json=replacement,
            ).save()
        else:
            getattr(template, path)()
    _assert_protected(caught.value)
    template.refresh_from_db()
    assert template.options_json == previous


@pytest.mark.parametrize("replacement", [[], [{"slug": "renamed", "label": "Used"}]])
def test_config_post_rejects_removal_without_success_audit(client, admin_user, referenced_template, replacement):
    template = referenced_template
    client.force_login(admin_user)
    response = client.post(
        reverse("core:config_fieldtemplate_update", kwargs={"pk": template.pk}), _post(template, replacement)
    )
    assert response.status_code == 200
    error = str(response.context["form"].errors["options_json"])
    assert "used" in error and "deaktivieren" in error
    assert "entfernen oder umbenennen" in error
    template.refresh_from_db()
    assert {option["slug"] for option in template.options_json} == {"used", "unused"}
    assert not AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(template.pk)).exists()


def test_admin_post_shows_validation_error_without_success_audit(client, super_admin_user, referenced_template):
    template = referenced_template
    client.force_login(super_admin_user)
    response = client.post(
        reverse("admin:core_fieldtemplate_change", args=[template.pk]),
        _post(template, [], facility=str(template.facility_id), _save="Save"),
    )
    assert response.status_code == 200
    form = response.context["adminform"].form
    assert "options_json" in form.errors
    assert "used" in str(form.errors["options_json"])
    assert "deaktivieren" in str(form.errors["options_json"])
    template.refresh_from_db()
    assert len(template.options_json) == 2
    assert not AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(template.pk)).exists()


@pytest.mark.parametrize("path", ["update", "bulk_update", "upsert"])
@pytest.mark.parametrize("scope", ["manager", "queryset"])
def test_unsafe_options_bulk_changes_are_rejected(referenced_template, path, scope):
    template = referenced_template
    writer = FieldTemplate.objects if scope == "manager" else FieldTemplate.objects.filter(facility=template.facility)
    template.options_json = []
    with pytest.raises(ValidationError, match="options_json"):
        if path == "update":
            writer.update(options_json=[])
        elif path == "bulk_update":
            writer.bulk_update([template], ["options_json"])
        else:
            writer.bulk_create([template], update_conflicts=True, update_fields=["options_json"], unique_fields=["id"])
    template.refresh_from_db()
    assert len(template.options_json) == 2


@pytest.mark.parametrize("path", ["update", "bulk_update", "upsert"])
def test_active_bulk_changes_remain_allowed(referenced_template, path):
    template = referenced_template
    template.is_active = False
    if path == "update":
        FieldTemplate.objects.filter(pk=template.pk).update(is_active=False)
    elif path == "bulk_update":
        FieldTemplate.objects.bulk_update([template], ["is_active"])
    else:
        FieldTemplate.objects.bulk_create(
            [template], update_conflicts=True, update_fields=["is_active"], unique_fields=["id"]
        )
    template.refresh_from_db()
    assert template.is_active is False
    assert len(template.options_json) == 2


@pytest.mark.parametrize("path", ["save", "config"])
def test_unused_removal_label_change_and_deactivation_are_allowed(client, admin_user, referenced_template, path):
    template = referenced_template
    options = [{"slug": "used", "label": "New label", "is_active": False}]
    if path == "config":
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": template.pk}), _post(template, options)
        )
        assert response.status_code == 302
    else:
        template.options_json = options
        template.full_clean()
        template.save()
    template.refresh_from_db()
    assert template.options_json == options


def test_save_checks_only_persisted_options_and_default(option_template):
    template = option_template
    template.default_value = "used"
    template.save()
    template.options_json = []
    template.is_active = False
    template.save(update_fields=["is_active"])
    template.refresh_from_db()
    assert len(template.options_json) == 2
    template.options_json = []
    template.default_value = ""
    with pytest.raises(ValidationError) as caught:
        template.save(update_fields=["options_json"])
    _assert_protected(caught.value)
    template.save(update_fields=["options_json", "default_value"])
    template.refresh_from_db()
    assert template.options_json == [] and template.default_value == ""


@pytest.mark.parametrize("path", ["save", "clean", "config"])
@pytest.mark.parametrize("default_value", ["used", "", "unused"])
def test_default_references_follow_the_same_write(client, admin_user, option_template, path, default_value):
    template = option_template
    template.default_value = "used"
    template.save()
    replacement = [{"slug": "unused", "label": "Unused"}]
    if path == "config":
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": template.pk}),
            _post(template, replacement, default_value=default_value),
        )
        assert response.status_code == (200 if default_value == "used" else 302)
        if default_value == "used":
            assert "options_json" in response.context["form"].errors
    else:
        template.options_json = replacement
        template.default_value = default_value
        if default_value == "used":
            with pytest.raises(ValidationError) as caught:
                getattr(template, path)()
            _assert_protected(caught.value)
        else:
            getattr(template, path)()


@pytest.mark.parametrize("source", ["event", "client", "case", "history", "condition"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_saved_references_block_removal_including_trash_and_encrypted_lists(
    option_template, client_identified, doc_type_contact, staff_user, source, encrypted
):
    template = option_template
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=generate_key()):
        value = encrypt_field(["used"]) if encrypted else ["used"]
        if source == "client":
            Client.objects.filter(pk=client_identified.pk).update(data_json={template.slug: value}, is_deleted=True)
        elif source == "case":
            Case.objects.create(
                facility=template.facility,
                client=client_identified,
                document_type=doc_type_contact,
                created_by=staff_user,
                title="Archived case",
                data_json={template.slug: value},
                is_deleted=True,
            )
        elif source == "condition":
            # Preserve malformed legacy values without creating a new invalid selection reference.
            template.field_type = FieldTemplate.FieldType.TEXT
            template.save(update_fields=["field_type"])
            dependency = DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=template)
            target = FieldTemplate.objects.create(facility=template.facility, name="Target")
            target_ref = DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=target)
            FieldCondition.objects.create(
                target_field=target_ref,
                depends_on_field=dependency,
                operator=FieldCondition.Operator.CONTAINS,
                value=value,
            )
            template.field_type = FieldTemplate.FieldType.MULTI_SELECT
            template.save(update_fields=["field_type"])
        else:
            event = Event.objects.create(
                facility=template.facility,
                client=client_identified,
                document_type=doc_type_contact,
                created_by=staff_user,
                occurred_at=timezone.now(),
                data_json={template.slug: value} if source == "event" else {},
                is_deleted=True,
            )
            if source == "history":
                EventHistory.objects.create(
                    event=event, changed_by=staff_user, data_before={template.slug: value}, data_after={}
                )
        assert "used" in option_slugs_in_use(template)
        template.options_json = []
        with pytest.raises(ValidationError) as caught:
            template.save()
        _assert_protected(caught.value)


def _package(template, options, default_value=""):
    return parse_package(
        {
            "schema_version": 1,
            "field_templates": [
                {
                    "slug": template.slug,
                    "name": template.name,
                    "field_type": template.field_type,
                    "options": options,
                    "default_value": default_value,
                },
                {"slug": "rollback-probe", "name": "Rollback probe", "field_type": "text"},
            ],
            "document_types": [],
        }
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_package_rejects_used_removal_and_rolls_back(referenced_template, dry_run):
    template = referenced_template
    with pytest.raises(PackageLoadError) as caught:
        load_package(template.facility, _package(template, []), dry_run=dry_run)
    assert any(
        identifier == template.slug and "used" in message and "deaktivieren" in message
        for identifier, message in caught.value.report.errors
    )
    assert template.slug not in caught.value.report.field_templates.updated
    assert not FieldTemplate.objects.filter(facility=template.facility, slug="rollback-probe").exists()
    template.refresh_from_db()
    assert len(template.options_json) == 2


@pytest.mark.parametrize("dry_run", [False, True])
def test_package_allows_unused_removal_with_warning(option_template, dry_run):
    template = option_template
    report = load_package(template.facility, _package(template, []), dry_run=dry_run)
    assert report.errors == []
    assert any(identifier == template.slug and "unused" in message for identifier, message in report.warnings)
    template.refresh_from_db()
    assert len(template.options_json) == (2 if dry_run else 0)


def test_package_can_replace_default_while_removing_its_old_option(option_template):
    template = option_template
    template.default_value = "used"
    template.save()
    report = load_package(template.facility, _package(template, [{"slug": "unused", "label": "Unused"}], "unused"))
    assert report.errors == []
    template.refresh_from_db()
    assert template.default_value == "unused"
    assert {option["slug"] for option in template.options_json} == {"unused"}


def test_iterable_update_fields_persists_safe_changes(option_template):
    option_template.options_json = []
    option_template.is_active = False
    option_template.save(update_fields=(name for name in ("options_json", "is_active")))
    option_template.refresh_from_db()
    assert option_template.options_json == []
    assert option_template.is_active is False


@pytest.mark.parametrize("source", ["event", "quick_active", "quick_inactive", "default_single", "default_multiple"])
def test_editor_renders_protected_and_removable_rows(
    client, admin_user, option_template, doc_type_contact, client_identified, staff_user, source
):
    if source == "event":
        from tests._factories import make_event

        make_event(
            facility=option_template.facility,
            client=client_identified,
            document_type=doc_type_contact,
            created_by=staff_user,
            occurred_at=timezone.now(),
            data_json={option_template.slug: ["used"]},
        )
    elif source.startswith("quick"):
        QuickTemplate.objects.create(
            facility=option_template.facility,
            document_type=doc_type_contact,
            name="Editor reference",
            is_active=source == "quick_active",
            prefilled_data={option_template.slug: ["used"]},
        )
    else:
        option_template.field_type = "select" if source == "default_single" else "multi_select"
        option_template.default_value = "used"
        option_template.save()
    client.force_login(admin_user)

    response = client.get(reverse("core:config_fieldtemplate_update", args=[option_template.pk]))
    rows = re.findall(r"<div data-option-row\b.*?</div>", response.content.decode(), re.DOTALL)
    used_row = next((row for row in rows if 'value="used"' in row), "")
    unused_row = next((row for row in rows if 'value="unused"' in row), "")

    assert response.status_code == 200
    assert re.search(r"<input[^>]*data-option-slug[^>]*readonly", used_row)
    assert "Deaktivieren" in used_row
    assert "Entfernen" not in used_row
    assert 'value="unused"' in unused_row
    assert "readonly" not in unused_row
    assert "Entfernen" in unused_row


@pytest.mark.parametrize("replacement", [[], [{"slug": "renamed", "label": "Changed"}]])
def test_editor_manipulated_post_keeps_protected_row_with_server_error(
    client, admin_user, referenced_template, replacement
):
    client.force_login(admin_user)
    before = copy.deepcopy(referenced_template.options_json)
    audit_before = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE).count()

    response = client.post(
        reverse("core:config_fieldtemplate_update", args=[referenced_template.pk]),
        _post(referenced_template, replacement),
    )
    rows = re.findall(r"<div data-option-row\b.*?</div>", response.content.decode(), re.DOTALL)
    affected_row = next((row for row in rows if 'value="used"' in row), "")

    assert response.status_code == 200
    assert "options_json" in response.context["form"].errors
    assert "data-option-error" in affected_row
    assert "Verwendete Options-Slugs" in affected_row
    assert "deaktivieren" in affected_row
    assert "readonly" in affected_row
    assert "Deaktivieren" in affected_row
    referenced_template.refresh_from_db()
    assert referenced_template.options_json == before
    assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE).count() == audit_before


def test_editor_deactivation_post_preserves_slug(client, admin_user, referenced_template):
    client.force_login(admin_user)
    options = [{"slug": "used", "label": "Changed label", "is_active": False}]

    response = client.post(
        reverse("core:config_fieldtemplate_update", args=[referenced_template.pk]),
        _post(referenced_template, options),
    )

    assert response.status_code == 302
    referenced_template.refresh_from_db()
    assert referenced_template.options_json == options
