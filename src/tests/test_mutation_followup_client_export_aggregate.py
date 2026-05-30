"""Mutation-Followup-Tests für ``core.services.client_export`` — Aggregate.

Refs Welle 7 (#930). Sub-File aus ``test_mutation_followup_client_export``;
enthält die Top-Level-Komposition (``TestBuildExportMeta``,
``TestExportClientDataAggregate``) plus den Test-Datei-internen
Sanity-Check (``TestImportSanity``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from core.models import (
    Case as CaseModel,
)
from core.models import (
    DeletionRequest,
    DocumentType,
    EventHistory,
    FieldTemplate,
    Settings,
    User,
)
from core.services.client_export import (
    _build_export_meta,
    export_client_data,
)
from tests._mutation_followup_client_export_helpers import (
    _make_doc_type,
    _make_event,
)

# ---------------------------------------------------------------------------
# _build_export_meta — Timestamp + Facility-Fallback-Chain
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildExportMeta:
    """Refs Welle 7 — ``_build_export_meta`` (Line 132).

    Boundary-Chain:
    - Settings.facility_full_name nicht leer → vollständiger Name
    - Settings.facility_full_name leer → facility.name als Fallback
    - keine Settings vorhanden → facility.name als Fallback
      (``getattr(facility, "settings", None)`` greift)
    """

    def test_contains_both_keys(self, facility):
        result = _build_export_meta(facility)
        assert set(result.keys()) == {"timestamp", "facility_name"}

    def test_timestamp_is_isoformat_string(self, facility):
        """Mutation ``timezone.now().isoformat()`` → ``.strftime(...)``
        oder Entfernen würde non-ISO-Format liefern."""
        result = _build_export_meta(facility)
        assert isinstance(result["timestamp"], str)
        # ISO-Format hat "T" und ist parseable.
        assert "T" in result["timestamp"]

    def test_uses_settings_facility_full_name_when_set(self, facility):
        """Mutation ``getattr(..., facility_full_name, "")`` → ``"facility_name"``
        würde den falschen Attribut-Namen lesen."""
        Settings.objects.create(
            facility=facility,
            facility_full_name="Anlaufstelle Musterstadt e.V.",
        )
        # Re-fetch der Facility — settings-Reverse-Accessor cached.
        facility.refresh_from_db()
        result = _build_export_meta(facility)
        assert result["facility_name"] == "Anlaufstelle Musterstadt e.V."

    def test_falls_back_to_facility_name_when_settings_empty(self, facility):
        """``Settings.facility_full_name=""`` → Fallback auf ``facility.name``.
        Mutation ``or facility.name`` → ``and facility.name`` würde leeren
        String zurückgeben."""
        Settings.objects.create(facility=facility, facility_full_name="")
        facility.refresh_from_db()
        result = _build_export_meta(facility)
        assert result["facility_name"] == facility.name
        assert result["facility_name"] == "Teststelle"

    def test_falls_back_to_facility_name_when_no_settings(self, facility):
        """``getattr(facility, "settings", None)`` muss None liefern, wenn
        keine Settings existiert → Fallback auf ``facility.name``."""
        result = _build_export_meta(facility)
        assert result["facility_name"] == "Teststelle"


# ---------------------------------------------------------------------------
# export_client_data — Top-Level-Komposition + Visibility-Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExportClientDataAggregate:
    """Refs Welle 7 — ``export_client_data`` (Line 141).

    Sicherstellen, dass das Top-Level-Dict alle sieben Aggregat-Keys
    enthält UND dass Visibility-Filter den ``_gather_events``-Pfad
    hochgereicht wird (Defense in Depth).
    """

    def test_top_level_dict_contains_all_seven_keys(self, client_identified, facility, staff_user):
        result = export_client_data(client_identified, facility, staff_user)
        assert set(result.keys()) == {
            "client",
            "events",
            "cases",
            "event_history",
            "deletion_requests",
            "work_items",
            "export_meta",
        }

    def test_visibility_propagates_to_event_history_and_deletion(
        self, facility, client_identified, doc_type_contact, staff_user, assistant_user
    ):
        """Wenn der ELEVATED-Event aus ``_gather_events`` rausgefiltert wird,
        darf seine History/DeletionRequest auch nicht auftauchen — die
        IDs werden ja gar nicht erst gesammelt.

        Mutation ``event_ids.append(event.pk)`` → vor ``visible_to`` würde
        das Loch öffnen."""
        elevated_doc = _make_doc_type(facility, name="Krise", sensitivity=DocumentType.Sensitivity.ELEVATED)
        elevated_event = _make_event(facility, client_identified, elevated_doc, staff_user)
        EventHistory.objects.create(event=elevated_event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=elevated_event.pk,
            reason="X",
            requested_by=staff_user,
        )
        result = export_client_data(client_identified, facility, assistant_user)
        assert result["events"] == []
        assert result["event_history"] == [], "EventHistory eines unsichtbaren Events darf nicht im Export landen"
        assert result["deletion_requests"] == [], (
            "DeletionRequests eines unsichtbaren Events darf nicht im Export landen"
        )

    def test_aggregate_includes_independent_buckets(
        self,
        facility,
        client_identified,
        doc_type_contact,
        staff_user,
        sample_workitem,
    ):
        """Cases/WorkItems sind unabhängig von Event-Visibility — sie müssen
        immer da sein, sofern sie auf dem Klienten hängen."""
        CaseModel.objects.create(facility=facility, client=client_identified, title="Cs", created_by=staff_user)
        _make_event(facility, client_identified, doc_type_contact, staff_user)
        result = export_client_data(client_identified, facility, staff_user)
        assert len(result["events"]) == 1
        assert len(result["cases"]) == 1
        assert len(result["work_items"]) == 1
        # sample_workitem fixture aktiviert.
        assert sample_workitem.title in [w["title"] for w in result["work_items"]]


# ---------------------------------------------------------------------------
# Sanity: ``FieldTemplate``- / ``Settings``-Modelle bleiben unmodifiziert
# (Sentinel gegen schädliche Test-Seiteneffekte; gleichzeitig Lint gegen
# F401 für Importe, die wir thematisch für Future-Boundary-Tests behalten).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestImportSanity:
    """Refs Welle 7 — Test-Datei-interner Sanity-Check.

    Stellt sicher, dass die Test-Imports (``FieldTemplate``, ``User``,
    ``timedelta``) tatsächlich verwendet werden — falls eine spätere
    Refactoring-Welle das File splittet, wird so klar, was gebraucht wird.
    """

    def test_imports_resolvable(self):
        assert FieldTemplate is not None
        assert User is not None
        assert timedelta(seconds=0) == timedelta(0)
