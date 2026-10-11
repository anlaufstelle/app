"""Public reporting paths for period quantity sums (Refs #2515)."""

import csv
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
import weasyprint
from django.urls import reverse
from django.utils import timezone

from core.forms.config import FieldTemplateForm
from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, Settings
from core.reporting import registry
from core.reporting.export import export_dataset
from core.views.statistics import build_report_panel_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def backend():
    return registry.get_backend("quantity-sum")


def make_type(facility, name="Counter", *, key="quantity_units", field_type="number", **kwargs):
    document_type = DocumentType.objects.create(facility=facility, name=name, **kwargs)
    field = FieldTemplate.objects.create(
        facility=facility, name=f"{name} quantity", field_type=field_type, report_key=key
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    return document_type, field


def add_events(facility, user, document_type, field, values):
    for value in values:
        Event.objects.create(
            facility=facility,
            created_by=user,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={} if value is None else {field.slug: value},
        )


def prepared(backend, facility, date_range):
    return backend.prepare_export(facility, backend.build_dataset(facility, *date_range))


def test_registry_vocabulary_and_field_select(facility):
    assert "quantity-sum" in {item.slug for item in registry.get_backends()}
    assert str(registry.get_field_key_vocabulary()["quantity_units"]) == "Stückzahl"
    assert "quantity_units" not in registry.get_report_key_vocabulary()
    assert [item.slug for item in registry.backends_for_field_key("quantity_units")] == ["quantity-sum"]
    assert "quantity_units" in dict(FieldTemplateForm(facility=facility).fields["report_key"].choices)
    assert registry.get_backend("quantity-sum").snapshot_enabled is False


def test_period_sums_are_type_independent_and_ordered(backend, facility, admin_user, date_range):
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
    second, second_field = make_type(facility, "Zulu", report_key="contact")
    first, first_field = make_type(facility, "Alpha")
    add_events(facility, admin_user, second, second_field, [8, 7, 0])
    add_events(facility, admin_user, first, first_field, [1, 2, 7])
    result = prepared(backend, facility, date_range)
    assert result.records == [
        {"document_type": "Alpha", "contributors": 3, "quantity": 10},
        {"document_type": "Zulu", "contributors": 3, "quantity": 15},
    ]
    assert result.summary["quantity_total"] == 25
    assert result.summary["contributors_total"] == 6
    assert "events_total" not in result.summary


@pytest.fixture(params=["exclusive", "other-type", "shared-field", "duplicate"])
def excluded_configuration(request, facility, admin_user):
    excluded, field = make_type(facility, "Excluded counter", report_key="excluded")
    add_events(facility, admin_user, excluded, field, [999] * 5)
    included = None
    included_field = None
    if request.param in {"other-type", "shared-field", "duplicate"}:
        included, included_field = make_type(facility, "Included counter", report_key="contact")
        if request.param == "shared-field":
            DocumentTypeField.objects.filter(document_type=included).delete()
            DocumentTypeField.objects.create(document_type=included, field_template=field)
            included_field = field
        add_events(facility, admin_user, included, included_field, [7] * 5)
    if request.param == "duplicate":
        other = FieldTemplate.objects.create(facility=facility, name="Duplicate quantity", field_type="number")
        DocumentTypeField.objects.create(document_type=excluded, field_template=other)
        FieldTemplate.objects.filter(pk=other.pk).update(report_key="quantity_units")
    return excluded, field, included, included_field


def test_excluded_type_does_not_contribute_to_dataset(backend, facility, date_range, excluded_configuration):
    _, _, included, _ = excluded_configuration
    result = backend.build_dataset(facility, *date_range)
    assert result.summary == {
        "document_type_counts": {"Excluded counter": 5, **({"Included counter": 5} if included else {})},
        "events_total": 5 if included else 0,
        "contributors_total": 5 if included else 0,
        "quantity_total": 35 if included else 0,
    }
    assert result.records == (
        [{"document_type": included.name, "events": 5, "contributors": 5, "quantity": 35}] if included else []
    )
    assert result.completeness == []
    if included:
        assert "duplicate_field_binding" not in {
            issue.code for issue in prepared(backend, facility, date_range).completeness
        }


def test_excluded_type_has_consistent_binding_information(backend, facility, excluded_configuration):
    excluded, field, included, included_field = excluded_configuration
    assert backend.is_available(facility) is bool(included)
    assert not any(str(backend.name) in name for name in registry.describe_type_bindings(excluded, facility))
    assert backend.contributions_for_type(excluded, facility) == ()
    assert not backend.uses_field_template(field, excluded, facility, backend.feld_bindungen[0])
    assert any(str(backend.name) in name for name in registry.describe_field_bindings(field, facility)) is (
        included_field is field
    )
    assert registry.diagnose_type_bindings(excluded, facility) == []
    assert registry.diagnose_field_bindings(field, facility) == []
    if included:
        assert any(str(backend.name) in name for name in registry.describe_type_bindings(included, facility))
        assert backend.uses_field_template(included_field, included, facility, backend.feld_bindungen[0])


@pytest.mark.parametrize("value", [None, "", True, False, "NaN", "Infinity", "-Infinity", "invalid", [], {}])
def test_invalid_values_do_not_contribute(backend, facility, admin_user, date_range, value):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [0, "2.5", value])
    result = backend.build_dataset(facility, *date_range)
    assert result.records[0]["events"] == 3
    assert result.records[0]["contributors"] == 2
    assert result.records[0]["quantity"] == 2.5
    assert [(issue.code, issue.count) for issue in result.completeness] == [("missing_field_values", 1)]
    exported = export_dataset(backend, facility, *date_range, format="json")
    json.loads(exported.content, parse_constant=lambda value: pytest.fail(f"Invalid JSON number: {value}"))


