"""Read-only configuration row locks leave deferred FK checks free (Refs #2525).

Configuration writers serialize through the exclusive protection gate. Rows
they only read and validate keep ``FOR NO KEY UPDATE``: it still conflicts
with every writer of those rows, but not with the ``FOR KEY SHARE`` lock a
referencing insert takes at commit. Rows saved through the model keep the key
strength PostgreSQL assigns to that update; FK writers queue at the gate.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import OperationalError, connections, transaction
from django.forms import modelform_factory
from django.test import RequestFactory

from core.admin.documents import FieldTemplateAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.config.mutations import add_field_to_doctype, save_field_template
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_write_locking import run_connection

pytestmark = pytest.mark.django_db(transaction=True)

_KEY_SHARE_PROBES = {
    DocumentType: "SELECT id FROM core_documenttype WHERE id = %s FOR KEY SHARE NOWAIT",
    FieldTemplate: "SELECT id FROM core_fieldtemplate WHERE id = %s FOR KEY SHARE NOWAIT",
}


def _fk_check_granted(obj):
    """Take the lock of a deferred FK check on another connection, without waiting."""

    def probe():
        with transaction.atomic(), connections["default"].cursor() as cursor:
            try:
                cursor.execute(_KEY_SHARE_PROBES[type(obj)], [obj.pk])
            except OperationalError as exc:
                if getattr(exc.__cause__, "sqlstate", None) == "55P03":
                    return False
                raise
        return True

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run_connection, probe).result(timeout=10)


@pytest.fixture
def assignment(facility):
    document_type = DocumentType.objects.create(facility=facility, name="Assigned type")
    template = FieldTemplate.objects.create(facility=facility, name="Assigned field")
    DocumentTypeField.objects.create(document_type=document_type, field_template=template)
    return document_type, template


@pytest.fixture
def config_request(facility, admin_user):
    request = _request(RequestFactory(), admin_user)
    request.current_facility = facility
    return request


def test_template_change_leaves_assigned_types_free_for_fk_checks(facility, config_request, assignment):
    document_type, template = assignment
    form = modelform_factory(FieldTemplate, fields=["name", "field_type", "help_text"])(
        data={"name": template.name, "field_type": template.field_type, "help_text": "Changed help"},
        instance=template,
    )
    assert form.is_valid(), form.errors
    with transaction.atomic():
        save_field_template(config_request, form, creating=False, facility=facility)
        assert _fk_check_granted(document_type)


def test_assignment_leaves_type_and_template_free_for_fk_checks(facility, config_request):
    document_type = DocumentType.objects.create(facility=facility, name="Target type")
    template = FieldTemplate.objects.create(facility=facility, name="New assignment")
    with transaction.atomic():
        add_field_to_doctype(config_request, document_type, template)
        assert _fk_check_granted(document_type)
        assert _fk_check_granted(template)


def test_bulk_toggle_leaves_templates_free_for_fk_checks(config_request, assignment):
    _document_type, template = assignment
    admin = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
    with transaction.atomic():
        assert admin._bulk_set_active(config_request, FieldTemplate.objects.filter(pk=template.pk), is_active=False)
        assert _fk_check_granted(template)
