"""Local configuration diagnostics for report requirements."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentTypeField, FieldTemplate
from core.reporting import registry
from core.reporting.backends.needle_exchange_balance import NeedleExchangeBalanceBackend
from core.reporting.base import Anforderung, BindungsEintrag, FeldBindung, TypBindung
from tests._factories import make_document_type, make_event

pytestmark = pytest.mark.django_db


def make_type(facility, name="Counter", key="needle_exchange"):
    return make_document_type(facility=facility, name=name, report_key=key)


def add_field(doc_type, key, *, active=True):
    field = FieldTemplate.objects.create(
        facility=doc_type.facility, name=f"{doc_type.name} {key}", field_type="number", report_key=key, is_active=active
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    return field


@pytest.fixture
def partial_type(facility):
    doc_type = make_type(facility)
    field = add_field(doc_type, "ausgabe_stueck")
    return doc_type, field


def test_partial_requirement_names_report_and_missing_key(partial_type, facility):
    doc_type, _ = partial_type
    warnings = registry.diagnose_type_bindings(doc_type, facility)
    assert any("Spritzentausch-Bilanz" in warning and "rueckgabe_stueck" in warning for warning in warnings)


def test_complete_requirement_has_no_warning_and_is_listed(partial_type, facility):
    doc_type, field = partial_type
    add_field(doc_type, "rueckgabe_stueck")
    assert registry.diagnose_type_bindings(doc_type, facility) == []
    assert any("Spritzentausch-Bilanz" in line for line in registry.describe_type_bindings(doc_type, facility))
    assert any("Spritzentausch-Bilanz" in line for line in registry.describe_field_bindings(field, facility))


def test_other_complete_type_does_not_mask_local_gap(partial_type, facility, staff_user):
    doc_type, field = partial_type
    complete = make_type(facility, "Complete")
    add_field(complete, "ausgabe_stueck")
    add_field(complete, "rueckgabe_stueck")
    now = timezone.now()
    make_event(
        facility=facility, client=None, document_type=doc_type, occurred_at=now, data_json={}, created_by=staff_user
    )
    result = NeedleExchangeBalanceBackend().build_dataset(
        facility, now.date() - timedelta(days=1), now.date() + timedelta(days=1)
    )
    assert result.summary["events"] == 1
    assert result.summary["events_ohne_werte"] == 1
    assert any("rueckgabe_stueck" in warning for warning in registry.diagnose_type_bindings(doc_type, facility))
    assert any("Spritzentausch-Bilanz" in line for line in registry.describe_type_bindings(doc_type, facility))
    assert not any("Spritzentausch-Bilanz" in line for line in registry.describe_field_bindings(field, facility))


def test_inactive_field_does_not_complete_requirement(partial_type, facility):
    doc_type, _ = partial_type
    add_field(doc_type, "rueckgabe_stueck", active=False)
    assert any("rueckgabe_stueck" in warning for warning in registry.diagnose_type_bindings(doc_type, facility))


def test_foreign_configuration_is_not_disclosed(partial_type, facility, second_facility):
    foreign = make_type(second_facility, "Foreign secret")
    foreign_field = add_field(foreign, "ausgabe_stueck")
    add_field(foreign, "rueckgabe_stueck")
    assert registry.diagnose_type_bindings(foreign, facility) == []
    assert registry.diagnose_field_bindings(foreign_field, facility) == []
    assert registry.describe_type_bindings(foreign, facility) == []
    assert registry.describe_field_bindings(foreign_field, facility) == []


def test_generic_or_requirements_and_missing_type_key(partial_type, facility, monkeypatch):
    doc_type, _ = partial_type
    first = FeldBindung((BindungsEintrag("ausgabe_stueck", "Output"),), bindet="report_key")
    second = FeldBindung((BindungsEintrag("quantity_units", "Units"),), bindet="report_key")
    alternate = FeldBindung((BindungsEintrag("alternative_units", "Alternative"),), bindet="report_key")
    binding = TypBindung((BindungsEintrag("custom_counter", "Custom counter"),))
    backend = NeedleExchangeBalanceBackend()
    backend.name = "Custom report"
    backend.typ_bindung = binding
    backend.feld_bindungen = (first, second, alternate)
    backend.verfuegbarkeit = (Anforderung(binding, (first, second)), Anforderung(None, (alternate,)))
    monkeypatch.setattr(registry, "get_backends", lambda: [backend])
    warnings = registry.diagnose_type_bindings(doc_type, facility)
    assert len(warnings) == 1
    assert "quantity_units" in warnings[0] and "custom_counter" in warnings[0]
    assert "alternative_units" not in warnings[0]
    add_field(doc_type, "alternative_units")
    assert registry.diagnose_type_bindings(doc_type, facility) == []
    assert "Custom report" in registry.describe_type_bindings(doc_type, facility)


@pytest.mark.parametrize("route,target", [("core:config_doctype_update", 0), ("core:config_fieldtemplate_update", 1)])
def test_edit_view_renders_warning(client, admin_user, partial_type, route, target):
    client.force_login(admin_user)
    response = client.get(reverse(route, args=[partial_type[target].pk]))
    assert response.status_code == 200
    assert b"rueckgabe_stueck" in response.content
    assert b"report-binding-warning" in response.content


def test_create_preview_warns_about_required_fields(facility):
    from core.forms.config import DocumentTypeForm

    form = DocumentTypeForm(facility=facility)
    assert "rueckgabe_stueck" in form.report_key_bindings_json


def test_field_only_contributes_through_its_completed_alternative(partial_type, facility, monkeypatch):
    doc_type, field = partial_type
    output = FeldBindung((BindungsEintrag("ausgabe_stueck", "Output"),), bindet="report_key")
    units = FeldBindung((BindungsEintrag("quantity_units", "Units"),), bindet="report_key")
    alternate = FeldBindung((BindungsEintrag("alternative_units", "Alternative"),), bindet="report_key")
    backend = NeedleExchangeBalanceBackend()
    backend.name = "Alternative report"
    backend.typ_bindung = None
    backend.feld_bindungen = (output, units, alternate)
    backend.verfuegbarkeit = (Anforderung(None, (output, units)), Anforderung(None, (alternate,)))
    monkeypatch.setattr(registry, "get_backends", lambda: [backend])
    add_field(doc_type, "alternative_units")
    assert registry.diagnose_type_bindings(doc_type, facility) == []
    assert registry.describe_field_bindings(field, facility) == []


def test_unassigned_field_is_not_listed(partial_type, facility):
    doc_type, _ = partial_type
    add_field(doc_type, "rueckgabe_stueck")
    field = FieldTemplate.objects.create(facility=facility, name="Unassigned", report_key="ausgabe_stueck")
    assert registry.describe_field_bindings(field, facility) == []


def test_inactive_assigned_field_still_explains_missing_requirement(partial_type, facility):
    doc_type, field = partial_type
    field.is_active = False
    field.save()
    assert any("ausgabe_stueck" in warning for warning in registry.diagnose_field_bindings(field, facility))


@pytest.mark.parametrize("route,target", [("core:config_doctype_update", 0), ("core:config_fieldtemplate_update", 1)])
def test_warning_allows_save_and_keeps_report_key(client, admin_user, partial_type, route, target):
    client.force_login(admin_user)
    doc_type, field = partial_type
    obj = partial_type[target]
    url = reverse(route, args=[obj.pk])
    assert b"report-binding-warning" in client.get(url).content
    data = {"name": obj.name, "sensitivity": "normal", "is_active": "on", "sort_order": "0"}
    if target == 1:
        data.update(field_type="number", options_json="[]")
    response = client.post(url, data)
    assert response.status_code == 302
    obj.refresh_from_db()
    assert obj.report_key == ("needle_exchange" if target == 0 else "ausgabe_stueck")


def test_field_warning_escapes_type_name(client, admin_user, partial_type):
    doc_type, field = partial_type
    doc_type.name = "<script>alert(1)</script>"
    doc_type.save()
    client.force_login(admin_user)
    response = client.get(reverse("core:config_fieldtemplate_update", args=[field.pk]))
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.content
    assert b"<script>alert(1)</script>" not in response.content


def test_inactive_type_with_counted_values_remains_listed(partial_type, facility, staff_user):
    complete = make_type(facility, "Complete")
    add_field(complete, "ausgabe_stueck")
    add_field(complete, "rueckgabe_stueck")
    doc_type, output = partial_type
    returned = add_field(doc_type, "rueckgabe_stueck")
    now = timezone.now()
    make_event(
        facility=facility,
        client=None,
        document_type=doc_type,
        occurred_at=now,
        data_json={output.slug: 3, returned.slug: 1},
        created_by=staff_user,
    )
    doc_type.is_active = False
    doc_type.save()
    result = NeedleExchangeBalanceBackend().build_dataset(
        facility, now.date() - timedelta(days=1), now.date() + timedelta(days=1)
    )
    assert result.summary["events"] == 1
    assert result.summary["summe_ausgabe"] == 3
    assert registry.diagnose_type_bindings(doc_type, facility) == []
    assert any("Spritzentausch-Bilanz" in line for line in registry.describe_type_bindings(doc_type, facility))
