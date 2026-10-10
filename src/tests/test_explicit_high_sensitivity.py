"""Configuration and package contracts for explicit High fields (Refs #2525)."""

from dataclasses import replace

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.forms.config import DocumentTypeFieldAddForm, DocumentTypeForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.packages.catalog import list_packages, parse_catalog_package
from core.packages.loader import PackageLoadError, load_package
from core.packages.schema import (
    DocumentTypeDef,
    FieldRefDef,
    FieldTemplateDef,
    PackageV1,
    PackageValidationError,
    parse_package,
)
from core.services.config import add_field_to_doctype
from tests._factories import make_document_type


def raw_package(level="high", field_level=""):
    return {
        "schema_version": 1,
        "field_templates": [{"slug": "note", "name": "Explicit note", "sensitivity": field_level}],
        "document_types": [
            {"slug": "secure", "name": "Secure", "sensitivity": level, "field_refs": [{"template_slug": "note"}]}
        ],
    }


def typed_package():
    return PackageV1(
        schema_version=1,
        field_templates=(FieldTemplateDef(slug="note", name="Explicit note"),),
        document_types=(
            DocumentTypeDef(
                slug="secure", name="Secure", sensitivity="high", field_refs=(FieldRefDef(template_slug="note"),)
            ),
        ),
    )


def test_schema_rejects_inheriting_high_field():
    with pytest.raises(PackageValidationError) as error:
        parse_package(raw_package())
    assert error.value.errors[0][0] == "document_types[0].field_refs[0].template_slug"
    assert "Explicit note" in str(error.value)
    assert "erben" in str(error.value)


@pytest.mark.parametrize(
    "level,field_level", [("normal", ""), ("elevated", ""), ("high", "elevated"), ("high", "normal")]
)
def test_schema_accepts_allowed_explicit_or_inherited_levels(level, field_level):
    assert parse_package(raw_package(level, field_level)).document_types[0].sensitivity == level


@pytest.mark.architecture
@pytest.mark.parametrize("package_name", list_packages())
def test_catalog_high_fields_have_explicit_protection(package_name):
    package = parse_catalog_package(package_name)
    templates = {field.slug: field for field in package.field_templates}
    for doc_type in package.document_types:
        if doc_type.sensitivity != "high":
            continue
        for ref in doc_type.field_refs:
            field = templates[ref.template_slug]
            assert field.sensitivity == ("elevated" if field.report_key else "high"), (package_name, field.slug)
            assert field.is_encrypted == (not bool(field.report_key)), (package_name, field.slug)


@pytest.mark.django_db
def test_loader_rejects_inheriting_high_field_atomically(facility):
    with pytest.raises(PackageLoadError) as error:
        load_package(facility, typed_package())
    assert "Explicit note" in str(error.value)
    assert "erben" in str(error.value)
    assert not DocumentType.objects.for_facility(facility).filter(slug="secure").exists()
    assert not FieldTemplate.objects.for_facility(facility).filter(slug="note").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["type", "field"])
