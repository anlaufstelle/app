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

from core.models import AuditLog, Case, DocumentType, DocumentTypeField, Event, FieldTemplate, User, WorkItem
from core.services.system import BUNDLE_SCHEMA_VERSION, build_client_offline_bundle


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

    def test_assistant_case_bundle_excludes_closed_and_blanks_open_description(
        self, facility, client_identified, assistant_user, staff_user
    ):
        """Refs #1355: Online sieht eine Assistenz Cases nur ueber den
        Client-Detail (nur ``status=OPEN``, nie ``description`` —
        ``clients.py``/``detail.html``); ``CaseListView``/``CaseDetailView``
        (mit description) sind STAFF_PLUS (``cases.py:70``). Das Offline-
        Bundle darf laut ADR-022 nie mehr zeigen als online: der CLOSED-Case
        muss fehlen, der OPEN-Case darf keine description tragen (leerer
        String, nicht fehlender Key — Schema-Stabilitaet)."""
        open_case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Offener Fall",
            description="Sensible Fallbeschreibung",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        closed_case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Geschlossener Fall",
            description="Sollte offline nicht landen",
            status=Case.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )

        bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        pks = {c["pk"] for c in bundle["cases"]}
        assert str(open_case.pk) in pks
        assert str(closed_case.pk) not in pks
        serialized_open = next(c for c in bundle["cases"] if c["pk"] == str(open_case.pk))
        assert serialized_open["description"] == ""
        assert "description" in serialized_open

    def test_staff_case_bundle_includes_closed_with_description(self, facility, client_identified, staff_user):
        """Gegenprobe: Staff+ sieht online via CaseListView/CaseDetailView
        alle Cases inkl. description — das Bundle bleibt fuer diese Rolle
        unveraendert vollstaendig."""
        open_case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Offener Fall",
            description="Offene Beschreibung",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        closed_case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Geschlossener Fall",
            description="Geschlossene Beschreibung",
            status=Case.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {c["pk"] for c in bundle["cases"]}
        assert pks == {str(open_case.pk), str(closed_case.pk)}
        descriptions = {c["pk"]: c["description"] for c in bundle["cases"]}
        assert descriptions[str(open_case.pk)] == "Offene Beschreibung"
        assert descriptions[str(closed_case.pk)] == "Geschlossene Beschreibung"

    def test_bundle_event_limit_respected(self, facility, client_identified, doc_type_contact, staff_user):
        from core.services.system import MAX_EVENTS_PER_BUNDLE

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

    def test_bundle_includes_all_active_document_types_even_if_unused(self, facility, client_identified, staff_user):
        """Refs #1397: Offline-Create muss denselben Katalog wie online anbieten
        (EventMetaForm: alle aktiven Facility-Doctypes), nicht nur die von den
        Events der Person bereits genutzten — sonst ist ein nie genutzter Typ
        offline nicht anlegbar. Die Person hat hier gar keine Events."""
        unused = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="NieGenutzt",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        entry = next((dt for dt in bundle["document_types"] if dt["pk"] == str(unused.pk)), None)
        assert entry is not None, "Aktiver Doctype muss im Bundle stehen (Offline-Create-Katalog)."
        assert entry["is_active"] is True

    def test_bundle_excludes_inactive_unreferenced_document_type(self, facility, client_identified, staff_user):
        """Refs #1397: spiegelt online ``is_active=True`` — ein stillgelegter Typ,
        den kein Event nutzt, wird nicht zur Offline-Erfassung angeboten."""
        inactive = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="Stillgelegt",
            is_active=False,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        dt_pks = {dt["pk"] for dt in bundle["document_types"]}
        assert str(inactive.pk) not in dt_pks

    def test_bundle_keeps_referenced_but_inactive_document_type(self, facility, client_identified, staff_user):
        """Refs #1397: ein Event auf einem seither stillgelegten Typ muss dessen
        Schema weiter mitschicken, damit der vorhandene Wert offline rendert
        (Vereinigung: aktiver Katalog ∪ referenzierte Typen)."""
        dt = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.CONTACT,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="AlterTyp",
            is_active=False,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        entry = next((d for d in bundle["document_types"] if d["pk"] == str(dt.pk)), None)
        assert entry is not None, "Referenzierter (auch inaktiver) Typ muss zum Rendern im Bundle bleiben."
        # Refs #1397: Das is_active-Flag trägt den Render-only-Zustand, damit der
        # Offline-Create-Dropdown (documentTypeOptions) inaktive Typen ausschließt.
        assert entry["is_active"] is False

    def test_bundle_event_carries_updated_at_token(self, facility, client_identified, doc_type_contact, staff_user):
        """F-07 (Refs #1109): Jedes serialisierte Event muss seinen
        ``updated_at``-Optimistic-Lock-Token mitführen.

        Ohne diesen Token kann der Offline-Replay keinen ``expected_updated_at``
        an den Server schicken — der serverseitige Konflikt-Check (der bei
        leerem Token aussteigt) feuert dann nie und ein offline entstandener
        Edit überschreibt Server-Daten still (silent Last-Write-Wins).
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "ok"},
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        serialized = next(e for e in bundle["events"] if e["pk"] == str(event.pk))
        assert "updated_at" in serialized
        # Muss exakt der ISO-Form von ``event.updated_at`` entsprechen, damit
        # der Server-Vergleich (``datetime.fromisoformat``) ohne Offset-Drift
        # greift.
        assert serialized["updated_at"] == event.updated_at.isoformat()

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

    def test_bundle_field_template_includes_render_metadata(self, facility, client_identified, staff_user):
        """Refs #1111: Damit der Offline-Viewer ein Edit-Formular OHNE Server
        rendern kann, muss jede Feldvorlage ihre Render-Metadaten mitführen:
        Auswahl-Optionen (SELECT/MULTI_SELECT), Pflichtfeld-Flag, Hilfetext.
        Der aktuelle Wert fürs Vorbelegen liegt im Event (``data_fields``).
        """
        dt = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.CONTACT,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="MitAuswahl",
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Stimmung",
            field_type=FieldTemplate.FieldType.SELECT,
            is_required=True,
            help_text="Bitte auswählen",
            options_json=[
                {"slug": "gut", "label": "Gut", "is_active": True},
                {"slug": "schlecht", "label": "Schlecht", "is_active": True},
                {"slug": "veraltet", "label": "Veraltet", "is_active": False},
            ],
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"stimmung": "gut"},
            created_by=staff_user,
        )

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        sdt = next(d for d in bundle["document_types"] if d["pk"] == str(dt.pk))
        field = next(f for f in sdt["fields"] if f["slug"] == "stimmung")
        assert field["is_required"] is True
        assert field["help_text"] == "Bitte auswählen"
        # Nur aktive Optionen, als value/label-Paare fürs Offline-Rendering.
        assert field["options"] == [
            {"value": "gut", "label": "Gut"},
            {"value": "schlecht", "label": "Schlecht"},
        ]
        # Aktueller Wert zum Vorbelegen kommt aus dem Event-Snapshot.
        ev = next(e for e in bundle["events"] if e["document_type_pk"] == str(dt.pk))
        assert ev["data_fields"]["stimmung"] == "gut"

    def test_bundle_omits_restricted_field_definitions_for_staff(
        self, facility, client_identified, doc_type_normal_with_high_field, staff_user, lead_user
    ):
        """Refs #1111: Das Offline-Edit-Formular rendert aus den Feld-Defs des
        Bundles. Felder, deren Wert der Nutzer per Sensitivity NICHT sehen darf,
        dürfen daher auch nicht als Definition (inkl. der neuen Optionen/Hilfe-
        texte) das Bundle erreichen — gleiche Servergrenze wie die Wert-Filterung
        in ``_visible_data_fields``. Sonst zeigte der Viewer ein Eingabefeld,
        dessen Eingabe der Server beim Replay still verwirft, und es verließe
        mehr Schema die Servergrenze als die Read-Snapshot-Filterung erlaubt.
        """
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_normal_with_high_field,
            occurred_at=timezone.now(),
            data_json={"bemerkung": "x", "risiko": "y"},
            created_by=staff_user,
        )
        staff_bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        lead_bundle = build_client_offline_bundle(lead_user, facility, client_identified)
        staff_dt = next(d for d in staff_bundle["document_types"] if d["pk"] == str(doc_type_normal_with_high_field.pk))
        lead_dt = next(d for d in lead_bundle["document_types"] if d["pk"] == str(doc_type_normal_with_high_field.pk))
        staff_slugs = {f["slug"] for f in staff_dt["fields"]}
        lead_slugs = {f["slug"] for f in lead_dt["fields"]}
        assert "bemerkung" in staff_slugs
        assert "risiko" not in staff_slugs, "HIGH-Feld-Definition darf nicht zu Staff gelangen"
        assert {"bemerkung", "risiko"} <= lead_slugs

    def test_bundle_event_reports_can_edit_flag(
        self, facility, client_identified, doc_type_contact, staff_user, assistant_user
    ):
        """Refs #1111: Der Viewer blendet die Edit-Affordanz nur ein, wenn der
        Nutzer das Event auch online bearbeiten dürfte (``EventUpdateView``:
        Staff+ darf alles, Assistant nur eigene Events). Ohne dieses Flag liefe
        ein Assistant in einen 403-Replay und der Edit bliebe ewig „unsynced".
        """
        own_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            created_by=assistant_user,
        )
        foreign_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 2},
            created_by=staff_user,
        )

        staff_bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        staff_flags = {e["pk"]: e["can_edit"] for e in staff_bundle["events"]}
        # Staff+ darf jedes sichtbare Event bearbeiten.
        assert staff_flags[str(own_event.pk)] is True
        assert staff_flags[str(foreign_event.pk)] is True

        assistant_bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        assistant_flags = {e["pk"]: e["can_edit"] for e in assistant_bundle["events"]}
        assert assistant_flags[str(own_event.pk)] is True
        assert assistant_flags[str(foreign_event.pk)] is False


@pytest.mark.django_db
class TestOfflineClientBundleView:
    """HTTP contract of ``GET /api/v1/offline/bundle/client/<uuid>/``."""

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
        """Refs #1410 (b): Jeder 200-Bundle-GET schreibt genau EINEN
        ``OFFLINE_BUNDLE_READ``-Eintrag — und KEINEN ``EXPORT`` mehr. Die
        Umwidmung entrauscht die Massen-Export-Breach-Heuristik (die hart auf
        ``EXPORT`` filtert), ohne die DSGVO-Rechenschaftsspur zu verlieren.
        """
        client.force_login(staff_user)
        export_before = AuditLog.objects.filter(
            action=AuditLog.Action.EXPORT,
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        read_before = AuditLog.objects.filter(
            action=AuditLog.Action.OFFLINE_BUNDLE_READ,
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        client.get(self._url(client_identified.pk))
        export_after = AuditLog.objects.filter(
            action=AuditLog.Action.EXPORT,
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        read_after = AuditLog.objects.filter(
            action=AuditLog.Action.OFFLINE_BUNDLE_READ,
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        # Kein EXPORT mehr, dafuer genau ein OFFLINE_BUNDLE_READ.
        assert export_after == export_before
        assert read_after == read_before + 1

    def test_rate_limit_decorator_configured(self):
        """The view must be decorated with ``ratelimit`` (``RATELIMIT_OFFLINE_BUNDLE``,
        120/h/user — Refs #1354) so that the production settings
        (``RATELIMIT_ENABLE = True``) apply the cap.

        Tests run with ``RATELIMIT_ENABLE = False`` so we cannot assert a 429
        at runtime; we assert the decorator is present instead.
        """
        from core.constants import RATELIMIT_OFFLINE_BUNDLE
        from core.views import offline as offline_module
        from core.views.offline import OfflineClientBundleView

        get = OfflineClientBundleView.get
        # method_decorator copies attributes onto the wrapper — the presence
        # of a ``__wrapped__`` chain signals decoration happened.
        assert hasattr(get, "__wrapped__"), "get() should be wrapped by ratelimit"
        # Refs #1354: dediziertes Bundle-Limit statt geteiltem 30/h-Bulk-Limit.
        # Der Modul-Namespace-Check stellt sicher, dass die View die Konstante
        # importiert (und nicht ein hartcodiertes Rate traegt).
        assert RATELIMIT_OFFLINE_BUNDLE == "120/h"
        assert offline_module.RATELIMIT_OFFLINE_BUNDLE == "120/h"

    def test_rate_limited_after_120_requests(self, client, client_identified, staff_user):
        """With rate-limiting forcibly enabled, the 121st request per hour is
        blocked (Refs #1354: dediziertes ``RATELIMIT_OFFLINE_BUNDLE`` = 120/h,
        statt dem geteilten 30/h-``RATELIMIT_BULK_ACTION``).

        django-ratelimit's ``Ratelimited`` (a ``PermissionDenied`` subclass) is
        mapped to HTTP 429 by the custom ``handler403`` — not Django's default
        403 — weil der Offline-Client 403 als Rechteentzug deutet und lokale
        verschlüsselte Bundles purgt (siehe ``offline-store.js``
        ``INVALIDATION_STATUSES``). Ein Rate-Limit-Treffer ist kein
        Rechteentzug.
        """
        cache.clear()
        client.force_login(staff_user)
        url = self._url(client_identified.pk)
        with override_settings(RATELIMIT_ENABLE=True):
            for _ in range(120):
                response = client.get(url)
                assert response.status_code == 200
            response = client.get(url)
            assert response.status_code == 429
        cache.clear()

    def test_real_permission_denied_still_403(self, client, client_identified, super_admin_user):
        """Kontrolltest (Refs #1354): Ein echter Berechtigungs-403 bleibt 403 —
        der ``handler403`` mappt ausschliesslich ``Ratelimited`` auf 429.

        super_admin ist per ``is_assistant_or_above`` vom Bundle-Endpoint
        ausgeschlossen (kein Facility-Bezug); der authentifizierte Zugriff
        wirft ``PermissionDenied`` und muss durch den Custom-Handler
        unveraendert als 403 herauskommen.
        """
        client.force_login(super_admin_user)
        response = client.get(self._url(client_identified.pk))
        assert response.status_code == 403

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

    # ── Refs #1410 (a): ETag-Revalidierung ──────────────────────────────────

    def test_response_carries_etag_header(self, client, client_identified, staff_user):
        """Der 200-Bundle-Response traegt einen (quoted) ETag, damit der Client
        spaeter bedingt revalidieren kann."""
        client.force_login(staff_user)
        response = client.get(self._url(client_identified.pk))
        assert response.status_code == 200
        etag = response.headers.get("ETag")
        assert etag, "Response muss einen ETag-Header tragen"
        assert etag.startswith('"') and etag.endswith('"'), "ETag muss HTTP-konform gequotet sein"

    def test_matching_if_none_match_returns_304_without_body_or_audit(self, client, client_identified, staff_user):
        """Ein bedingter GET mit passendem If-None-Match liefert 304 (kein
        Body) und schreibt KEINEN Audit-Eintrag (kein Datenabfluss)."""
        client.force_login(staff_user)
        etag = client.get(self._url(client_identified.pk)).headers["ETag"]

        audit_before = AuditLog.objects.filter(
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        response = client.get(self._url(client_identified.pk), HTTP_IF_NONE_MATCH=etag)
        assert response.status_code == 304
        assert response.headers["ETag"] == etag
        assert response.content == b""
        audit_after = AuditLog.objects.filter(
            target_type="Client-OfflineBundle",
            target_id=str(client_identified.pk),
        ).count()
        assert audit_after == audit_before, "304 darf keine Audit-Spur schreiben"

    def test_stale_if_none_match_returns_200_with_etag_and_audit(self, client, client_identified, staff_user):
        """Ein nicht passender If-None-Match liefert 200 mit ETag und schreibt
        eine OFFLINE_BUNDLE_READ-Spur (regulaerer Read-Pfad)."""
        client.force_login(staff_user)
        audit_before = AuditLog.objects.filter(
            action=AuditLog.Action.OFFLINE_BUNDLE_READ,
            target_id=str(client_identified.pk),
        ).count()
        response = client.get(self._url(client_identified.pk), HTTP_IF_NONE_MATCH='"stale-etag"')
        assert response.status_code == 200
        assert response.headers.get("ETag")
        audit_after = AuditLog.objects.filter(
            action=AuditLog.Action.OFFLINE_BUNDLE_READ,
            target_id=str(client_identified.pk),
        ).count()
        assert audit_after == audit_before + 1

    def test_etag_stable_and_changes_on_data_change(self, client, client_identified, staff_user, doc_type_contact):
        """Der ETag ist stabil bei unveraenderten Daten (zwei Requests → gleicher
        ETag, weil generated_at/expires_at/ttl NICHT einfliessen) und aendert
        sich nach einer relevanten Datenaenderung (neues Event)."""
        client.force_login(staff_user)
        etag_1 = client.get(self._url(client_identified.pk)).headers["ETag"]
        etag_2 = client.get(self._url(client_identified.pk)).headers["ETag"]
        assert etag_1 == etag_2, "ETag muss ueber volatile Metadaten hinweg stabil sein"

        Event.objects.create(
            facility=staff_user.facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        etag_3 = client.get(self._url(client_identified.pk)).headers["ETag"]
        assert etag_3 != etag_1, "Neues Event muss den ETag aendern"


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
    a malformed UUID to ``/api/v1/offline/bundle/client/...`` → 404.
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


@pytest.mark.django_db
class TestBundleWorkItemAuthoring:
    """Refs #1398: WorkItems offline erfassen/bearbeiten — das Bundle muss die
    editierbaren Felder + Konflikt-Token (updated_at) + ``can_edit`` tragen und
    (nur fuer Staff+) die zuweisbaren Nutzer:innen liefern."""

    def _mk_workitem(self, facility, client, creator, **kw):
        kw.setdefault("title", "Aufgabe")
        return WorkItem.objects.create(facility=facility, client=client, created_by=creator, **kw)

    def test_workitem_carries_edit_fields(self, facility, client_identified, staff_user):
        from datetime import date

        wi = self._mk_workitem(
            facility,
            client_identified,
            staff_user,
            description="desc",
            priority=WorkItem.Priority.URGENT,
            item_type=WorkItem.ItemType.HINT,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=date(2099, 1, 1),
            remind_at=date(2098, 12, 1),
            assigned_to=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        entry = next(w for w in bundle["workitems"] if w["pk"] == str(wi.pk))
        assert entry["updated_at"] is not None
        assert entry["remind_at"] == "2098-12-01"
        assert entry["recurrence"] == WorkItem.Recurrence.WEEKLY
        assert entry["assigned_to_pk"] == str(staff_user.pk)
        assert entry["can_edit"] is True  # Ersteller

    def test_workitem_can_edit_unassigned_team_task(self, facility, client_identified, staff_user, lead_user):
        wi = self._mk_workitem(facility, client_identified, lead_user, assigned_to=None)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        entry = next(w for w in bundle["workitems"] if w["pk"] == str(wi.pk))
        assert entry["can_edit"] is True  # unzugewiesene Teamaufgabe (#1125)

    def test_workitem_not_editable_when_assigned_to_other(self, facility, client_identified, staff_user, lead_user):
        wi = self._mk_workitem(facility, client_identified, lead_user, assigned_to=lead_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        entry = next(w for w in bundle["workitems"] if w["pk"] == str(wi.pk))
        assert entry["can_edit"] is False  # weder Ersteller/Zugewiesener/Lead

    def test_assistant_cannot_edit_workitem(self, facility, client_identified, assistant_user, staff_user):
        wi = self._mk_workitem(facility, client_identified, staff_user, assigned_to=None)
        bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        entry = next(w for w in bundle["workitems"] if w["pk"] == str(wi.pk))
        assert entry["can_edit"] is False  # StaffRequired schliesst Assistenz aus

    def test_bundle_ships_assignable_users_for_staff(
        self, facility, client_identified, staff_user, lead_user, assistant_user
    ):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {u["pk"] for u in bundle["assignable_users"]}
        assert str(staff_user.pk) in pks
        assert str(lead_user.pk) in pks
        assert str(assistant_user.pk) in pks  # Assistenz ist zuweisbar (#1125)

    def test_assignable_users_excludes_inactive_and_super_admin(self, facility, client_identified, staff_user):
        inactive = User.objects.create_user(
            username="inaktiv", role=User.Role.STAFF, facility=facility, is_active=False
        )
        superad = User.objects.create_user(username="super", role=User.Role.SUPER_ADMIN, facility=facility)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {u["pk"] for u in bundle["assignable_users"]}
        assert str(inactive.pk) not in pks
        assert str(superad.pk) not in pks

    def test_assistant_bundle_omits_assignable_users(self, facility, client_identified, assistant_user):
        bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        assert bundle["assignable_users"] == []
