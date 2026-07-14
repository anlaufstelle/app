"""Tests for the person-less facility-meta offline bundle (Refs #1518, #1499).

The facility bundle (``build_facility_offline_bundle`` /
``OfflineFacilityBundleView``) mirrors the client bundle but carries NO
client roster and NO per-person data — it is the offline-create metadata
(DocumentTypes + field schema + assignable users) needed to author events/
work items cold-offline without a previously downloaded client.

Verifies:
- Field-level sensitivity filtering is inherited unchanged from the shared
  ``_serialize_document_type`` (HIGH-field schema never leaves the server).
- ETag/304 revalidation writes no audit trail.
- A 200 read writes exactly one PII-free ``OFFLINE_FACILITY_BUNDLE_READ``.
- The bundle is person-less (no ``client``/``events``/``cases``/``workitems``).
- Every DocumentType carries its ``min_contact_stage`` (soft picker prefilter).
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from core.models import AuditLog, DocumentType, DocumentTypeField, FieldTemplate
from core.services.system import (
    BUNDLE_TTL_SECONDS,
    FACILITY_BUNDLE_SCHEMA_VERSION,
    build_facility_offline_bundle,
)


@pytest.fixture
def doc_type_normal_with_high_field(facility):
    """NORMAL document type with a HIGH-sensitivity field override."""
    dt = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        sensitivity=DocumentType.Sensitivity.NORMAL,
        name="NormalMitHighField",
    )
    ft_normal = FieldTemplate.objects.create(
        facility=facility,
        name="Bemerkung",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_hi = FieldTemplate.objects.create(
        facility=facility,
        name="Risiko",
        field_type=FieldTemplate.FieldType.TEXT,
        sensitivity="high",
        help_text="streng geheimer Hilfetext",
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_normal, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_hi, sort_order=1)
    return dt


@pytest.fixture
def doc_type_qualified_only(facility):
    """Active DocumentType requiring a minimum contact stage of ``qualified``."""
    return DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.NORMAL,
        name="NurQualifiziert",
        min_contact_stage="qualified",
    )


@pytest.fixture
def doc_type_high(facility):
    """Active HIGH-sensitivity DocumentType (only Lead/Admin may create it)."""
    return DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Suizidgefaehrdung-GEHEIM",
    )


@pytest.fixture
def doc_type_elevated(facility):
    """Active ELEVATED-sensitivity DocumentType (Staff+ may create it)."""
    return DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        name="Krisenintervention-ELEVATED",
    )


@pytest.mark.django_db
class TestBuildFacilityOfflineBundleService:
    """Service-level invariants independent of the HTTP layer."""

    def test_bundle_has_metadata(self, facility, staff_user):
        bundle = build_facility_offline_bundle(staff_user, facility)
        assert bundle["schema_version"] == FACILITY_BUNDLE_SCHEMA_VERSION
        assert "generated_at" in bundle
        assert "expires_at" in bundle
        assert bundle["ttl"] == BUNDLE_TTL_SECONDS == 48 * 3600

    def test_bundle_is_personless(self, facility, staff_user, doc_type_contact):
        """Refs #1499: das Facility-Bundle ist personenlos (kein Roster-PII).
        Es darf keinen ``client``-Block und keine personengebundenen Listen
        (events/cases/workitems) tragen — nur Offline-Create-Metadaten."""
        bundle = build_facility_offline_bundle(staff_user, facility)
        assert "client" not in bundle
        assert "events" not in bundle
        assert "cases" not in bundle
        assert "workitems" not in bundle
        assert set(bundle.keys()) == {
            "schema_version",
            "generated_at",
            "ttl",
            "expires_at",
            "document_types",
            "assignable_users",
        }

    def test_bundle_lists_active_document_types(self, facility, staff_user, doc_type_contact):
        inactive = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="Stillgelegt",
            is_active=False,
        )
        bundle = build_facility_offline_bundle(staff_user, facility)
        pks = {dt["pk"] for dt in bundle["document_types"]}
        assert str(doc_type_contact.pk) in pks
        assert str(inactive.pk) not in pks

    def test_bundle_document_types_sorted_by_sort_order_then_name(self, facility, staff_user):
        """Refs #1498 (Regressionsanker): das Facility-Bundle sortiert bereits
        explizit per ``.order_by("sort_order", "name")`` (Z.376) — dieser Test
        haelt das fest, damit der Client-Bundle-Fix (#1498) hier nicht wieder
        auseinanderdriftet."""
        dt_c = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="Charlie",
            sort_order=10,
        )
        dt_a = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="Alpha",
            sort_order=5,
        )
        bundle = build_facility_offline_bundle(staff_user, facility)
        names_in_order = [dt["name"] for dt in bundle["document_types"] if dt["pk"] in {str(dt_a.pk), str(dt_c.pk)}]
        assert names_in_order == ["Alpha", "Charlie"]

    def test_high_field_schema_does_not_leak_to_staff(
        self, facility, staff_user, lead_user, doc_type_normal_with_high_field
    ):
        """Risk 5 (Bundle-ist-Derivat-Invariante): das HIGH-Feld (Schema,
        Hilfetext, Optionen) darf fuer Staff (max ELEVATED) das Facility-Bundle
        NICHT erreichen — derselbe ``user_can_see_field``-Filter wie im
        Klient-Bundle. Lead (sieht HIGH) als Gegenprobe."""
        staff_bundle = build_facility_offline_bundle(staff_user, facility)
        lead_bundle = build_facility_offline_bundle(lead_user, facility)
        staff_dt = next(d for d in staff_bundle["document_types"] if d["pk"] == str(doc_type_normal_with_high_field.pk))
        lead_dt = next(d for d in lead_bundle["document_types"] if d["pk"] == str(doc_type_normal_with_high_field.pk))
        staff_slugs = {f["slug"] for f in staff_dt["fields"]}
        lead_slugs = {f["slug"] for f in lead_dt["fields"]}
        assert "bemerkung" in staff_slugs
        assert "risiko" not in staff_slugs, "HIGH-Feld-Definition darf nicht zu Staff gelangen"
        assert {"bemerkung", "risiko"} <= lead_slugs
        # Auch kein Rest-Leak (Hilfetext/Optionen) im gesamten Staff-Bundle-JSON.
        assert "streng geheimer Hilfetext" not in json.dumps(staff_bundle, default=str)

    def test_min_contact_stage_present_per_doctype(
        self, facility, staff_user, doc_type_contact, doc_type_qualified_only
    ):
        """Refs #1518: jeder DocType traegt ``min_contact_stage`` (weicher
        Picker-Vorfilter fuer „ohne Person")."""
        bundle = build_facility_offline_bundle(staff_user, facility)
        stages = {dt["pk"]: dt["min_contact_stage"] for dt in bundle["document_types"]}
        # DocType ohne Mindeststufe → leerer String (JSON-clean, wie icon/color).
        assert stages[str(doc_type_contact.pk)] == ""
        assert stages[str(doc_type_qualified_only.pk)] == "qualified"
        # Der Key MUSS auf jedem DocType existieren.
        for dt in bundle["document_types"]:
            assert "min_contact_stage" in dt

    def test_assignable_users_present_for_staff(self, facility, staff_user, lead_user, assistant_user):
        bundle = build_facility_offline_bundle(staff_user, facility)
        pks = {u["pk"] for u in bundle["assignable_users"]}
        assert str(staff_user.pk) in pks
        assert str(lead_user.pk) in pks
        assert str(assistant_user.pk) in pks

    def test_assignable_users_empty_for_assistant(self, facility, assistant_user):
        """Spiegelt den Klient-Builder: Assistenz erhaelt kein Roster (nur Staff+
        legt WorkItems an/weist zu). SI-5 haengt seinen WorkItem-Shell-Gate
        (``canCreateWorkItem``) an eine nicht-leere Liste."""
        bundle = build_facility_offline_bundle(assistant_user, facility)
        assert bundle["assignable_users"] == []


@pytest.mark.django_db
class TestFacilityBundleDocTypeSensitivityFilter:
    """Refs #1518 (Review MEDIUM): der DocType-Katalog des Facility-Bundles muss
    denselben DocType-EBENEN-Sensitivity-Filter durchlaufen wie das Online-
    ``EventMetaForm`` (``sensitivity__in=allowed_sensitivities_for_user``,
    ``forms/events.py``). Sonst leakt der NAME/die Metadaten eines HIGH/ELEVATED-
    DocType offline an niedrigere Rollen und wuerden in SI-4s Create-Picker
    auftauchen — Verstoss gegen die Bundle-ist-Derivat-Invariante. Das Bundle
    darf also nur die *erstellbaren* Typen tragen (Derivat des Online-Create-
    Formulars), nicht bloss die feld-gefilterten.

    Rollen-Rang (ROLE_MAX_SENSITIVITY): Assistant=NORMAL, Staff=ELEVATED,
    Lead/Admin=HIGH.
    """

    @staticmethod
    def _dt_pks(bundle):
        return {dt["pk"] for dt in bundle["document_types"]}

    def test_high_doctype_absent_for_assistant(self, facility, assistant_user, doc_type_high):
        """Assistant (max NORMAL) darf einen HIGH-DocType weder als Eintrag noch
        als Namens-Leak irgendwo im Bundle-JSON sehen."""
        bundle = build_facility_offline_bundle(assistant_user, facility)
        assert str(doc_type_high.pk) not in self._dt_pks(bundle)
        assert doc_type_high.name not in json.dumps(bundle, default=str)

    def test_elevated_doctype_absent_for_assistant(self, facility, assistant_user, doc_type_elevated):
        """Assistant (max NORMAL) darf auch einen ELEVATED-DocType nicht sehen."""
        bundle = build_facility_offline_bundle(assistant_user, facility)
        assert str(doc_type_elevated.pk) not in self._dt_pks(bundle)
        assert doc_type_elevated.name not in json.dumps(bundle, default=str)

    def test_high_doctype_absent_for_staff(self, facility, staff_user, doc_type_high):
        """Staff (max ELEVATED) darf einen HIGH-DocType nicht sehen."""
        bundle = build_facility_offline_bundle(staff_user, facility)
        assert str(doc_type_high.pk) not in self._dt_pks(bundle)
        assert doc_type_high.name not in json.dumps(bundle, default=str)

    def test_elevated_doctype_present_for_staff(self, facility, staff_user, doc_type_elevated):
        """Staff (max ELEVATED) darf einen ELEVATED-DocType erstellen ⇒ er MUSS
        im Bundle sein (Gegenprobe: der Filter darf nicht zu scharf sein)."""
        bundle = build_facility_offline_bundle(staff_user, facility)
        assert str(doc_type_elevated.pk) in self._dt_pks(bundle)

    def test_high_and_elevated_doctypes_present_for_lead(self, facility, lead_user, doc_type_high, doc_type_elevated):
        """Lead (max HIGH) sieht beide berechtigten Typen — der Filter blendet
        nur oberhalb des Rollen-Rangs aus."""
        bundle = build_facility_offline_bundle(lead_user, facility)
        pks = self._dt_pks(bundle)
        assert str(doc_type_high.pk) in pks
        assert str(doc_type_elevated.pk) in pks


@pytest.mark.django_db
class TestOfflineFacilityBundleView:
    """HTTP contract of ``GET /api/v1/offline/bundle/facility/``."""

    def _url(self):
        return reverse("core:offline_facility_bundle")

    def test_requires_login(self, client):
        response = client.get(self._url())
        assert response.status_code in (302, 403)

    def test_returns_bundle_for_own_facility(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(self._url())
        assert response.status_code == 200
        payload = response.json()
        assert "client" not in payload
        assert payload["schema_version"] == FACILITY_BUNDLE_SCHEMA_VERSION
        dt_pks = {dt["pk"] for dt in payload["document_types"]}
        assert str(doc_type_contact.pk) in dt_pks

    def test_only_get_allowed(self, client, staff_user):
        client.force_login(staff_user)
        response = client.post(self._url())
        assert response.status_code == 405

    def test_response_carries_etag_header(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(self._url())
        etag = response.headers.get("ETag")
        assert etag and etag.startswith('"') and etag.endswith('"')

    def test_response_carries_explicit_no_store_header(self, client, staff_user, settings):
        """Refs #1342: explizites no-store DIREKT in der View, redundant zur
        Blanket-Middleware. Middleware bewusst aus dem Stack entfernt, damit
        der Test wirklich den View-eigenen Header prueft."""
        settings.MIDDLEWARE = [
            m for m in settings.MIDDLEWARE if m != "core.middleware.no_store_cache.NoStoreCacheMiddleware"
        ]
        client.force_login(staff_user)
        response = client.get(self._url())
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store, private"

    def test_audit_log_created_pii_free(self, client, staff_user, facility, doc_type_contact):
        """Refs #1518: ein 200-Read schreibt genau EINEN
        ``OFFLINE_FACILITY_BUNDLE_READ`` — PII-frei (personenlos, kein
        Pseudonym), Detail traegt nur Zaehl-/Schema-Metadaten. Bewusst KEIN
        ``EXPORT`` (Breach-Heuristik)."""
        client.force_login(staff_user)
        read_before = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_FACILITY_BUNDLE_READ).count()
        export_before = AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count()

        client.get(self._url())

        reads = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_FACILITY_BUNDLE_READ)
        assert reads.count() == read_before + 1
        assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == export_before
        entry = reads.latest("timestamp")
        # PII-frei: kein Pseudonym-Feld, nur Zaehl-/Schema-Metadaten.
        assert set(entry.detail.keys()) <= {"event", "document_type_count", "schema_version"}
        assert "pseudonym" not in entry.detail
        assert entry.detail["schema_version"] == FACILITY_BUNDLE_SCHEMA_VERSION

    def test_matching_if_none_match_returns_304_without_audit(self, client, staff_user):
        """Ein bedingter GET mit passendem If-None-Match liefert 304 (kein Body)
        und schreibt KEINEN Audit-Eintrag."""
        client.force_login(staff_user)
        etag = client.get(self._url()).headers["ETag"]

        audit_before = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_FACILITY_BUNDLE_READ).count()
        response = client.get(self._url(), HTTP_IF_NONE_MATCH=etag)
        assert response.status_code == 304
        assert response.headers["ETag"] == etag
        assert response.content == b""
        audit_after = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_FACILITY_BUNDLE_READ).count()
        assert audit_after == audit_before, "304 darf keine Audit-Spur schreiben"

    def test_rate_limit_decorator_configured(self):
        from core.constants import RATELIMIT_OFFLINE_BUNDLE
        from core.views.offline import OfflineFacilityBundleView

        get = OfflineFacilityBundleView.get
        assert hasattr(get, "__wrapped__"), "get() should be wrapped by ratelimit"
        assert RATELIMIT_OFFLINE_BUNDLE == "120/h"