def test_loader_validates_existing_assignments_not_mentioned_in_package(facility, change):
    doc_type = make_document_type(
        facility=facility, name="Secure", slug="secure", sensitivity="normal" if change == "type" else "high"
    )
    field = FieldTemplate.objects.create(
        facility=facility, name="Explicit note", slug="note", sensitivity="" if change == "type" else "elevated"
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    package = typed_package()
    if change == "type":
        package = replace(
            package, field_templates=(), document_types=(replace(package.document_types[0], field_refs=()),)
        )
    else:
        package = replace(package, document_types=())
    with pytest.raises(PackageLoadError):
        load_package(facility, package)
    doc_type.refresh_from_db()
    field.refresh_from_db()
    assert doc_type.sensitivity == ("normal" if change == "type" else "high")
    assert field.sensitivity == ("" if change == "type" else "elevated")


@pytest.mark.django_db
@pytest.mark.parametrize("initial_level", ["normal", "elevated", "high"])
def test_type_form_and_view_reject_inheriting_high_fields(client, admin_user, facility, initial_level):
    doc_type = make_document_type(facility=facility, name="Secure", sensitivity=initial_level)
    field = FieldTemplate.objects.create(facility=facility, name="Inherited note")
    # Deliberately corrupt configuration to exercise the existing-state form guard.
    DocumentTypeField.objects.bulk_create([DocumentTypeField(document_type=doc_type, field_template=field)])
    data = {"name": doc_type.name, "sensitivity": "high", "is_active": "on"}
    form = DocumentTypeForm(data, instance=doc_type, facility=facility)
    assert not form.is_valid()
    assert field.name in str(form.errors["sensitivity"])
    assert "erben" in str(form.errors["sensitivity"])
    client.force_login(admin_user)
    response = client.post(reverse("core:config_doctype_update", args=[doc_type.pk]), data)
    assert response.status_code == 200
    assert "erben" in response.content.decode()
    doc_type.refresh_from_db()
    assert doc_type.sensitivity == initial_level


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["form", "service", "view"])
def test_assignment_form_service_and_view_reject_inheriting_high_field(client, admin_user, facility, rf, path):
    doc_type = make_document_type(facility=facility, name="Secure", sensitivity="high")
    field = FieldTemplate.objects.create(facility=facility, name="Inherited note")
    if path == "form":
        form = DocumentTypeFieldAddForm({"field_template": str(field.pk)}, doc_type=doc_type)
        assert not form.is_valid()
        assert field.name in str(form.errors["field_template"])
    elif path == "service":
        request = rf.post("/")
        request.user = admin_user
        request.current_facility = facility
        with pytest.raises(ValidationError, match="erben"):
            add_field_to_doctype(request, doc_type, field)
    else:
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_field_add", args=[doc_type.pk]),
            {"field_template": str(field.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "erben" in response.content.decode()
    assert not doc_type.fields.exists()


@pytest.mark.django_db
def test_field_edit_cannot_restore_inheritance_in_high_type(client, admin_user, facility):
    doc_type = make_document_type(facility=facility, name="Secure", sensitivity="high")
    field = FieldTemplate.objects.create(facility=facility, name="Explicit note", sensitivity="elevated")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    client.force_login(admin_user)
    response = client.post(
        reverse("core:config_fieldtemplate_update", args=[field.pk]),
        {"name": field.name, "field_type": "text", "sensitivity": "", "is_active": "on"},
    )
    assert response.status_code == 200
    assert "erben" in response.content.decode()
    field.refresh_from_db()
    assert field.sensitivity == "elevated"


@pytest.mark.django_db
@pytest.mark.parametrize("field_level", ["normal", "elevated", "high"])
def test_high_type_accepts_explicit_field_via_view(client, admin_user, facility, field_level):
    doc_type = make_document_type(facility=facility, name="Secure", sensitivity="high")
    field = FieldTemplate.objects.create(facility=facility, name="Explicit note", sensitivity=field_level)
    client.force_login(admin_user)
    response = client.post(
        reverse("core:config_doctype_field_add", args=[doc_type.pk]),
        {"field_template": str(field.pk)},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert doc_type.fields.filter(field_template=field).exists()


@pytest.mark.django_db
def test_type_change_view_rejects_inheriting_high_field(client, admin_user, facility):
    doc_type = make_document_type(facility=facility, name="Secure")
    field = FieldTemplate.objects.create(facility=facility, name="Inherited note")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    client.force_login(admin_user)
    response = client.post(
        reverse("core:config_doctype_update", args=[doc_type.pk]),
        {"name": doc_type.name, "sensitivity": "high", "is_active": "on"},
    )
    assert response.status_code == 200
    assert "erben" in response.content.decode()
    doc_type.refresh_from_db()
    assert doc_type.sensitivity == "normal"
