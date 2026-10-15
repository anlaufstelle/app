"""Activation preserves unique report bindings (Refs #2574)."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.admin.documents import FieldTemplateAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.reporting.base import BindungsEintrag, FeldBindung
from core.services.config import set_field_template_active
from tests.test_sensitivity_transitions import request_for

pytestmark = pytest.mark.django_db


def binding_pair(facility, *, active=True):
    doc_type = DocumentType.objects.create(facility=facility, name="Duration report")
    old = FieldTemplate.objects.create(
        facility=facility,
        name="Old duration",
        field_type="number",
        report_key="dauer_minuten",
        is_active=False,
    )
    new = FieldTemplate.objects.create(
        facility=facility,
        name="New duration",
        field_type="number",
        report_key="dauer_minuten",
        is_active=active,
    )
    for field in (old, new):
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    return doc_type, old, new


@pytest.mark.parametrize("path", ["model", "service", "toggle", "admin"])
def test_conflicting_reactivation_preserves_report_binding(facility, admin_user, client, rf, path):
    doc_type, old, new = binding_pair(facility)
    binding = FeldBindung((BindungsEintrag("dauer_minuten", "Duration"),), bindet="report_key", field_type="number")
    before = binding.leseschluessel(facility)
    assert before.je_typ[doc_type.pk] == new.slug
    request = request_for(rf, facility, admin_user)
    if path == "toggle":
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_toggle", args=[old.pk]), {"active": "1"}, follow=True)
        assert response.status_code == 200
        assert new.name in response.content.decode()
        assert doc_type.name in response.content.decode()
    else:
        with pytest.raises(ValidationError):
            if path == "model":
                old.is_active = True
                old.save(update_fields=["is_active"])
            elif path == "service":
                set_field_template_active(request, old, active=True)
            else:
                admin = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
                admin._bulk_set_active(
                    request, FieldTemplate.objects.for_facility(facility).filter(pk=old.pk), is_active=True
                )
    old.refresh_from_db()
    assert not old.is_active
    assert binding.leseschluessel(facility) == before


def test_admin_bulk_rejects_conflicts_within_selected_set(facility, admin_user, rf):
    _, old, new = binding_pair(facility, active=False)
    admin = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
    with pytest.raises(ValidationError):
        admin._bulk_set_active(
            request_for(rf, facility, admin_user),
            FieldTemplate.objects.for_facility(facility).filter(pk__in=[old.pk, new.pk]),
            is_active=True,
        )
    assert not FieldTemplate.objects.for_facility(facility).filter(pk__in=[old.pk, new.pk], is_active=True).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("second_path", ["activation", "assignment"])
def test_concurrent_binding_changes_serialize(facility, admin_user, rf, second_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as ThreadEvent

    from django.db import connections
    from django.db.models.signals import post_save

    from core.services.config import add_field_to_doctype
    from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

    doc_type, old, new = binding_pair(facility, active=False)
    if second_path == "assignment":
        DocumentTypeField.objects.filter(document_type=doc_type, field_template=new).delete()
        new.is_active = True
        new.save(update_fields=["is_active"])
    ready, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def pause_first(sender, instance, **kwargs):
        if instance.pk == old.pk:
            ready.set()
            assert resume.wait(10), "First activation was not released"

    def first():
        current = FieldTemplate.objects.for_facility(facility).get(pk=old.pk)
        current.is_active = True
        current.save(update_fields=["is_active"])

    def second():
        pids["second"] = connections["default"].connection.info.backend_pid
        started.set()
        try:
            if second_path == "activation":
                current = FieldTemplate.objects.for_facility(facility).get(pk=new.pk)
                current.is_active = True
                current.save(update_fields=["is_active"])
            else:
                add_field_to_doctype(request_for(rf, facility, admin_user), doc_type, new)
        except ValidationError:
            return "rejected"
        return "accepted"

    post_save.connect(pause_first, sender=FieldTemplate, weak=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(run_connection, first)
            try:
                assert ready.wait(5), "First activation did not reach persistence"
                challenger = pool.submit(run_connection, second)
                assert started.wait(5)
                assert wait_for_completion_or_lock(challenger, pids["second"])
            finally:
                resume.set()
            writer.result(timeout=12)
            assert challenger.result(timeout=12) == "rejected"
    finally:
        post_save.disconnect(pause_first, sender=FieldTemplate)
    assert (
        FieldTemplate.objects.for_facility(facility)
        .filter(
            document_type_fields__document_type=doc_type,
            is_active=True,
            report_key="dauer_minuten",
        )
        .count()
        == 1
    )


def test_rejected_reactivation_preserves_report_totals(facility, admin_user, rf):
    from django.utils import timezone

    from core.models import Event
    from core.reporting.backends.counseling_hours import CounselingHoursBackend

    doc_type, old, new = binding_pair(facility)
    doc_type.report_key = "contact"
    doc_type.save()
    for _ in range(3):
        Event.objects.create(
            facility=facility,
            document_type=doc_type,
            created_by=admin_user,
            occurred_at=timezone.now(),
            data_json={new.slug: 15},
        )
    backend = CounselingHoursBackend()
    today = timezone.localdate()
    before = backend.build_dataset(facility, today, today)
    assert before.summary["minuten_gesamt"] == 45
    with pytest.raises(ValidationError):
        set_field_template_active(request_for(rf, facility, admin_user), old, active=True)
    after = backend.build_dataset(facility, today, today)
    assert after.records == before.records
    assert after.summary == before.summary


def test_activation_uses_persisted_key_during_partial_save(facility):
    _, old, _ = binding_pair(facility)
    old.report_key = None
    old.is_active = True
    with pytest.raises(ValidationError):
        old.save(update_fields=["is_active"])
    old.refresh_from_db()
    assert not old.is_active
    assert old.report_key == "dauer_minuten"


def test_normal_edit_reactivation_error_names_type_and_competing_template(facility, admin_user, client):
    doc_type, old, new = binding_pair(facility)
    client.force_login(admin_user)
    response = client.post(
        reverse("core:config_fieldtemplate_update", args=[old.pk]),
        {"name": old.name, "field_type": "number", "options_json": "[]", "is_active": "on"},
    )
    assert response.status_code == 200
    errors = str(response.context["form"].errors)
    assert doc_type.name in errors
    assert new.name in errors
    old.refresh_from_db()
    assert not old.is_active
