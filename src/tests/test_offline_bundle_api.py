"""Tests for the offline read-bundle API (Refs #574, #572).

Verifies the server-side filters in :mod:`core.services.offline`:
- Role-based event visibility (via ``Event.objects.visible_to``).
- Field-level sensitivity filtering (via ``user_can_see_field``).
- Rate-limiting on the HTTP endpoint.
- Notes visibility (only for Staff+).
- Facility scoping (cross-tenant 404).
- Audit logging of every bundle fetch.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.offline import BUNDLE_SCHEMA_VERSION, build_client_offline_bundle


@pytest.fixture
def doc_type_high(facility):
    dt = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Hochsensibel",
    )
    ft_secret = FieldTemplate.objects.create(
        facility=facility,
        name="GeheimFeld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_secret, sort_order=0)
    return dt


@pytest.fixture
def doc_type_normal_with_high_field(facility):
    """NORMAL document type but with a HIGH-sensitivity field override."""
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
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_normal, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_hi, sort_order=1)
    return dt


@pytest.mark.django_db
class TestBuildClientOfflineBundleService:
    """Service-level invariants independent of the HTTP layer."""

    def test_bundle_has_metadata(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION
        assert "generated_at" in bundle
        assert bundle["ttl"] == 48 * 3600
        assert bundle["client"]["pk"] == str(client_identified.pk)

    def test_bundle_contains_only_visible_events(
        self, facility, client_identified, doc_type_contact, doc_type_high, staff_user
    ):
        # Event accessible to staff (NORMAL doc type)
        visible_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "ok"},
            created_by=staff_user,
        )
        # Event locked away for staff (HIGH doc type)
        hidden_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={"geheimfeld": "super-secret"},
            created_by=staff_user,
        )

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        event_pks = {e["pk"] for e in bundle["events"]}
        assert str(visible_event.pk) in event_pks
        assert str(hidden_event.pk) not in event_pks

    def test_bundle_fields_filtered_by_field_sensitivity(
        self, facility, client_identified, doc_type_normal_with_high_field, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_normal_with_high_field,
            occurred_at=timezone.now(),
            data_json={"bemerkung": "sichtbar", "risiko": "muss-nicht-sichtbar-sein"},
            created_by=staff_user,
        )

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert len(bundle["events"]) == 1
        fields = bundle["events"][0]["data_fields"]
        # staff sees ELEVATED max → HIGH-override field must be dropped
        assert "bemerkung" in fields
        assert fields["bemerkung"] == "sichtbar"
        assert "risiko" not in fields

    def test_lead_sees_high_field_that_staff_cannot(
        self, facility, client_identified, doc_type_normal_with_high_field, lead_user, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_normal_with_high_field,
            occurred_at=timezone.now(),
            data_json={"bemerkung": "sichtbar", "risiko": "muss-lead-sehen"},
            created_by=staff_user,
        )
        staff_bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        lead_bundle = build_client_offline_bundle(lead_user, facility, client_identified)

        assert "risiko" not in staff_bundle["events"][0]["data_fields"]
        assert lead_bundle["events"][0]["data_fields"]["risiko"] == "muss-lead-sehen"

    def test_assistant_cannot_see_notes(self, facility, client_identified, assistant_user):
        client_identified.notes = "interne notiz"
        client_identified.save(update_fields=["notes"])

        bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        assert bundle["client"]["notes"] == ""

    def test_staff_sees_notes(self, facility, client_identified, staff_user):
        client_identified.notes = "interne notiz"
        client_identified.save(update_fields=["notes"])

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["notes"] == "interne notiz"

    def test_bundle_event_limit_respected(self, facility, client_identified, doc_type_contact, staff_user):
        from core.services.offline import MAX_EVENTS_PER_BUNDLE

        # Create 5 events more than the cap to verify truncation.
        for i in range(MAX_EVENTS_PER_BUNDLE + 5):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={"dauer": i, "notiz": f"#{i}"},
                created_by=staff_user,
            )

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert len(bundle["events"]) == MAX_EVENTS_PER_BUNDLE

    def test_bundle_includes_referenced_document_types(self, facility, client_identified, doc_type_contact, staff_user):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10, "notiz": "hi"},
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        dt_pks = {dt["pk"] for dt in bundle["document_types"]}
        assert str(doc_type_contact.pk) in dt_pks

    def test_bundle_normalizes_stage_b_files_marker(self, facility, client_identified, doc_type_contact, staff_user):
        """Refs #786 (C-18): Stage-B-Multifile (`__files__`) muss zu einem
        sicheren Marker minimiert werden — keine internen Attachment-IDs,
        keine Sortier-Indizes im Offline-Bundle.
        """
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={
                "dauer": 5,
                "notiz": "with attachments",
                # Simuliert Stage-B-Marker: 3 Eintraege mit internen IDs
                "anhang": {
                    "__files__": True,
                    "entries": [
                        {"id": "11111111-1111-1111-1111-111111111111", "sort": 0},
                        {"id": "22222222-2222-2222-2222-222222222222", "sort": 1},
                        {"id": "33333333-3333-3333-3333-333333333333", "sort": 2},
                    ],
                },
            },
            created_by=staff_user,
        )

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        ev = bundle["events"][0]
        marker = ev["data_fields"].get("anhang")
        # Marker existiert aber enthaelt KEINE entries-Liste oder IDs.
        assert marker is not None
        assert marker.get("__files__") is True
        assert marker.get("count") == 3
        assert "entries" not in marker, (
            f"Stage-B-Marker im Offline-Bundle leakt entries (interne Attachment-IDs): {marker}"
        )
        # Defensive: das gesamte Bundle als JSON serialisieren und sicherstellen,
        # dass keine der drei UUIDs auftaucht.
        import json

        body = json.dumps(bundle)
        for uid in (
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
        ):
            assert uid not in body, f"Attachment-UUID {uid} darf nicht im Offline-Bundle stehen."

    def test_bundle_reports_field_metadata(
        self, facility, client_identified, doc_type_normal_with_high_field, lead_user, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_normal_with_high_field,
            occurred_at=timezone.now(),
            data_json={"bemerkung": "x", "risiko": "y"},
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(lead_user, facility, client_identified)
        dts = [dt for dt in bundle["document_types"] if dt["pk"] == str(doc_type_normal_with_high_field.pk)]
        assert len(dts) == 1
        field_sens = {f["slug"]: f["sensitivity"] for f in dts[0]["fields"]}
        assert field_sens["risiko"] == "high"


@pytest.mark.django_db
class TestOfflineClientBundleView:
    """HTTP contract of ``GET /api/offline/bundle/client/<uuid>/``."""

    def _url(self, client_pk):
        return reverse("core:offline_bundle", kwargs={"pk": client_pk})

    def test_requires_login(self, client, client_identified):
        response = client.get(self._url(client_identified.pk))
        assert response.status_code in (302, 403)

    def test_returns_bundle_for_own_facility(self, client, client_identified, staff_user):
        client.force_login(staff_user)
        response = client.get(self._url(client_identified.pk))
        assert response.status_code == 200
        payload = response.json()
        assert payload["client"]["pk"] == str(client_identified.pk)

    def test_cross_facility_is_404(self, client, client_identified, second_facility_user):
        client.force_login(second_facility_user)
        response = client.get(self._url(client_identified.pk))
        assert response.status_code == 404

    def test_audit_log_created(self, client, client_identified, staff_user):
        client.force_login(staff_user)
        before = AuditLog.objects.filter(
            action=AuditLog.Action.EXPORT,
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        client.get(self._url(client_identified.pk))
        after = AuditLog.objects.filter(
            action=AuditLog.Action.EXPORT,
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        assert after == before + 1

    def test_rate_limit_decorator_configured(self):
        """The view must be decorated with ``ratelimit`` (30/h/user) so that
        the production settings (``RATELIMIT_ENABLE = True``) apply the cap.

        Tests run with ``RATELIMIT_ENABLE = False`` so we cannot assert a 429
        at runtime; we assert the decorator is present instead.
        """
        from core.views.offline import OfflineClientBundleView

        get = OfflineClientBundleView.get
        # method_decorator copies attributes onto the wrapper — the presence
        # of a ``__wrapped__`` chain signals decoration happened.
        assert hasattr(get, "__wrapped__"), "get() should be wrapped by ratelimit"

    def test_rate_limited_after_30_requests(self, client, client_identified, staff_user):
        """With rate-limiting forcibly enabled, the 31st request per hour is blocked
        (django-ratelimit's default ``block=True`` responds with 403).
        """
        cache.clear()
        client.force_login(staff_user)
        url = self._url(client_identified.pk)
        with override_settings(RATELIMIT_ENABLE=True):
            for _ in range(30):
                response = client.get(url)
                assert response.status_code == 200
            response = client.get(url)
            assert response.status_code == 403
        cache.clear()

    def test_only_get_allowed(self, client, client_identified, staff_user):
        client.force_login(staff_user)
        response = client.post(self._url(client_identified.pk))
        assert response.status_code == 405

    def test_high_sensitivity_event_not_in_bundle_for_staff(self, client, client_identified, doc_type_high, staff_user):
        Event.objects.create(
            facility=staff_user.facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={"geheimfeld": "secret"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(self._url(client_identified.pk))
        assert response.status_code == 200
        events = response.json()["events"]
        # Staff must not see HIGH events
        assert events == []


@pytest.mark.django_db
class TestOfflineClientDetailShellView:
    """The HTML scaffold under ``/offline/clients/<uuid>/`` (rendered by JS)."""

    def _url(self, pk):
        return reverse("core:offline_client_detail", kwargs={"pk": pk})

    def test_requires_login(self, client):
        response = client.get(self._url("00000000-0000-0000-0000-000000000000"))
        assert response.status_code in (302, 403)

    def test_renders_scaffold(self, client, staff_user):
        """Scaffold should render regardless of whether the client exists —
        the JS tries to pull from IndexedDB. This is intentional so the SW
        redirect always lands on a usable shell.
        """
        client.force_login(staff_user)
        response = client.get(self._url("00000000-0000-0000-0000-000000000000"))
        assert response.status_code == 200
        assert b"offline-client-view" in response.content


@pytest.mark.django_db
class TestClientPkRenderedAsBareUuid:
    """Regression: ``data-pk`` must contain the literal UUID, not ``\\u002D``-
    escaped hyphens. ``escapejs`` is for inline ``<script>`` strings; in HTML
    attributes the browser reads the escape sequence verbatim, so JS appends
    a malformed UUID to ``/api/offline/bundle/client/...`` → 404.
    """

    def _bare_pk_html(self, pk):
        return f'data-pk="{pk}"'.encode()

    def test_client_list_renders_bare_uuid(self, client, client_identified, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"))
        assert response.status_code == 200
        assert self._bare_pk_html(client_identified.pk) in response.content
        assert b"\\u002D" not in response.content

    def test_client_detail_renders_bare_uuid(self, client, client_identified, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        assert response.status_code == 200
        assert self._bare_pk_html(client_identified.pk) in response.content
        assert b"\\u002D" not in response.content

    def test_offline_detail_shell_renders_bare_uuid(self, client, staff_user):
        pk = "6b70767f-9143-43a9-8908-feccc4a94a9f"
        client.force_login(staff_user)
        response = client.get(reverse("core:offline_client_detail", kwargs={"pk": pk}))
        assert response.status_code == 200
        assert self._bare_pk_html(pk) in response.content
        assert b"\\u002D" not in response.content

    def test_conflict_review_renders_bare_uuid(self, client, staff_user):
        pk = "6b70767f-9143-43a9-8908-feccc4a94a9f"
        client.force_login(staff_user)
        response = client.get(reverse("core:offline_conflict_review", kwargs={"pk": pk}))
        assert response.status_code == 200
        assert f'data-event-pk="{pk}"'.encode() in response.content
        assert b"\\u002D" not in response.content