def test_encrypted_values_and_unreadable_ciphertext(backend, facility, admin_user, date_range):
    doc_type, field = make_type(facility)
    field.is_encrypted = True
    field.save()
    add_events(facility, admin_user, doc_type, field, ["12.5"])
    event = Event.objects.get(document_type=doc_type)
    assert event.data_json[field.slug]["__encrypted__"] is True
    add_events(facility, admin_user, doc_type, field, [None])
    Event.objects.filter(document_type=doc_type, data_json={}).update(
        data_json={field.slug: {"__encrypted__": True, "value": "unreadable"}}
    )
    result = backend.build_dataset(facility, *date_range)
    assert result.records[0]["quantity"] == 12.5
    assert result.records[0]["contributors"] == 1
    assert result.completeness[0].count == 1


def test_duplicate_binding_never_sums_either_field(backend, facility, admin_user, date_range):
    doc_type, field = make_type(facility)
    other = FieldTemplate.objects.create(facility=facility, name="Other quantity", field_type="number")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=other)
    FieldTemplate.objects.filter(pk=other.pk).update(report_key="quantity_units")
    add_events(facility, admin_user, doc_type, field, [100] * 6)
    Event.objects.filter(document_type=doc_type).update(data_json={field.slug: 100, other.slug: 200})
    raw = backend.build_dataset(facility, *date_range)
    assert raw.records[0]["contributors"] == 0
    assert raw.records[0]["quantity"] is None
    assert "duplicate_field_binding" in {issue.code for issue in raw.completeness}
    assert prepared(backend, facility, date_range).summary["quantity_total"] is None


@pytest.mark.parametrize(
    ("field_type", "entity_type", "active_type", "active_field", "available"),
    [
        ("number", "event", True, True, True),
        ("text", "event", True, True, False),
        ("number", "case", True, True, False),
        ("number", "event", False, True, False),
        ("number", "event", True, False, False),
    ],
)
def test_availability_requires_active_event_number_field(
    backend, facility, field_type, entity_type, active_type, active_field, available
):
    _, field = make_type(facility, field_type=field_type, entity_type=entity_type, is_active=active_type)
    FieldTemplate.objects.filter(pk=field.pk).update(is_active=active_field)
    assert backend.is_available(facility) is available


def test_read_path_excludes_other_units_inactive_and_non_number_fields(backend, facility, admin_user, date_range):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [3])
    for name, key, field_type, entity_type in [
        ("Exchange", "ausgabe_stueck", "number", "event"),
        ("Duration", "dauer_minuten", "number", "event"),
        ("Text", "quantity_units", "text", "event"),
        ("Case", "quantity_units", "number", "case"),
    ]:
        other_type, other_field = make_type(facility, name, key=key, field_type=field_type, entity_type=entity_type)
        add_events(facility, admin_user, other_type, other_field, [999])
    inactive_type, inactive_field = make_type(facility, "Inactive field")
    add_events(facility, admin_user, inactive_type, inactive_field, [999])
    FieldTemplate.objects.filter(pk=inactive_field.pk).update(is_active=False)
    raw = backend.build_dataset(facility, *date_range)
    assert len(raw.records) == 1
    assert raw.summary["quantity_total"] == 3
    assert raw.summary["events_total"] == 1


