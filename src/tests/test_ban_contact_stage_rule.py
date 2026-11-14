"""A ban document type needs a minimum contact stage on every path.

Covers the ways a ban type (``system_type: ban``) could end up without
``min_contact_stage``: the package schema (own packages, re-applying a package),
the configuration form (create and edit), the Django admin form and the model
backstop (service, import and shell paths). Other types stay valid without a
stage, and a partial save that touches neither field keeps working for types
that predate the rule.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.forms.config import DocumentTypeForm
from core.models import Client, DocumentType
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.packages.schema import PackageValidationError, parse_package
from tests._factories import make_document_type

BAN = DocumentType.SystemType.BAN
IDENTIFIED = Client.ContactStage.IDENTIFIED
QUALIFIED = Client.ContactStage.QUALIFIED


def _ban_package(**doc_type_overrides: Any) -> dict[str, Any]:
    doc_type: dict[str, Any] = {
        "slug": "ban-type",
        "name": "Ban type",
        "entity_type": "event",
        "system_type": "ban",
        "report_key": "excluded",
        "min_contact_stage": "identified",
    }
    doc_type.update(doc_type_overrides)
    return {"schema_version": 1, "field_templates": [], "document_types": [doc_type]}


# ---------------------------------------------------------------------------
# Package schema
# ---------------------------------------------------------------------------


class TestSchema:
    @staticmethod
    def _error_paths(raw):
        with pytest.raises(PackageValidationError) as excinfo:
            parse_package(raw)
        return [path for path, _message in excinfo.value.errors]

    def test_ban_without_stage_is_rejected_at_the_stage_path(self):
        raw = _ban_package()
        del raw["document_types"][0]["min_contact_stage"]

        assert self._error_paths(raw) == ["document_types[0].min_contact_stage"]

    def test_ban_with_null_stage_is_rejected_at_the_stage_path(self):
        raw = _ban_package(min_contact_stage=None)

        assert self._error_paths(raw) == ["document_types[0].min_contact_stage"]

    @pytest.mark.parametrize("stage", ["identified", "qualified"])
    def test_ban_with_a_stage_is_accepted(self, stage):
        parsed = parse_package(_ban_package(min_contact_stage=stage))

        assert parsed.document_types[0].min_contact_stage == stage

    def test_non_ban_types_stay_valid_without_a_stage(self):
        for system_type in (None, "crisis"):
            raw = _ban_package(system_type=system_type)
            del raw["document_types"][0]["min_contact_stage"]

            assert parse_package(raw).document_types[0].min_contact_stage is None

    def test_a_removed_legacy_system_type_does_not_demand_a_stage(self):
        raw = _ban_package(system_type="medical")
        del raw["document_types"][0]["min_contact_stage"]

        assert parse_package(raw).document_types[0].system_type is None


# ---------------------------------------------------------------------------
# Re-applying a package (resync trap)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReapplyingAPackage:
    def test_reapplying_never_removes_the_stage_of_a_ban_type(self, facility):
        package = parse_catalog_package("default")
        load_package(facility, package)
        DocumentType.objects.filter(facility=facility, slug="hausverbot").update(min_contact_stage=QUALIFIED)

        load_package(facility, package)

        doc_type = DocumentType.objects.get(facility=facility, slug="hausverbot")
        assert doc_type.min_contact_stage == IDENTIFIED

    def test_reapplying_fills_a_stage_that_is_missing_on_an_existing_ban_type(self, facility):
        package = parse_catalog_package("default")
        load_package(facility, package)
        DocumentType.objects.filter(facility=facility, slug="hausverbot").update(min_contact_stage=None)

        report = load_package(facility, package)

        assert not report.errors
        doc_type = DocumentType.objects.get(facility=facility, slug="hausverbot")
        assert doc_type.min_contact_stage == IDENTIFIED

    def test_a_package_that_would_clear_the_stage_is_refused_before_anything_is_written(self, facility):
        load_package(facility, parse_catalog_package("default"))

        with pytest.raises(PackageValidationError):
            parse_package(_ban_package(slug="hausverbot", min_contact_stage=None))

        doc_type = DocumentType.objects.get(facility=facility, slug="hausverbot")
        assert doc_type.min_contact_stage == IDENTIFIED


# ---------------------------------------------------------------------------
# Configuration form
# ---------------------------------------------------------------------------


def _form_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Property ban",
        "sensitivity": DocumentType.Sensitivity.NORMAL,
        "is_active": "on",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestForm:
    def test_creating_a_ban_type_without_a_stage_is_a_field_error(self, facility):
        form = DocumentTypeForm(_form_data(system_type=BAN), facility=facility)

        assert not form.is_valid()
        assert list(form.errors) == ["min_contact_stage"]
        assert form.errors["min_contact_stage"] == [
            "Ein Hausverbot braucht eine Person: Bitte eine Mindest-Kontaktstufe wählen."
        ]

    def test_creating_a_ban_type_with_a_stage_is_valid(self, facility):
        form = DocumentTypeForm(_form_data(system_type=BAN, min_contact_stage=QUALIFIED), facility=facility)

        assert form.is_valid(), form.errors

    def test_creating_other_types_without_a_stage_stays_valid(self, facility):
        for system_type in ("", DocumentType.SystemType.CRISIS):
            form = DocumentTypeForm(_form_data(system_type=system_type), facility=facility)

            assert form.is_valid(), form.errors

    def test_clearing_the_stage_of_an_existing_ban_type_is_a_field_error(self, facility):
        ban_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)

        form = DocumentTypeForm(_form_data(name="Ban", min_contact_stage=""), instance=ban_type, facility=facility)

        assert not form.is_valid()
        assert list(form.errors) == ["min_contact_stage"]

    def test_editing_a_legacy_ban_type_without_a_stage_demands_one(self, facility):
        ban_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)
        DocumentType.objects.filter(pk=ban_type.pk).update(min_contact_stage=None)
        ban_type.refresh_from_db()

        form = DocumentTypeForm(_form_data(name="Ban"), instance=ban_type, facility=facility)

        assert not form.is_valid()
        assert form.errors["min_contact_stage"] == [
            "Ein Hausverbot braucht eine Person: Bitte eine Mindest-Kontaktstufe wählen."
        ]

    def test_editing_a_ban_type_with_a_stage_is_valid(self, facility):
        ban_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)

        form = DocumentTypeForm(
            _form_data(name="Ban", min_contact_stage=QUALIFIED), instance=ban_type, facility=facility
        )

        assert form.is_valid(), form.errors

    def test_editing_a_non_ban_type_may_leave_the_stage_empty(self, facility):
        crisis = make_document_type(facility=facility, name="Crisis", system_type=DocumentType.SystemType.CRISIS)

        form = DocumentTypeForm(_form_data(name="Crisis"), instance=crisis, facility=facility)

        assert form.is_valid(), form.errors

    def test_form_hands_the_prefill_to_the_browser(self, facility):
        form = DocumentTypeForm(facility=facility)

        assert form.min_contact_stage_defaults_attr == "ban:identified"

    def test_create_view_rejects_a_ban_type_without_a_stage_and_shows_the_message_once(
        self, client, admin_user, facility
    ):
        client.force_login(admin_user)

        response = client.post(reverse("core:config_doctype_create"), _form_data(system_type=BAN))

        assert response.status_code == 200
        assert not DocumentType.objects.filter(facility=facility, name="Property ban").exists()
        message = "Ein Hausverbot braucht eine Person: Bitte eine Mindest-Kontaktstufe wählen."
        assert response.content.decode().count(message) == 1

    def test_create_view_stores_a_ban_type_with_a_stage(self, client, admin_user, facility):
        client.force_login(admin_user)

        response = client.post(
            reverse("core:config_doctype_create"), _form_data(system_type=BAN, min_contact_stage=IDENTIFIED)
        )

        assert response.status_code == 302
        stored = DocumentType.objects.get(facility=facility, name="Property ban")
        assert (stored.system_type, stored.min_contact_stage) == (BAN, IDENTIFIED)

    def test_edit_view_rejects_clearing_the_stage(self, client, admin_user, facility):
        ban_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)
        client.force_login(admin_user)

        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": ban_type.pk}),
            _form_data(name="Ban", min_contact_stage=""),
        )

        assert response.status_code == 200
        ban_type.refresh_from_db()
        assert ban_type.min_contact_stage == IDENTIFIED

    def test_form_page_marks_the_field_for_the_ban_prefill_and_explains_the_rule(self, client, admin_user):
        client.force_login(admin_user)

        html = client.get(reverse("core:config_doctype_create")).content.decode()

        assert 'data-min-contact-stage-defaults="ban:identified"' in html
        assert "Bei Systemtyp Hausverbot Pflicht." in html

    def test_edit_page_of_a_ban_type_renders_the_required_marker(self, client, admin_user, facility):
        ban_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)
        client.force_login(admin_user)

        html = client.get(reverse("core:config_doctype_update", kwargs={"pk": ban_type.pk})).content.decode()

        label = html.split('for="id_min_contact_stage"', 1)[1].split("</label>", 1)[0]
        assert "data-required-marker" in label
        assert "hidden" not in label


# ---------------------------------------------------------------------------
# Model backstop
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestModelBackstop:
    @pytest.mark.parametrize("stage", [None, ""])
    def test_saving_a_ban_type_without_a_stage_is_refused(self, facility, stage):
        with pytest.raises(ValidationError):
            DocumentType.objects.create(facility=facility, name="Ban", system_type=BAN, min_contact_stage=stage)

        assert not DocumentType.objects.filter(facility=facility, name="Ban").exists()

    def test_clean_reports_the_missing_stage_on_the_field(self, facility):
        doc_type = DocumentType(facility=facility, name="Ban", system_type=BAN)

        with pytest.raises(ValidationError) as excinfo:
            doc_type.full_clean(exclude=["slug"])

        assert "min_contact_stage" in excinfo.value.message_dict

    def test_clearing_the_stage_of_a_saved_ban_type_is_refused(self, facility):
        doc_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)
        doc_type.min_contact_stage = None

        with pytest.raises(ValidationError):
            doc_type.save()

        doc_type.refresh_from_db()
        assert doc_type.min_contact_stage == IDENTIFIED

    @pytest.mark.parametrize("stage", [IDENTIFIED, QUALIFIED])
    def test_ban_type_with_a_stage_saves(self, facility, stage):
        doc_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=stage)

        assert DocumentType.objects.get(pk=doc_type.pk).min_contact_stage == stage

    def test_other_types_save_without_a_stage(self, facility):
        make_document_type(facility=facility, name="Plain")
        make_document_type(facility=facility, name="Crisis", system_type=DocumentType.SystemType.CRISIS)

    def test_a_legacy_ban_type_without_a_stage_can_still_be_deactivated(self, facility):
        doc_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)
        DocumentType.objects.filter(pk=doc_type.pk).update(min_contact_stage=None)
        doc_type.refresh_from_db()

        doc_type.is_active = False
        doc_type.save(update_fields=["is_active"])

        doc_type.refresh_from_db()
        assert doc_type.is_active is False


@pytest.mark.django_db
def test_admin_form_refuses_a_ban_type_without_a_stage(rf, admin_user, facility):
    from core.admin_site import anlaufstelle_admin_site

    model_admin = anlaufstelle_admin_site._registry[DocumentType]
    request = rf.get("/admin/core/documenttype/add/")
    request.user = admin_user
    form_class = model_admin.get_form(request)

    def form_for(**extra):
        data = {
            "facility": str(facility.pk),
            "name": "Admin ban",
            "sensitivity": DocumentType.Sensitivity.NORMAL,
            "entity_type": DocumentType.EntityType.EVENT,
            "system_type": BAN,
            "is_active": "on",
            "sort_order": "0",
        }
        data.update(extra)
        return form_class(data)

    assert "min_contact_stage" in form_for().errors
    assert "min_contact_stage" not in form_for(min_contact_stage=IDENTIFIED).errors
