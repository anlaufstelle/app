"""Zentrale Strichlisten-Eignungs-Policy (Refs #1908)."""

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.forms.tally import TallyNumberDataForm
from core.models import DocumentType, DocumentTypeField, Event, FieldCondition, FieldTemplate
from core.services.events import (
    TallyIneligibilityReason,
    TallyMode,
    evaluate_tally_eligibility,
    tally_document_types,
    tally_eligibilities,
)
from core.services.events import tally as tally_service
from tests._tally_helpers import post_tally


def _assignment(
    document_type,
    *,
    name,
    field_type=FieldTemplate.FieldType.NUMBER,
    required=True,
    sensitivity="",
    sort_order=0,
):
    template = FieldTemplate.objects.create(
        facility=document_type.facility,
        name=name,
        field_type=field_type,
        is_required=required,
        sensitivity=sensitivity,
    )
    return DocumentTypeField.objects.create(
        document_type=document_type,
        field_template=template,
        sort_order=sort_order,
    )


@pytest.mark.django_db
class TestTallyEligibilityPolicy:
    def test_returns_structured_direct_and_number_modes(self, facility, staff_user):
        direct = DocumentType.objects.create(
            facility=facility,
            name="Direkter Kontakt",
            tally_enabled=True,
        )
        numbered = DocumentType.objects.create(
            facility=facility,
            name="Zahlenkontakt",
            tally_enabled=True,
        )
        second = _assignment(numbered, name="Rückgabe", sort_order=2)
        first = _assignment(numbered, name="Ausgabe", sort_order=1)

        direct_result = evaluate_tally_eligibility(direct, staff_user)
        number_result = evaluate_tally_eligibility(numbered, staff_user)

        assert direct_result.eligible is True
        assert direct_result.mode is TallyMode.DIRECT
        assert direct_result.required_number_fields == ()
        assert direct_result.reasons == ()
        assert number_result.eligible is True
        assert number_result.mode is TallyMode.NUMBER
        assert number_result.required_number_fields == (first, second)

    def test_conditional_required_field_is_conservatively_ineligible(self, facility, staff_user):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Bedingte Zahlen",
            tally_enabled=True,
        )
        source = _assignment(document_type, name="Auslöser", required=False)
        target = _assignment(document_type, name="Bedingte Anzahl")
        FieldCondition.objects.create(
            target_field=target,
            depends_on_field=source,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=0,
        )

        result = evaluate_tally_eligibility(document_type, staff_user)

        assert result.eligible is False
        assert result.mode is None
        assert result.reasons == (TallyIneligibilityReason.CONDITIONAL_REQUIRED_FIELD,)
        assert document_type.pk not in {
            entry.document_type.pk for entry in tally_eligibilities(facility, staff_user) if entry.eligible
        }

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"tally_enabled": False}, TallyIneligibilityReason.OPT_IN_DISABLED),
            ({"is_active": False}, TallyIneligibilityReason.INACTIVE),
            (
                {"entity_type": DocumentType.EntityType.CLIENT},
                TallyIneligibilityReason.UNSUPPORTED_ENTITY_TYPE,
            ),
            ({"min_contact_stage": "identified"}, TallyIneligibilityReason.REQUIRES_CONTACT),
        ],
    )
    def test_stable_structural_reasons(self, facility, staff_user, overrides, reason):
        values = {
            "facility": facility,
            "name": f"Policy {reason.value}",
            "tally_enabled": True,
            **overrides,
        }
        document_type = DocumentType.objects.create(**values)

        result = evaluate_tally_eligibility(document_type, staff_user)

        assert result.eligible is False
        assert reason in result.reasons

    def test_role_restrictions_are_explicit_policy_reasons(self, facility, staff_user, lead_user):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Hochsensibles Feld",
            tally_enabled=True,
        )
        _assignment(
            document_type,
            name="Nur Leitung",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )

        staff_result = evaluate_tally_eligibility(document_type, staff_user)
        lead_result = evaluate_tally_eligibility(document_type, lead_user)

        assert staff_result.reasons == (TallyIneligibilityReason.RESTRICTED_REQUIRED_FIELD,)
        assert lead_result.eligible is True
        assert lead_result.mode is TallyMode.NUMBER

    def test_bulk_policy_query_count_is_constant(self, facility, staff_user):
        baseline = DocumentType.objects.create(
            facility=facility,
            name="Baseline",
            tally_enabled=True,
        )
        _assignment(baseline, name="Baseline-Zahl")

        with CaptureQueriesContext(connection) as small_context:
            small = tally_eligibilities(facility, staff_user)
        assert any(result.document_type == baseline for result in small)

        for index in range(8):
            document_type = DocumentType.objects.create(
                facility=facility,
                name=f"Weitere Zählung {index}",
                tally_enabled=True,
            )
            _assignment(document_type, name=f"Zahl {index}")

        with CaptureQueriesContext(connection) as large_context:
            large = tally_eligibilities(facility, staff_user)
        assert len(large) >= len(small) + 8
        assert len(large_context) == len(small_context)

    def test_bulk_policy_prefilters_statically_impossible_types_and_their_fields(
        self,
        facility,
        staff_user,
        monkeypatch,
    ):
        eligible = DocumentType.objects.create(
            facility=facility,
            name="Kandidat",
            tally_enabled=True,
        )
        _assignment(eligible, name="Kandidat-Zahl")
        conditional = DocumentType.objects.create(
            facility=facility,
            name="Bedingter Kandidat",
            tally_enabled=True,
        )
        source = _assignment(conditional, name="Bedingte Quelle", required=False)
        target = _assignment(conditional, name="Bedingtes Ziel")
        FieldCondition.objects.create(
            target_field=target,
            depends_on_field=source,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=0,
        )

        impossible_specs = [
            ("Opt-out", {"tally_enabled": False}),
            ("Inaktiv", {"is_active": False}),
            ("Personentyp", {"entity_type": DocumentType.EntityType.CLIENT}),
            ("Falltyp", {"entity_type": DocumentType.EntityType.CASE}),
            ("Person erforderlich", {"min_contact_stage": "identified"}),
            ("Dokumenttyp gesperrt", {"sensitivity": DocumentType.Sensitivity.HIGH}),
        ]
        impossible = []
        for index, (name, overrides) in enumerate(impossible_specs):
            document_type = DocumentType.objects.create(
                facility=facility,
                name=name,
                **{"tally_enabled": True, **overrides},
            )
            _assignment(document_type, name=f"Ausgeschlossene Zahl {index}")
            impossible.append(document_type)

        evaluated_ids = []
        original_evaluate = tally_service.evaluate_tally_eligibility

        def tracked_evaluate(document_type, user=None):
            evaluated_ids.append(document_type.pk)
            return original_evaluate(document_type, user)

        monkeypatch.setattr(tally_service, "evaluate_tally_eligibility", tracked_evaluate)
        with CaptureQueriesContext(connection) as context:
            results = tally_service.tally_eligibilities(facility, staff_user)

        assert {result.document_type.pk for result in results} == {eligible.pk, conditional.pk}
        assert set(evaluated_ids) == {eligible.pk, conditional.pk}
        assignment_queries = [
            query["sql"] for query in context.captured_queries if "core_documenttypefield" in query["sql"]
        ]
        assert len(assignment_queries) == 1
        assert eligible.pk.hex in assignment_queries[0]
        assert conditional.pk.hex in assignment_queries[0]
        for document_type in impossible:
            assert document_type.pk.hex not in assignment_queries[0]


