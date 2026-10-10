"""Compare public binding information with actual datasets of every registered backend."""

import pytest
from django.utils import timezone

from core.models import Case, DocumentTypeField, FieldTemplate
from core.reporting import registry
from core.reporting.base import feld_bindungen, typ_bindungen
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db


def configure_type(facility, name, requirement=None, *, fields=None, key=None):
    binding = requirement.typ_bindung if requirement else None
    attributes = {binding.values_fields()[0]: binding.keys()[0]} if binding else {"report_key": "contact"}
    if key is not None:
        attributes["report_key"] = key
    doc_type = make_document_type(facility=facility, name=name, **attributes)
    templates = []
    for field_binding in fields if fields is not None else requirement.feld_bindungen if requirement else ():
        field = FieldTemplate.objects.create(
            facility=facility,
            name=f"{name} {field_binding.keys()[0]}",
            field_type="number",
            **{
                field_binding.bindet: (
                    field_binding.keys() if field_binding.bindet == "report_key" else field_binding.slugs()
                )[0]
            },
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
        templates.append(field)
    return doc_type, templates


def counted_events(backend, result):
    if backend.slug in {"example", "monthly-trend", "sample-report-1"}:
        return result.summary["total"]
    if backend.slug == "needle-exchange-balance":
        return result.summary["events"]
    if backend.slug == "counseling-hours":
        return result.summary["events_gesamt"]
    if backend.slug == "service-distribution":
        return sum(result.summary.values())
    if backend.slug == "ban-overview":
        return result.summary["ausgesprochen"]
    if backend.slug == "client-metrics":
        return result.summary["aktive_personen"]
    if backend.slug == "case-metrics":
        return result.summary["neue_faelle"]
    assert backend.slug == "general-key-figures", "A new registered backend needs a dataset contract."
    return result.summary["total_contacts"]


def counted_field_values(backend, result, key):
    if backend.slug == "needle-exchange-balance":
        return result.summary[{"ausgabe_stueck": "summe_ausgabe", "rueckgabe_stueck": "summe_rueckgabe"}[key]]
    if backend.slug == "counseling-hours":
        return result.summary["minuten_gesamt"]
    assert backend.slug == "service-distribution", "A new value backend needs a dataset contract."
    return result.summary.get(f"events_{key}", 0)


@pytest.mark.parametrize("backend", registry.get_backends(), ids=lambda backend: backend.slug)
@pytest.mark.parametrize(
    "scenario",
    ["complete", "one-field", "inactive", "other-group", "incomplete-in-configured-group", "excluded", "foreign"],
)
def test_binding_information_matches_actual_dataset(
    backend, scenario, facility, second_facility, staff_user, date_range
):
    requirements = backend.verfuegbarkeit
    if not requirements and backend.typ_bindung is not None:
        from core.reporting.base import Anforderung

        requirements = (Anforderung(backend.typ_bindung, backend.feld_bindungen),)
    for index, requirement in enumerate(requirements):
        if scenario != "other-group" or index == 0:
            configure_type(facility, f"Configured {index}", requirement)
    target_facility = second_facility if scenario == "foreign" else facility
    requirement = requirements[-1] if requirements else None
    fields = requirement.feld_bindungen if requirement else ()
    if scenario == "one-field":
        fields = fields[:1]
    if scenario == "incomplete-in-configured-group" or (scenario == "other-group" and len(requirements) > 1):
        fields = ()
    doc_type, templates = configure_type(
        target_facility,
        "Target",
        requirement,
        fields=fields,
        key="excluded" if scenario == "excluded" else None,
    )
    if scenario == "inactive":
        doc_type.is_active = False
        doc_type.save()
    now = timezone.now()
    payload = {template.slug: "choice" if backend.slug == "service-distribution" else 7 for template in templates}
    for index in range(5):
        person = make_client(facility=target_facility, pseudonym=f"Contract {index}", created_by=staff_user)
        make_event(
            facility=target_facility,
            client=person,
            document_type=doc_type,
            occurred_at=now,
            data_json=payload,
            created_by=staff_user,
        )
        if backend.slug == "case-metrics":
            Case.objects.create(facility=target_facility, client=person, title="Contract", created_by=staff_user)
    result = backend.build_dataset(facility, *date_range)
    count = counted_events(backend, result)
    declared = bool(typ_bindungen(backend) or backend.verfuegbarkeit)
    listed = any(str(backend.name) in name for name in registry.describe_type_bindings(doc_type, facility))
    # Reports without key declarations count their own populations independently of mappings.
    assert listed == (declared and count > 0)
    assert bool(backend.contributions_for_type(doc_type, facility)) == (declared and count > 0)
    expected = 0 if scenario == "foreign" else 5
    if declared and scenario == "excluded" and backend.slug != "ban-overview":
        expected = 0
    if backend.slug == "service-distribution" and scenario in {"other-group", "incomplete-in-configured-group"}:
        expected = 0
    if backend.slug == "general-key-figures" and scenario == "foreign":
        expected = None  # The exported zero remains suppressed by the existing privacy pipeline.
    assert count == expected
    for template in templates:
        binding = next(binding for binding in feld_bindungen(backend) if binding.key_for_template(template))
        key = binding.key_for_template(template)
        field_count = counted_field_values(backend, result, key)
        assert field_count == (0 if count == 0 else 5 if backend.slug == "service-distribution" else 35)
        field_listed = any(str(backend.name) in name for name in registry.describe_field_bindings(template, facility))
        assert field_listed == (field_count > 0)
        assert backend.uses_field_template(template, doc_type, facility, binding) == (field_count > 0)
    if backend.slug == "needle-exchange-balance" and scenario == "one-field":
        assert result.summary["summe_ausgabe"] == 35
        assert result.summary["summe_rueckgabe"] == 0
        assert any("rueckgabe_stueck" in warning for warning in registry.diagnose_type_bindings(doc_type, facility))
    if backend.slug == "service-distribution" and scenario == "other-group":
        assert result.summary == {"events_leistungen": 0}
        assert any("art_der_krise" in warning for warning in registry.diagnose_type_bindings(doc_type, facility))

    if backend.slug == "service-distribution" and scenario == "incomplete-in-configured-group":
        assert result.summary == {"events_leistungen": 0, "events_art_der_krise": 0}
        assert any(issue.code == "missing_field_values" and issue.count == 5 for issue in result.completeness)
        assert any("art_der_krise" in warning for warning in registry.diagnose_type_bindings(doc_type, facility))