def test_scope_deleted_and_outside_period(
    backend, facility, second_facility, admin_user, second_facility_user, date_range
):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [7])
    foreign_type, foreign_field = make_type(second_facility)
    add_events(second_facility, second_facility_user, foreign_type, foreign_field, [900])
    assert backend.is_available(second_facility)
    add_events(facility, admin_user, doc_type, field, [88, 99])
    Event.objects.filter(document_type=doc_type, data_json={field.slug: 88}).update(is_deleted=True)
    Event.objects.filter(document_type=doc_type, data_json={field.slug: 99}).update(
        occurred_at=timezone.now() - timedelta(days=100)
    )
    assert backend.build_dataset(facility, *date_range).summary["quantity_total"] == 7
    assert (
        backend.build_dataset(facility, date_range[0] - timedelta(days=20), date_range[0] - timedelta(days=10)).records
        == []
    )


@pytest.mark.parametrize("quantity", [0, 100000])
def test_sum_threshold_uses_contributing_events(backend, facility, admin_user, date_range, quantity):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [quantity] * 4 + [None] * 5)
    result = prepared(backend, facility, date_range)
    assert result.records == [{"document_type": "Counter", "contributors": None, "quantity": None}]
    assert result.summary["quantity_total"] is None
    assert result.summary["contributors_total"] is None
    assert all(issue.count is None for issue in result.completeness)


def test_zero_counts_as_contribution_at_threshold(backend, facility, admin_user, date_range):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [0] * 5)
    assert prepared(backend, facility, date_range).records == [
        {"document_type": "Counter", "contributors": 5, "quantity": 0}
    ]


def test_residual_guard_and_quantity_margin(backend, facility, admin_user, date_range):
    for name, values in [("Alpha", [11] * 20), ("Beta", [23] * 9), ("Gamma", [97] * 2)]:
        doc_type, field = make_type(facility, name)
        add_events(facility, admin_user, doc_type, field, values)
    result = prepared(backend, facility, date_range)
    rows = {row["document_type"]: row for row in result.records}
    assert rows["Gamma"]["contributors"] is None
    assert sum(row["contributors"] is None for row in rows.values()) >= 2
    assert rows["Gamma"]["contributors"] is None
    assert rows["Gamma"]["quantity"] is None
    assert result.summary["quantity_total"] is None
    assert result.summary["contributors_total"] is None
    assert "events_total" not in result.summary


@pytest.mark.parametrize("small", [False, True])
def test_panel_csv_pdf_share_published_values(backend, facility, admin_user, date_range, monkeypatch, small):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [37] * (2 if small else 6))
    context = build_report_panel_context(
        facility, backend, SimpleNamespace(date_from=date_range[0], date_to=date_range[1])
    )
    csv_response = export_dataset(backend, facility, *date_range, format="csv")
    csv_rows = list(csv.reader(b"".join(csv_response.streaming_content).decode().splitlines()))
    pdf_text = []

    def write_pdf(html, *args, **kwargs):
        document = html.render()
        pdf_text.append(
            " ".join(
                box.text for page in document.pages for box in page._page_box.descendants() if hasattr(box, "text")
            )
        )
        return document.write_pdf(*args, **kwargs)

    monkeypatch.setattr(weasyprint.HTML, "write_pdf", write_pdf)
    response = export_dataset(backend, facility, *date_range, format="pdf")
    assert response.content.startswith(b"%PDF-")
    expected = ["Counter", None, None] if small else ["Counter", 6, 222]
    assert context["preview_rows"] == [expected]
    data_row = next(row for row in csv_rows if row and row[0] == "Counter")
    assert data_row == [str(value) if value is not None else "" for value in expected]
    expected_pdf_row = "Counter — —" if small else "Counter 6 222"
    assert expected_pdf_row in pdf_text[-1]
    assert ("222" in pdf_text[-1]) is (not small)
    assert "74" not in pdf_text[-1]
    assert all(issue.count is None for issue in context["result"].completeness)


def test_download_uses_existing_release_and_audit_path(client, backend, facility, admin_user, date_range):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [2] * 6)
    client.force_login(admin_user)
    response = client.get(
        reverse("core:statistics_report_export"),
        {
            "backend": backend.slug,
            "format": "csv",
            "date_from": str(date_range[0]),
            "date_to": str(date_range[1]),
            "recipient": "Reviewer",
            "purpose": "Review",
        },
    )
    assert response.status_code == 200
    entry = AuditLog.objects.get(action=AuditLog.Action.EXPORT)
    assert entry.facility_id == facility.pk
    assert entry.detail["backend"] == "quantity-sum"
    assert entry.detail["recipient"] == "Reviewer"
    assert entry.detail["purpose"] == "Review"