@pytest.mark.django_db
class TestTallyPolicyConsumers:
    def test_tally_document_types_uses_pk_as_name_tiebreak(self, facility, staff_user):
        queryset = tally_document_types(facility, staff_user)

        assert queryset.query.order_by == ("name", "pk")

    def test_tally_document_types_keeps_filterable_queryset_contract_and_annotation(
        self,
        facility,
        staff_user,
    ):
        direct = DocumentType.objects.create(
            facility=facility,
            name="Direkt per QuerySet",
            tally_enabled=True,
        )
        numbered = DocumentType.objects.create(
            facility=facility,
            name="Zahl per QuerySet",
            tally_enabled=True,
        )
        _assignment(numbered, name="QuerySet-Zahl")
        DocumentType.objects.create(
            facility=facility,
            name="Nicht angeboten",
            tally_enabled=False,
        )

        queryset = tally_document_types(facility, staff_user)

        assert isinstance(queryset, QuerySet)
        assert queryset.filter(pk=direct.pk).exists()
        assert queryset.filter(pk=numbered.pk).count() == 1
        assert queryset.count() == 2
        assert queryset.get(pk=direct.pk).requires_number_input is False
        assert queryset.get(pk=numbered.pk).requires_number_input is True

    def test_form_builds_only_fields_supplied_by_policy(self, facility, staff_user):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Formular-Policy",
            tally_enabled=True,
        )
        required = _assignment(document_type, name="Erforderliche Zahl")
        optional = _assignment(document_type, name="Optionale Zahl", required=False, sort_order=1)
        eligibility = evaluate_tally_eligibility(document_type, staff_user)

        form = TallyNumberDataForm(eligibility=eligibility)

        assert list(form.fields) == [required.field_template.slug]
        assert optional.field_template.slug not in form.fields

    def test_conditional_required_type_is_absent_and_post_rejected_by_same_policy(
        self,
        client,
        facility,
        staff_user,
    ):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Bedingter POST",
            tally_enabled=True,
        )
        source = _assignment(document_type, name="Quelle", required=False)
        target = _assignment(document_type, name="Ziel")
        FieldCondition.objects.create(
            target_field=target,
            depends_on_field=source,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=0,
        )
        client.force_login(staff_user)

        response = post_tally(
            client,
            {
                "document_type": str(document_type.pk),
                source.field_template.slug: "1",
                target.field_template.slug: "2",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert "bedingtes Pflichtfeld" in response.content.decode()
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_config_detail_distinguishes_unavailable_opt_in_and_shows_reason(
        self,
        client,
        facility,
        admin_user,
    ):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Ungeeigneter Typ",
            tally_enabled=True,
        )
        _assignment(
            document_type,
            name="Pflichttext",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        client.force_login(admin_user)

        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": document_type.pk}))
        content = response.content.decode()

        assert response.context["tally_eligibility"].eligible is False
        assert 'data-testid="doctype-tally-status"' in content
        assert "Opt-in aktiv, aber nicht verfügbar" in content
        assert "Nicht unterstütztes Pflichtfeld" in content

    def test_config_detail_describes_eligible_status_as_role_dependent(
        self,
        client,
        facility,
        admin_user,
    ):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Geeigneter Typ",
            tally_enabled=True,
        )
        client.force_login(admin_user)

        content = client.get(reverse("core:config_doctype_detail", kwargs={"pk": document_type.pk})).content.decode()

        assert "Angeboten für berechtigte Rollen" in content
