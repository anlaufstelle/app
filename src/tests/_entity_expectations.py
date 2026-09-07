"""Deklarative Soll-Tabelle fuer die Dynamic-UI-Tests von Fall und Person.

``picker_excluded_fixtures`` ist bewusst asymmetrisch: Der Basis-Client-Test
schloss keinen Falltyp-Fixture aus, der Basis-Case-Test den Personenprofil-Typ
hingegen explizit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from django.db import connection

from core.models import Case, Client
from core.services.client import _gather_cases, _gather_client_fields

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest
    from django.db.models import Model


IdentityKwargsBuilder = Callable[["FixtureRequest", str], dict[str, Any]]
PostDataBuilder = Callable[["FixtureRequest", str, dict[str, Any]], dict[str, Any]]
DynamicDataAccessor = Callable[["Model"], list[dict[str, Any]]]
EntityRecordAccessor = Callable[["Model"], dict[str, Any]]
RawDataAccessor = Callable[["Model"], dict[str, Any]]
InvalidIdentityBuilder = Callable[["FixtureRequest"], str]
ExportOwnerAccessor = Callable[["Model"], "Client"]
ExportEntryAccessor = Callable[[dict[str, Any]], dict[str, Any]]


def _case_identity_kwargs(request: FixtureRequest, identity: str) -> dict[str, Any]:
    return {
        "client": request.getfixturevalue("client_identified"),
        "title": identity,
    }


def _client_identity_kwargs(request: FixtureRequest, identity: str) -> dict[str, Any]:
    del request
    return {"pseudonym": identity}


def _blank_identity(request: FixtureRequest) -> str:
    del request
    return ""


def _duplicate_client_identity(request: FixtureRequest) -> str:
    return request.getfixturevalue("client_identified").pseudonym


def _case_post_data(request: FixtureRequest, identity: str, extra: dict[str, Any]) -> dict[str, Any]:
    data = {
        "title": identity,
        "client": str(request.getfixturevalue("client_identified").pk),
        "description": "",
        "lead_user": "",
    }
    data.update(extra)
    return data


def _client_post_data(request: FixtureRequest, identity: str, extra: dict[str, Any]) -> dict[str, Any]:
    del request
    data = {
        "pseudonym": identity,
        "contact_stage": Client.ContactStage.IDENTIFIED,
        "age_cluster": Client.AgeCluster.UNKNOWN,
        "notes": "",
    }
    data.update(extra)
    return data


def _gather_case_dynamic_data(obj: Model) -> list[dict[str, Any]]:
    return _gather_cases(obj.client)[0]["dynamic_data"]


def _gather_client_dynamic_data(obj: Model) -> list[dict[str, Any]]:
    return _gather_client_fields(obj)["dynamic_data"]


def _gather_case_record(obj: Model) -> dict[str, Any]:
    return _gather_cases(obj.client)[0]


def _gather_client_record(obj: Model) -> dict[str, Any]:
    return _gather_client_fields(obj)


def _case_export_owner(obj: Model) -> Client:
    return obj.client


def _client_export_owner(obj: Model) -> Client:
    return obj


def _case_export_entry(data: dict[str, Any]) -> dict[str, Any]:
    return data["cases"][0]


def _client_export_entry(data: dict[str, Any]) -> dict[str, Any]:
    return data["client"]


def _raw_case_data_json(obj: Model) -> dict[str, Any]:
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_case WHERE id = %s", [str(obj.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def _raw_client_data_json(obj: Model) -> dict[str, Any]:
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_client WHERE id = %s", [str(obj.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@dataclass(frozen=True)
class EntitySpec:
    """Alle strukturellen und fachsprachlichen Unterschiede einer Entitaet."""

    name: str
    model: type[Model]
    entity_type: str
    url_names: tuple[tuple[str, str], ...]
    identity_field: str
    identity_kwargs_builder: IdentityKwargsBuilder
    invalid_identity_builder: InvalidIdentityBuilder
    post_data_builder: PostDataBuilder
    gather_dynamic_data: DynamicDataAccessor
    gather_entity_record: EntityRecordAccessor
    raw_data_json: RawDataAccessor
    export_owner: ExportOwnerAccessor
    export_entry: ExportEntryAccessor
    export_base_keys: tuple[str, ...]
    doctype_fixtures: tuple[tuple[str, str], ...]
    fixture_names: tuple[tuple[str, str], ...]
    field_names: tuple[tuple[str, str], ...]
    labels: tuple[tuple[str, str], ...]
    values: tuple[tuple[str, str], ...]
    picker_excluded_fixtures: tuple[str, ...]
    id_prefix: str
    update_service_path: str
    attachment_relation: str

    @staticmethod
    def _lookup(entries: tuple[tuple[str, str], ...], key: str) -> str:
        return dict(entries)[key]

    def url(self, action: str) -> str:
        return self._lookup(self.url_names, action)

    def fixture(self, request: FixtureRequest, key: str) -> Any:
        return request.getfixturevalue(self._lookup(self.fixture_names, key))

    def doctype(self, request: FixtureRequest, variant: str) -> Any:
        return request.getfixturevalue(self._lookup(self.doctype_fixtures, variant))

    def field(self, key: str) -> str:
        return self._lookup(self.field_names, key)

    def label(self, key: str) -> str:
        return self._lookup(self.labels, key)

    def value(self, key: str) -> str:
        return self._lookup(self.values, key)

    def identity_kwargs(self, request: FixtureRequest, suffix: str) -> dict[str, Any]:
        return self.identity_kwargs_builder(request, f"{self.id_prefix}{suffix}")

    def post_data(self, request: FixtureRequest, suffix: str, **extra: Any) -> dict[str, Any]:
        return self.post_data_builder(request, f"{self.id_prefix}{suffix}", extra)


CASE = EntitySpec(
    name="case",
    model=Case,
    entity_type="case",
    url_names=(
        ("create", "core:case_create"),
        ("update", "core:case_update"),
        ("detail", "core:case_detail"),
        ("fields_partial", "core:case_fields_partial"),
    ),
    identity_field="title",
    identity_kwargs_builder=_case_identity_kwargs,
    invalid_identity_builder=_blank_identity,
    post_data_builder=_case_post_data,
    gather_dynamic_data=_gather_case_dynamic_data,
    gather_entity_record=_gather_case_record,
    raw_data_json=_raw_case_data_json,
    export_owner=_case_export_owner,
    export_entry=_case_export_entry,
    export_base_keys=(
        "title",
        "description",
        "status",
        "created_at",
        "updated_at",
        "closed_at",
        "lead_user",
        "created_by",
        "is_deleted",
        "deleted_at",
        "deleted_by",
        "anonymized_at",
    ),
    doctype_fixtures=(
        ("form", "doc_type_case_form"),
        ("high", "doc_type_case_high"),
        ("mixed", "doc_type_case_mixed"),
        ("select", "doc_type_case_select"),
        ("conditional", "doc_type_case_conditional"),
        ("file_condition", "doc_type_case_file_condition"),
    ),
    fixture_names=(
        ("foreign_doctype", "foreign_doc_type_case"),
        ("object", "case_open"),
        ("foreign_object", "foreign_case"),
    ),
    field_names=(
        ("primary", "anlass"),
        ("secondary", "verlauf"),
        ("high", "gefahrenlage"),
        ("mixed_visible", "zielsetzung"),
        ("mixed_restricted", "risikoeinschaetzung"),
        ("select", "hilfeform"),
        ("boolean", "kostenzusage"),
        ("conditional_target", "betreuungsdetails"),
        ("file_source", "kostenbescheid"),
        ("file_target", "bescheid-erlaeuterung"),
        ("meta", "description"),
    ),
    labels=(
        ("primary", "Anlass"),
        ("secondary", "Verlauf"),
        ("high", "Gefahrenlage"),
        ("mixed_visible", "Zielsetzung"),
        ("mixed_restricted", "Risikoeinschaetzung"),
        ("select", "Hilfeform"),
        ("boolean", "Kostenzusage"),
    ),
    values=(
        ("primary", "Mietschulden"),
        ("secondary", "Sehr sensibel"),
        ("confirm_primary", "A"),
        ("confirm_secondary", "B"),
        ("high_secret", "geheim"),
        ("high_confidential", "streng vertraulich"),
        ("discarded", "wird verworfen"),
        ("rerender_primary", "Anlass-Wert"),
        ("prefill_primary", "Mietschulden Q3"),
        ("update_primary", "Wohnungsverlust"),
        ("mixed_initial", "Wohnung"),
        ("mixed_updated", "Arbeit"),
        ("historical_identity", "Historische Zahl"),
        ("historical_new_identity", "Neuer Titel"),
        ("range_doctype_name", "Fall-Zahlengrenze"),
        ("visible_error_identity", "Sichtbarfehler"),
        ("visible_error_new_identity", "Sichtbar-Neu"),
        ("visible_error_doctype_name", "Fall-Sichtbarfehler"),
        ("lock_identity", "Lock-Typ-01"),
        ("meta_updated", "Neue Beschreibung"),
        ("select", "ambulant"),
        ("select_label", "Ambulant"),
    ),
    picker_excluded_fixtures=("doc_type_client_profile", "doc_type_contact"),
    id_prefix="P8-",
    update_service_path="core.views.cases.update_case",
    attachment_relation="case",
)


CLIENT = EntitySpec(
    name="client",
    model=Client,
    entity_type="client",
    url_names=(
        ("create", "core:client_create"),
        ("update", "core:client_update"),
        ("detail", "core:client_detail"),
        ("fields_partial", "core:client_fields_partial"),
    ),
    identity_field="pseudonym",
    identity_kwargs_builder=_client_identity_kwargs,
    invalid_identity_builder=_duplicate_client_identity,
    post_data_builder=_client_post_data,
    gather_dynamic_data=_gather_client_dynamic_data,
    gather_entity_record=_gather_client_record,
    raw_data_json=_raw_client_data_json,
    export_owner=_client_export_owner,
    export_entry=_client_export_entry,
    export_base_keys=(
        "pseudonym",
        "contact_stage",
        "age_cluster",
        "notes",
        "is_active",
        "k_anonymized",
        "created_at",
        "updated_at",
        "created_by",
        "is_deleted",
        "deleted_at",
        "deleted_by",
    ),
    doctype_fixtures=(
        ("form", "doc_type_client_profile"),
        ("high", "doc_type_client_high"),
        ("mixed", "doc_type_client_mixed"),
        ("select", "doc_type_client_select"),
        ("conditional", "doc_type_client_conditional"),
        ("file_condition", "doc_type_client_file_condition"),
    ),
    fixture_names=(
        ("foreign_doctype", "foreign_doc_type_client"),
        ("object", "client_identified"),
        ("foreign_object", "foreign_client"),
    ),
    field_names=(
        ("primary", "wohnsituation"),
        ("secondary", "diagnose"),
        ("high", "schutzkonzept"),
        ("mixed_visible", "sprache"),
        ("mixed_restricted", "gefaehrdung"),
        ("select", "wohnform"),
        ("boolean", "einwilligung"),
        ("conditional_target", "betreuungsdetails"),
        ("file_source", "nachweis"),
        ("file_target", "nachweis-erlaeuterung"),
        ("meta", "notes"),
    ),
    labels=(
        ("primary", "Wohnsituation"),
        ("secondary", "Diagnose"),
        ("high", "Schutzkonzept"),
        ("mixed_visible", "Sprache"),
        ("mixed_restricted", "Gefaehrdung"),
        ("select", "Wohnform"),
        ("boolean", "Einwilligung"),
    ),
    values=(
        ("primary", "WG"),
        ("secondary", "Sehr sensibel"),
        ("confirm_primary", "WG"),
        ("confirm_secondary", "Text"),
        ("high_secret", "geheim"),
        ("high_confidential", "streng vertraulich"),
        ("discarded", "wird verworfen"),
        ("rerender_primary", "WG-Wert"),
        ("prefill_primary", "WG im Nordviertel"),
        ("update_primary", "Eigene Wohnung"),
        ("mixed_initial", "DE"),
        ("mixed_updated", "EN"),
        ("historical_identity", "Historische-Zahl"),
        ("historical_new_identity", "Neuer-Pseudonym"),
        ("range_doctype_name", "Personen-Zahlengrenze"),
        ("visible_error_identity", "Sichtbarfehler"),
        ("visible_error_new_identity", "Sichtbar-Neu"),
        ("visible_error_doctype_name", "Personen-Sichtbarfehler"),
        ("lock_identity", "Lock-01"),
        ("meta_updated", "Neue Notiz"),
        ("select", "wohnungslos"),
        ("select_label", "Wohnungslos"),
    ),
    picker_excluded_fixtures=("doc_type_contact",),
    id_prefix="P7-",
    update_service_path="core.views.clients.update_client",
    attachment_relation="client",
)


ENTITY_SPECS = (CASE, CLIENT)
ENTITY_PARAMS = tuple(pytest.param(spec, id=spec.name) for spec in ENTITY_SPECS)