def test_large_finite_quantities_sum_exactly_without_nonfinite_json(backend, facility, admin_user, date_range):
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
    doc_type, field = make_type(facility)
    value = 123456789012345678901234567890123456789
    add_events(facility, admin_user, doc_type, field, [value, value])
    result = prepared(backend, facility, date_range)
    assert result.records[0]["quantity"] == value * 2
    assert result.summary["quantity_total"] == value * 2
    payload = json.loads(export_dataset(backend, facility, *date_range, format="json").content)
    assert payload["records"][0]["quantity"] == value * 2


def test_sum_stays_hidden_when_contributor_count_exposes_small_missing_gap(backend, facility, admin_user, date_range):
    doc_type, field = make_type(facility)
    add_events(facility, admin_user, doc_type, field, [3] * 5 + [None])
    result = prepared(backend, facility, date_range)
    assert "events" not in result.records[0]
    assert result.records[0]["contributors"] is None
    assert result.records[0]["quantity"] is None
    assert result.summary["contributors_total"] is None
    assert result.summary["quantity_total"] is None


def test_duplicate_key_on_text_field_also_blocks_numeric_sum(backend, facility, admin_user, date_range):
    doc_type, field = make_type(facility)
    other = FieldTemplate.objects.create(facility=facility, name="Text quantity", field_type="text")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=other)
    FieldTemplate.objects.filter(pk=other.pk).update(report_key="quantity_units")
    add_events(facility, admin_user, doc_type, field, [100] * 6)
    result = backend.build_dataset(facility, *date_range)
    assert result.records[0]["quantity"] is None
    assert result.records[0]["contributors"] == 0
    assert "duplicate_field_binding" in {issue.code for issue in result.completeness}


@pytest.mark.parametrize("unbound_count", [2, 0])
def test_cross_report_exports_share_document_type_suppression(client, facility, lead_user, date_range, unbound_count):
    """A bound subtotal must not recover hidden types from another report."""
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
    for name, count, value in [("Alpha", 7, 1), ("Bravo", 3, 50)]:
        doc_type, field = make_type(facility, name)
        add_events(facility, lead_user, doc_type, field, [value] * count)
    unbound = DocumentType.objects.create(facility=facility, name="Charlie")
    for _ in range(unbound_count):
        Event.objects.create(facility=facility, created_by=lead_user, document_type=unbound, occurred_at=timezone.now())
    client.force_login(lead_user)
    payloads = {}
    for slug in ["general-key-figures", "quantity-sum"]:
        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": slug,
                "format": "json",
                "date_from": str(date_range[0]),
                "date_to": str(date_range[1]),
                "recipient": "Funder",
                "purpose": "Annual report",
            },
        )
        assert response.status_code == 200
        payloads[slug] = json.loads(response.content)
    general = {
        row["label"]: row["count"]
        for row in payloads["general-key-figures"]["records"]
        if row["table"] == "by_document_type"
    }
    quantity = payloads["quantity-sum"]
    assert general["Bravo"] is None
    if unbound_count:
        assert general["Alpha"] == 7 and general["Charlie"] is None
    else:
        assert general["Alpha"] is None
    assert "events_total" not in quantity["summary"]
    assert quantity["summary"]["contributors_total"] is None
    assert quantity["summary"]["quantity_total"] is None
    for row in quantity["records"]:
        assert "events" not in row
        if general.get(row["document_type"]) is None:
            assert row["contributors"] is None and row["quantity"] is None
    releases = AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.EXPORT)
    assert releases.count() == 2
    assert all(entry.detail["artifact_released"] is True for entry in releases)


def test_sum_export_hides_type_suppressed_by_general_figures(client, facility, lead_user, date_range):
    doc_type, field = make_type(facility, "Alpha")
    add_events(facility, lead_user, doc_type, field, [1] * 7)
    other = DocumentType.objects.create(facility=facility, name="Bravo")
    for _ in range(3):
        Event.objects.create(facility=facility, created_by=lead_user, document_type=other, occurred_at=timezone.now())
    client.force_login(lead_user)
    payloads = []
    for slug in ["general-key-figures", "quantity-sum"]:
        response = client.get(
            reverse("core:statistics_report_export"),
            {"backend": slug, "format": "json", "date_from": str(date_range[0]), "date_to": str(date_range[1])},
        )
        assert response.status_code == 200
        payloads.append(json.loads(response.content))
    assert (
        next(
            row["count"]
            for row in payloads[0]["records"]
            if row["table"] == "by_document_type" and row["label"] == "Alpha"
        )
        is None
    )
    assert payloads[1]["records"][0]["contributors"] is None
    assert payloads[1]["records"][0]["quantity"] is None


