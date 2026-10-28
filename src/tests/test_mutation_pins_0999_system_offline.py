"""Pins for offline visibility defaults and document-type loading.

Source: mutation delta of the release-0.99.9 checkpoint, 4 mutants.
Groups: missing or inaccessible types (1), absent role flag (1), type loading (2).
"""

import re
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Case, Client, DocumentType, DocumentTypeField, FieldTemplate, User
from core.services.system.offline import build_client_offline_bundle

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("has_type", [False, True], ids=["missing-type", "inaccessible-type"])
def test_bundle_hides_client_data_without_a_visible_type(facility, client_identified, staff_user, has_type):
    """Client data needs a visible type: x__visible_data_fields__mutmut_1."""
    doc_type = None
    if has_type:
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Restricted profile",
            entity_type=DocumentType.EntityType.CLIENT,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
    Client.objects.filter(pk=client_identified.pk).update(document_type=doc_type, data_json={"secret": "Hidden"})

    with CaptureQueriesContext(connection) as queries:
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)

    assert bundle["client"]["data_fields"] == {}
    assert bundle["client"]["field_metadata"] == []
    assert not any('FROM "core_documenttypefield"' in query["sql"] for query in queries)


def test_missing_staff_flag_excludes_closed_cases_and_roster(
    facility, client_identified, staff_user, case_open, case_closed
):
    """An absent role flag grants no staff bundle data: x_build_client_offline_bundle__mutmut_69."""
    actor = SimpleNamespace(role=User.Role.ASSISTANT, pk=staff_user.pk, is_authenticated=True)

    bundle = build_client_offline_bundle(actor, facility, client_identified)

    assert [item["pk"] for item in bundle["cases"]] == [str(case_open.pk)]
    assert str(case_closed.pk) not in {item["pk"] for item in bundle["cases"]}
    assert bundle["assignable_users"] == []


def test_typed_client_and_cases_need_no_individual_type_fetches(facility, client_identified, staff_user):
    """Related types are available without individual document-type reads.

    x_build_client_offline_bundle__mutmut_2, x_build_client_offline_bundle__mutmut_75.
    """
    expected_cases = {}
    for entity_type in [DocumentType.EntityType.CLIENT, DocumentType.EntityType.CASE]:
        doc_type = DocumentType.objects.create(facility=facility, name=entity_type, entity_type=entity_type)
        field = FieldTemplate.objects.create(
            facility=facility, name=entity_type, slug=f"{entity_type}-detail", field_type="text"
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
        data = {field.slug: f"Stored {entity_type}"}
        if entity_type == DocumentType.EntityType.CLIENT:
            Client.objects.filter(pk=client_identified.pk).update(document_type=doc_type, data_json=data)
        else:
            for index in range(3):
                case = Case.objects.create(
                    facility=facility,
                    client=client_identified,
                    created_by=staff_user,
                    title=f"Typed case {index}",
                    document_type=doc_type,
                    data_json=data,
                )
                expected_cases[str(case.pk)] = data

    with CaptureQueriesContext(connection) as queries:
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)

    assert bundle["client"]["data_fields"] == {"client-detail": "Stored client"}
    assert {item["pk"]: item["data_fields"] for item in bundle["cases"]} == expected_cases
    assert not any(
        re.search(r'FROM "core_documenttype" WHERE "core_documenttype"\."id" =', query["sql"]) for query in queries
    )
