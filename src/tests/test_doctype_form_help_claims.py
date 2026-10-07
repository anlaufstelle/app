"""Document type form help: every claim must match what the code does (Refs #2460).

The rendered help is checked against the false wordings found in review (document type
sensitivity does not encrypt; tally exclusion reasons; contact stage check) and against
the application paths the statements rely on.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Client, DocumentType, DocumentTypeField, FieldCondition, FieldTemplate
from core.services.events.crud import _validate_contact_stage, create_event
from core.services.events.tally import TallyIneligibilityReason, evaluate_tally_eligibility
from core.services.file_vault import is_encrypted_value
from tests.test_doctype_form_field_help import _parse

pytestmark = pytest.mark.django_db


@pytest.fixture
def create_page(client, admin_user):
    client.force_login(admin_user)
    return _parse(client.get(reverse("core:config_doctype_create")).content.decode())


def _field_help_text(page, field):
    """Inline help plus the expandable block of one field, as visible text."""
    inline = page.find_by_id(f"id_{field}-help").text()
    deep = page.find_all("details", data_field_help=field)[0].text()
    return f"{inline} {deep}"


class TestSensitivityDoesNotPromiseEncryption:
    def test_help_never_says_high_is_always_encrypted(self, create_page):
        text = _field_help_text(create_page, "sensitivity")

        assert "immer verschlüsselt" not in text
        assert "„Hoch“ wird" not in text

    def test_help_names_the_field_template_as_the_place_of_encryption(self, create_page):
        text = _field_help_text(create_page, "sensitivity")

        assert "Feldvorlage" in text
        assert "legt die Feldvorlage fest" in text

    def test_form_help_text_is_not_the_model_text_with_the_wrong_promise(self):
        from core.forms.config import DocumentTypeForm

        assert "immer verschlüsselt" not in str(DocumentTypeForm().fields["sensitivity"].help_text)

    def test_high_document_type_alone_leaves_an_inheriting_field_in_plaintext(self, facility, admin_user):
        """The code fact the help relies on: only the field template encrypts."""
        document_type = DocumentType.objects.create(
            facility=facility, name="High type", sensitivity=DocumentType.Sensitivity.HIGH
        )
        template = FieldTemplate.objects.create(
            facility=facility, name="Inherited note", field_type=FieldTemplate.FieldType.TEXTAREA
        )
        DocumentTypeField.objects.create(document_type=document_type, field_template=template)

        event = create_event(
            facility=facility,
            user=admin_user,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={template.slug: "Plain content"},
            is_anonymous=True,
        )
        event.refresh_from_db()

        assert not template.is_encrypted
        assert not is_encrypted_value(event.data_json[template.slug])

    def test_high_field_template_forces_encryption(self, facility):
        """The other half of the statement: HIGH on the field template encrypts."""
        template = FieldTemplate.objects.create(
            facility=facility,
            name="High note",
            field_type=FieldTemplate.FieldType.TEXTAREA,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )

        assert template.is_encrypted


#: One needle per exclusion reason of ``evaluate_tally_eligibility`` that must be named.
TALLY_REASON_NEEDLES = {
    TallyIneligibilityReason.OPT_IN_DISABLED: "freigegeben",
    TallyIneligibilityReason.INACTIVE: "aktiv",
    TallyIneligibilityReason.UNSUPPORTED_ENTITY_TYPE: "Ereignis",
    TallyIneligibilityReason.REQUIRES_CONTACT: "Mindest-Kontaktstufe",
    TallyIneligibilityReason.RESTRICTED_DOCUMENT_TYPE: "Rolle",
    TallyIneligibilityReason.CONDITIONAL_REQUIRED_FIELD: "Bedingung",
    TallyIneligibilityReason.UNSUPPORTED_REQUIRED_FIELD: "anderen Typs",
    TallyIneligibilityReason.RESTRICTED_REQUIRED_FIELD: "alle Pflichtfelder sehen",
}


class TestAdminFormSensitivityHelp:
    """The Django admin renders the model help text; it must make the same claim as the form."""

    @pytest.fixture
    def admin_form(self, rf, super_admin_user):
        from core.admin.documents import DocumentTypeAdmin
        from core.admin_site import anlaufstelle_admin_site

        request = rf.get("/admin-mgmt/core/documenttype/add/")
        request.user = super_admin_user
        return DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site).get_form(request, None)

    def test_admin_help_never_says_high_is_always_encrypted(self, admin_form):
        text = str(admin_form.base_fields["sensitivity"].help_text)

        assert "immer verschlüsselt" not in text
        assert "legt die Feldvorlage fest" in text

    def test_model_admin_and_form_show_the_same_text(self, admin_form):
        from core.forms.config import DocumentTypeForm

        model_text = str(DocumentType._meta.get_field("sensitivity").help_text)

        assert str(admin_form.base_fields["sensitivity"].help_text) == model_text
        assert str(DocumentTypeForm().fields["sensitivity"].help_text) == model_text


class TestTallyHelpNamesEveryExclusionReason:
    def test_the_needle_table_covers_every_reason(self):
        assert set(TALLY_REASON_NEEDLES) == set(TallyIneligibilityReason)

    @pytest.mark.parametrize("reason", list(TallyIneligibilityReason))
    def test_deep_help_names_the_reason(self, create_page, reason):
        deep = create_page.find_all("details", data_field_help="tally_enabled")[0].text()

        assert TALLY_REASON_NEEDLES[reason] in deep, reason

    def test_number_mode_is_restricted_to_unconditional_number_fields(self, create_page):
        deep = create_page.find_all("details", data_field_help="tally_enabled")[0].text()

        assert "ohne Bedingung" in deep

    def test_numeric_required_field_with_condition_excludes_the_type(self, facility):
        """The code fact behind the sentence: a condition on a number field excludes the type."""
        document_type = DocumentType.objects.create(facility=facility, name="Numeric tally", tally_enabled=True)
        source = FieldTemplate.objects.create(facility=facility, name="Source", field_type=FieldTemplate.FieldType.TEXT)
        target = FieldTemplate.objects.create(
            facility=facility, name="Amount", field_type=FieldTemplate.FieldType.NUMBER, is_required=True
        )
        source_assignment = DocumentTypeField.objects.create(document_type=document_type, field_template=source)
        target_assignment = DocumentTypeField.objects.create(document_type=document_type, field_template=target)
        FieldCondition.objects.create(
            target_field=target_assignment,
            depends_on_field=source_assignment,
            operator=FieldCondition.Operator.IS_NOT_EMPTY,
        )

        eligibility = evaluate_tally_eligibility(document_type)

        assert eligibility.mode is None
        assert eligibility.reasons == (TallyIneligibilityReason.CONDITIONAL_REQUIRED_FIELD,)


class TestContactStageHelpDescribesTheStageCheck:
    def test_help_does_not_promise_a_check_of_stored_details(self, create_page):
        text = _field_help_text(create_page, "min_contact_stage")

        assert "zusätzliche Angaben gespeichert" not in text
        assert "Angaben gespeichert" not in text

    def test_help_names_the_stage_that_the_person_must_have(self, create_page):
        deep = create_page.find_all("details", data_field_help="min_contact_stage")[0].text()

        assert "mindestens die Kontaktstufe „Mit Zusatzangaben“" in deep
        assert "geprüft wird die Kontaktstufe, nicht der Inhalt der Personenakte" in deep

    def test_only_the_stored_stage_is_compared_not_the_content(self):
        """The code fact behind the sentence: the stage alone decides, details are not read."""
        document_type = DocumentType(min_contact_stage=Client.ContactStage.QUALIFIED)
        person = Client(pseudonym="Only pseudonym", contact_stage=Client.ContactStage.QUALIFIED)

        assert not person.data_json
        _validate_contact_stage(document_type, person, is_anonymous=False)