@pytest.mark.parametrize("contact_key,other_key", [("contact", "counseling"), ("medical", "referral")])
@pytest.mark.parametrize("download_order", [("quantity-sum", "sample-report-1"), ("sample-report-1", "quantity-sum")])
def test_quantity_category_exports_keep_hidden_cell_ambiguous(
    client, facility, lead_user, date_range, contact_key, other_key, download_order
):
    """A contributor lower bound must not unlock a hidden category subtotal."""
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
    doc_type, field = make_type(facility, "Alpha", report_key=contact_key)
    add_events(facility, lead_user, doc_type, field, [1] * 7)
    for name, key, count in [("Bravo", other_key, 1), ("Charlie", "excluded", 4)]:
        other = DocumentType.objects.create(facility=facility, name=name, report_key=key)
        for _ in range(count):
            Event.objects.create(
                facility=facility, document_type=other, created_by=lead_user, occurred_at=timezone.now()
            )
    client.force_login(lead_user)
    payloads = {}
    for slug in download_order:
        response = client.get(
            reverse("core:statistics_report_export"),
            {"backend": slug, "format": "json", "date_from": str(date_range[0]), "date_to": str(date_range[1])},
        )
        assert response.status_code == 200
        payloads[slug] = json.loads(response.content)
    category = payloads["sample-report-1"]
    quantity = payloads["quantity-sum"]
    assert len(category["records"]) == 2
    assert all(row["count"] is None for row in category["records"])
    # Both categories are positive. A total of eight and a lower bound of seven
    # would force the other category to exactly one, even without equal units.
    total = category["summary"]["total"]
    lower_bound = quantity["records"][0]["contributors"]
    possible_hidden_counts = range(1, 5) if total is None or lower_bound is None else range(1, total - lower_bound + 1)
    assert len(possible_hidden_counts) > 1, "The hidden category must not be exactly recoverable from both downloads"
    assert lower_bound is None
    assert quantity["records"][0]["quantity"] is None
    assert quantity["summary"]["contributors_total"] is None
    assert quantity["summary"]["quantity_total"] is None
    releases = AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.EXPORT)
    assert releases.count() == 2
    assert all(entry.detail["artifact_released"] is True for entry in releases)


def test_category_total_cannot_unlock_hidden_quantity_type(client, facility, lead_user, date_range):
    """Different type keys sharing one category must keep hidden types ambiguous."""
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
    for name, key, count in [("Alpha", "counseling", 7), ("Bravo", "crisis", 1)]:
        doc_type, field = make_type(facility, name, report_key=key)
        add_events(facility, lead_user, doc_type, field, [1] * count)
    outside = DocumentType.objects.create(facility=facility, name="Charlie", report_key="excluded")
    for _ in range(4):
        Event.objects.create(facility=facility, document_type=outside, created_by=lead_user, occurred_at=timezone.now())
    client.force_login(lead_user)
    payloads = {}
    for slug in ("sample-report-1", "quantity-sum"):
        response = client.get(
            reverse("core:statistics_report_export"),
            {"backend": slug, "format": "json", "date_from": str(date_range[0]), "date_to": str(date_range[1])},
        )
        assert response.status_code == 200
        payloads[slug] = json.loads(response.content)
    sample = payloads["sample-report-1"]
    quantity = {row["document_type"]: row for row in payloads["quantity-sum"]["records"]}
    assert sample["records"] == [{"category": "Beratung", "count": 8}]
    assert quantity["Bravo"]["contributors"] is None
    # Bravo's row proves at least one event exists. Both types exhaust the
    # category, so eight minus an Alpha lower bound of seven would force one.
    total = sample["summary"]["total"]
    lower_bound = quantity["Alpha"]["contributors"]
    possible_hidden_counts = range(1, 5) if lower_bound is None or total is None else range(1, total - lower_bound + 1)
    assert len(possible_hidden_counts) > 1, "A category total must not pin the hidden type to one event"
    assert lower_bound is None
    assert quantity["Alpha"]["quantity"] is None
    releases = AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.EXPORT)
    assert releases.count() == 2
    assert all(entry.detail["artifact_released"] is True for entry in releases)
