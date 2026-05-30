"""Mutation-Followup-Tests für ``core.services.client.export`` — Gather-Helfer.

Refs #930. Sub-File aus ``test_mutation_followup_client_export``;
enthält die Test-Klassen für die privaten ``_gather_*``-Helfer
(``_gather_client_fields``, ``_gather_events``, ``_gather_cases``,
``_gather_event_history``, ``_gather_deletion_requests``,
``_gather_workitems``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import (
    Case as CaseModel,
)
from core.models import (
    DeletionRequest,
    DocumentType,
    EventHistory,
    WorkItem,
)
from core.services.client import (
    _gather_cases,
    _gather_client_fields,
    _gather_deletion_requests,
    _gather_event_history,
    _gather_events,
    _gather_workitems,
)
from tests._mutation_followup_client_export_helpers import (
    _make_doc_type,
    _make_event,
)

# ---------------------------------------------------------------------------
# _gather_client_fields — DSGVO-vollständige Master-Daten
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherClientFields:
    """Refs ``_gather_client_fields`` (Line 12).

    Mutation jedes einzelnen ``key`` würde das Result-Dict beschneiden.
    DSGVO Art. 15 verlangt aber Vollständigkeit der Stammdaten — wir
    pinnen jedes Feld explizit fest.
    """

    def test_contains_all_six_master_fields(self, client_identified):
        result = _gather_client_fields(client_identified)
        assert set(result.keys()) == {
            "pseudonym",
            "contact_stage",
            "age_cluster",
            "is_active",
            "created_at",
            "created_by",
        }

    def test_pseudonym_is_preserved(self, client_identified):
        result = _gather_client_fields(client_identified)
        assert result["pseudonym"] == client_identified.pseudonym

    def test_contact_stage_uses_display_label_not_raw_value(self, client_identified):
        """Mutation ``get_contact_stage_display()`` → ``contact_stage``
        würde den Rohwert ``"identified"`` statt ``"Identifiziert"`` liefern.
        """
        result = _gather_client_fields(client_identified)
        # i18n-Label, nicht der Rohwert.
        assert result["contact_stage"] == "Identifiziert"
        assert result["contact_stage"] != "identified"

    def test_contact_stage_qualified(self, client_qualified):
        result = _gather_client_fields(client_qualified)
        assert result["contact_stage"] == "Qualifiziert"

    def test_age_cluster_uses_display_label(self, facility, staff_user):
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="age-test",
            contact_stage=Client.ContactStage.IDENTIFIED,
            age_cluster=Client.AgeCluster.AGE_18_26,
            created_by=staff_user,
        )
        result = _gather_client_fields(c)
        assert result["age_cluster"] == "18–26"

    def test_is_active_true(self, client_identified):
        result = _gather_client_fields(client_identified)
        assert result["is_active"] is True

    def test_is_active_false_is_preserved(self, facility, staff_user):
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="inactive",
            contact_stage=Client.ContactStage.IDENTIFIED,
            is_active=False,
            created_by=staff_user,
        )
        result = _gather_client_fields(c)
        assert result["is_active"] is False, "Bool muss 1:1 durchgereicht werden, nicht negiert"

    def test_created_at_is_isoformat_string(self, client_identified):
        """Mutation ``.isoformat()`` → ``.strftime(...)`` oder Entfernen würde
        ein ``datetime``-Objekt liefern statt String. ISO-Format ist
        Pflicht für JSON-Serialisierung des Exports."""
        result = _gather_client_fields(client_identified)
        assert isinstance(result["created_at"], str)
        assert result["created_at"] == client_identified.created_at.isoformat()
        # ISO-Format hat ein "T" zwischen Datum und Uhrzeit.
        assert "T" in result["created_at"]

    def test_created_by_username_when_present(self, client_identified, staff_user):
        result = _gather_client_fields(client_identified)
        assert result["created_by"] == staff_user.username

    def test_created_by_none_when_user_missing(self, facility):
        """Mutation ``... if client.created_by else None`` → ``... if client.created_by``
        ohne else-Branch würde crashen. Wir simulieren den FK=NULL-Fall."""
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="no-creator",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=None,
        )
        result = _gather_client_fields(c)
        assert result["created_by"] is None


# ---------------------------------------------------------------------------
# _gather_events — Filter-Boundaries, Visibility, Sort, Return-Tuple
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEvents:
    """Refs ``_gather_events`` (Line 41).

    Schwerpunkte:
    - Tuple-Return ``(events_data, event_ids)``
    - Filter: ``client=client``, ``is_deleted=False``
    - Visibility-Filter: ``Event.objects.visible_to(user)``
    - Sort: ``-occurred_at`` (neueste zuerst)
    """

    def test_returns_tuple_of_list_and_id_list(self, facility, client_identified, doc_type_contact, staff_user):
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert isinstance(events_data, list)
        assert isinstance(event_ids, list)
        assert len(events_data) == 1
        assert ev.pk in event_ids

    def test_empty_when_no_events(self, client_identified, staff_user):
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert events_data == []
        assert event_ids == []

    def test_excludes_soft_deleted_events(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``is_deleted=False`` → ``is_deleted=True`` würde alle
        gelöschten Events im Export landen lassen. ``False`` → entfernt
        würde wiederum gelöschte mit-exportieren."""
        kept = _make_event(facility, client_identified, doc_type_contact, staff_user)
        deleted = _make_event(facility, client_identified, doc_type_contact, staff_user, is_deleted=True)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert kept.pk in event_ids
        assert deleted.pk not in event_ids
        assert len(events_data) == 1

    def test_excludes_events_of_other_clients(
        self, facility, client_identified, client_qualified, doc_type_contact, staff_user
    ):
        """Mutation ``client=client`` → ``client=anything_else`` würde
        Cross-Klient-Daten leaken. Wir legen je einen Event auf zwei
        Klienten und prüfen Isolation."""
        ev_own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        ev_other = _make_event(facility, client_qualified, doc_type_contact, staff_user)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert ev_own.pk in event_ids
        assert ev_other.pk not in event_ids

    def test_assistant_cannot_see_elevated_events(
        self, facility, client_identified, doc_type_contact, staff_user, assistant_user
    ):
        """``visible_to(user)``-Filter: ASSISTANT (rank 0) darf ELEVATED
        nicht sehen. Mutation ``visible_to(user)`` → ``all()`` würde den
        Elevated-Event im Assistent:innen-Export auftauchen lassen.
        """
        elevated_doc = _make_doc_type(facility, name="Krise", sensitivity=DocumentType.Sensitivity.ELEVATED)
        normal_event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        elevated_event = _make_event(facility, client_identified, elevated_doc, staff_user)

        events_data, event_ids = _gather_events(client_identified, assistant_user)
        assert normal_event.pk in event_ids
        assert elevated_event.pk not in event_ids, (
            "ASSISTANT darf ELEVATED-Events nicht im Export sehen — Sensitivity-Matrix verletzt"
        )

    def test_lead_sees_high_sensitivity_events(self, facility, client_identified, staff_user, lead_user):
        """LEAD (rank 2) sieht HIGH-Events. Boundary auf der oberen Seite
        der Sensitivity-Matrix."""
        high_doc = _make_doc_type(facility, name="Hoch", sensitivity=DocumentType.Sensitivity.HIGH)
        ev = _make_event(facility, client_identified, high_doc, staff_user)
        events_data, event_ids = _gather_events(client_identified, lead_user)
        assert ev.pk in event_ids

    def test_order_descending_by_occurred_at(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``order_by("-occurred_at")`` → ``order_by("occurred_at")``
        oder Entfernen würde die Sortierung kippen. Neueste Events
        müssen oben stehen."""
        now = timezone.now()
        ev_old = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=now - timedelta(days=5),
        )
        ev_mid = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=now - timedelta(days=1),
        )
        ev_new = _make_event(facility, client_identified, doc_type_contact, staff_user, occurred_at=now)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        # Reihenfolge: neueste zuerst, dann mid, dann old.
        assert event_ids == [ev_new.pk, ev_mid.pk, ev_old.pk]

    def test_event_data_and_ids_have_same_length_and_order(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation an einer der zwei ``append``-Stellen würde Listen
        unterschiedlicher Länge produzieren — beides muss parallel
        wachsen und die Reihenfolge muss übereinstimmen."""
        now = timezone.now()
        evs = [
            _make_event(
                facility,
                client_identified,
                doc_type_contact,
                staff_user,
                occurred_at=now - timedelta(hours=i),
            )
            for i in range(3)
        ]
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert len(events_data) == len(event_ids) == 3
        # Parallel-Reihenfolge: events_data und event_ids referenzieren das
        # selbe Event an Position i (neueste zuerst → evs[0]).
        assert event_ids[0] == evs[0].pk


# ---------------------------------------------------------------------------
# _gather_cases — Field-Selection, Filter, Sort
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherCases:
    """Refs ``_gather_cases`` (Line 64).

    Mutationen im Dict-Literal entfernen einzelne Case-Felder. Wir
    pinnen jedes Feld einzeln + Sort + Filter-Boundary.
    """

    def test_empty_when_no_cases(self, client_identified):
        assert _gather_cases(client_identified) == []

    def test_contains_all_seven_fields(self, facility, client_identified, staff_user):
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="T",
            description="D",
            status=CaseModel.Status.OPEN,
            created_by=staff_user,
        )
        result = _gather_cases(client_identified)
        assert len(result) == 1
        entry = result[0]
        assert set(entry.keys()) == {
            "title",
            "description",
            "status",
            "created_at",
            "closed_at",
            "lead_user",
            "created_by",
        }

    def test_title_preserved(self, facility, client_identified, staff_user):
        CaseModel.objects.create(facility=facility, client=client_identified, title="Mein Fall", created_by=staff_user)
        assert _gather_cases(client_identified)[0]["title"] == "Mein Fall"

    def test_description_preserved(self, facility, client_identified, staff_user):
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            description="Lange Beschreibung",
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["description"] == "Lange Beschreibung"

    def test_status_uses_display_label(self, facility, client_identified, staff_user):
        """Mutation ``get_status_display()`` → ``status`` würde
        ``"open"`` statt ``"Offen"`` liefern."""
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.OPEN,
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["status"] == "Offen"

    def test_status_closed_label(self, facility, client_identified, staff_user):
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["status"] == "Geschlossen"

    def test_created_at_isoformat(self, facility, client_identified, staff_user):
        c = CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        result = _gather_cases(client_identified)[0]
        assert isinstance(result["created_at"], str)
        assert result["created_at"] == c.created_at.isoformat()

    def test_closed_at_none_when_open(self, facility, client_identified, staff_user):
        """Mutation ``... if case.closed_at else None`` → fester Wert würde
        bei offenen Fällen einen falschen Timestamp liefern."""
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.OPEN,
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["closed_at"] is None

    def test_closed_at_isoformat_when_set(self, facility, client_identified, staff_user):
        t = timezone.now()
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.CLOSED,
            closed_at=t,
            created_by=staff_user,
        )
        result = _gather_cases(client_identified)[0]
        assert isinstance(result["closed_at"], str)
        assert result["closed_at"] == t.isoformat()

    def test_lead_user_username(self, facility, client_identified, staff_user, lead_user):
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            lead_user=lead_user,
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["lead_user"] == lead_user.username

    def test_lead_user_none_when_unset(self, facility, client_identified, staff_user):
        """Mutation ``... if case.lead_user else None`` → fester Wert würde
        bei fehlendem Lead crashen oder falsche Werte liefern."""
        CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        assert _gather_cases(client_identified)[0]["lead_user"] is None

    def test_created_by_username(self, facility, client_identified, staff_user):
        CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        assert _gather_cases(client_identified)[0]["created_by"] == staff_user.username

    def test_created_by_none_when_unset(self, facility, client_identified):
        """``created_by`` ist optional (SET_NULL) — None muss durchgereicht werden."""
        CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=None)
        assert _gather_cases(client_identified)[0]["created_by"] is None

    def test_excludes_cases_of_other_clients(self, facility, client_identified, client_qualified, staff_user):
        """``filter(client=client)``-Boundary: Cross-Klient-Daten müssen
        unsichtbar bleiben."""
        own = CaseModel.objects.create(facility=facility, client=client_identified, title="Own", created_by=staff_user)
        CaseModel.objects.create(facility=facility, client=client_qualified, title="Other", created_by=staff_user)
        results = _gather_cases(client_identified)
        assert len(results) == 1
        assert results[0]["title"] == "Own"
        assert own  # noqa: B015 — gegen Lint-Warning

    def test_order_descending_by_created_at(self, facility, client_identified, staff_user):
        """Mutation ``-created_at`` → ``created_at`` würde die Reihenfolge
        invertieren (ältester zuerst)."""
        c1 = CaseModel.objects.create(facility=facility, client=client_identified, title="Erst", created_by=staff_user)
        c2 = CaseModel.objects.create(
            facility=facility, client=client_identified, title="Zweite", created_by=staff_user
        )
        c3 = CaseModel.objects.create(
            facility=facility, client=client_identified, title="Dritte", created_by=staff_user
        )
        # auto_now_add → c3 > c2 > c1
        result = _gather_cases(client_identified)
        titles = [r["title"] for r in result]
        assert titles == ["Dritte", "Zweite", "Erst"]
        assert c1 and c2 and c3  # noqa: B015


# ---------------------------------------------------------------------------
# _gather_event_history — IDs, Field-Selection, Sort
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEventHistory:
    """Refs ``_gather_event_history`` (Line 81).

    Mutationen:
    - ``filter(event_id__in=event_ids)`` → ohne Filter → cross-Event-Leak
    - ``order_by("-changed_at")`` → ASC würde Doku-Reihenfolge kippen
    - Einzelne Dict-Felder entfernt
    """

    def test_empty_when_no_history(self):
        assert _gather_event_history([]) == []

    def test_empty_event_ids_returns_empty(self, sample_event, staff_user):
        """``event_ids=[]`` → ``__in=[]`` → leeres Queryset.

        Mutation ``event_id__in`` → ``event_id`` (without lookup) würde
        bei einer leeren Liste in einen TypeError laufen oder alle
        History-Entries laden. Wir legen einen History-Entry an, der
        existiert, aber bei leerer ``event_ids``-Liste NICHT auftauchen
        darf — so wird klar, dass der Filter greift, nicht nur ein
        leeres Result aus leerer Tabelle kommt.
        """
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        assert _gather_event_history([]) == []

    def test_contains_all_three_fields(self, sample_event, staff_user):
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        result = _gather_event_history([sample_event.pk])
        assert len(result) == 1
        assert set(result[0].keys()) == {"action", "changed_at", "changed_by"}

    def test_action_uses_display_label(self, sample_event, staff_user):
        """Mutation ``get_action_display()`` → ``action`` würde ``"update"``
        statt ``"Aktualisiert"`` liefern."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
        )
        result = _gather_event_history([sample_event.pk])
        assert result[0]["action"] == "Aktualisiert"

    def test_action_create_label(self, sample_event, staff_user):
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        assert _gather_event_history([sample_event.pk])[0]["action"] == "Erstellt"

    def test_changed_at_isoformat(self, sample_event, staff_user):
        h = EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        result = _gather_event_history([sample_event.pk])
        assert isinstance(result[0]["changed_at"], str)
        assert result[0]["changed_at"] == h.changed_at.isoformat()

    def test_changed_by_username(self, sample_event, staff_user):
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
        )
        assert _gather_event_history([sample_event.pk])[0]["changed_by"] == staff_user.username

    def test_changed_by_none_when_user_missing(self, sample_event):
        """Mutation ``... if entry.changed_by else None`` → fester Wert würde
        bei FK=NULL crashen."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=None,
        )
        assert _gather_event_history([sample_event.pk])[0]["changed_by"] is None

    def test_order_descending_by_changed_at(self, sample_event, staff_user):
        """Mutation ``-changed_at`` → ``changed_at`` würde älteste zuerst
        zeigen. Wir verifizieren ID-Reihenfolge bei auto_now_add."""
        EventHistory.objects.create(event=sample_event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        EventHistory.objects.create(event=sample_event, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        h_last = EventHistory.objects.create(
            event=sample_event, action=EventHistory.Action.UPDATE, changed_by=staff_user
        )
        result = _gather_event_history([sample_event.pk])
        # auto_now_add → der zuletzt erstellte Entry ist neuester →
        # mit -changed_at-Sort steht er an Position 0.
        # Falls Timestamps gleich sind, bleibt zumindest die Menge identisch.
        assert len(result) == 3
        # Bei strikter Monotonie: h_last steht oben.
        assert result[0]["changed_at"] >= result[-1]["changed_at"]
        assert h_last  # noqa: B015

    def test_excludes_history_of_other_events(self, facility, client_identified, doc_type_contact, staff_user):
        """``filter(event_id__in=event_ids)``-Boundary: History eines
        anderen Events darf nicht im Export auftauchen."""
        ev_own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        ev_other = _make_event(facility, client_identified, doc_type_contact, staff_user)
        EventHistory.objects.create(event=ev_own, action=EventHistory.Action.CREATE, changed_by=staff_user)
        EventHistory.objects.create(event=ev_other, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        result = _gather_event_history([ev_own.pk])
        assert len(result) == 1
        assert result[0]["action"] == "Erstellt"


# ---------------------------------------------------------------------------
# _gather_deletion_requests — Filter + Field-Selection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherDeletionRequests:
    """Refs ``_gather_deletion_requests`` (Line 96).

    Mutationen:
    - ``target_id__in=event_ids`` → falsche Boundary leaked
    - ``target_type="Event"`` → falsche Type-Konstante leaked
      Client-Löschanträge in den Event-Export
    - Einzelne Felder entfernt
    - ``order_by("-created_at")``
    """

    def test_empty_when_no_requests(self):
        assert _gather_deletion_requests([]) == []

    def test_contains_all_six_fields(self, facility, sample_event, staff_user, lead_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="GDPR",
            requested_by=staff_user,
            reviewed_by=lead_user,
            reviewed_at=timezone.now(),
            status=DeletionRequest.Status.APPROVED,
        )
        result = _gather_deletion_requests([sample_event.pk])
        assert len(result) == 1
        assert set(result[0].keys()) == {
            "status",
            "reason",
            "requested_by",
            "reviewed_by",
            "created_at",
            "reviewed_at",
        }

    def test_status_uses_display_label(self, facility, sample_event, staff_user):
        """Mutation ``get_status_display()`` → ``status`` würde Roh-Slug liefern."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            status=DeletionRequest.Status.PENDING,
        )
        assert _gather_deletion_requests([sample_event.pk])[0]["status"] == "Ausstehend"

    def test_status_approved_label(self, facility, sample_event, staff_user, lead_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            reviewed_by=lead_user,
            status=DeletionRequest.Status.APPROVED,
            reviewed_at=timezone.now(),
        )
        assert _gather_deletion_requests([sample_event.pk])[0]["status"] == "Genehmigt"

    def test_reason_preserved(self, facility, sample_event, staff_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="Klient hat Auskunft beantragt",
            requested_by=staff_user,
        )
        assert _gather_deletion_requests([sample_event.pk])[0]["reason"] == ("Klient hat Auskunft beantragt")

    def test_requested_by_username(self, facility, sample_event, staff_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
        )
        assert _gather_deletion_requests([sample_event.pk])[0]["requested_by"] == staff_user.username

    def test_reviewed_by_none_when_pending(self, facility, sample_event, staff_user):
        """Mutation ``... if dr.reviewed_by else None`` → fester Wert würde
        bei PENDING crashen."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            status=DeletionRequest.Status.PENDING,
        )
        result = _gather_deletion_requests([sample_event.pk])[0]
        assert result["reviewed_by"] is None
        assert result["reviewed_at"] is None

    def test_reviewed_by_username_when_approved(self, facility, sample_event, staff_user, lead_user):
        t = timezone.now()
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            reviewed_by=lead_user,
            status=DeletionRequest.Status.APPROVED,
            reviewed_at=t,
        )
        result = _gather_deletion_requests([sample_event.pk])[0]
        assert result["reviewed_by"] == lead_user.username
        assert result["reviewed_at"] == t.isoformat()

    def test_created_at_isoformat(self, facility, sample_event, staff_user):
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests([sample_event.pk])[0]
        assert result["created_at"] == dr.created_at.isoformat()

    def test_filter_target_id_in_event_ids(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``target_id__in=event_ids`` → ohne Filter würde alle
        DeletionRequests laden — auch von anderen Events."""
        ev_own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        ev_other = _make_event(facility, client_identified, doc_type_contact, staff_user)
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=ev_own.pk,
            reason="Own",
            requested_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=ev_other.pk,
            reason="Other",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests([ev_own.pk])
        assert len(result) == 1
        assert result[0]["reason"] == "Own"

    def test_filter_target_type_event_only(self, facility, client_identified, sample_event, staff_user):
        """Mutation ``target_type="Event"`` → ``"Client"`` würde komplett
        andere Records liefern. Wir legen einen CLIENT-DeletionRequest
        mit demselben ``target_id`` an und stellen sicher, dass er
        nicht im Event-Export landet."""
        # CLIENT-DR mit IDENTISCHEM target_id wie sample_event.pk
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=sample_event.pk,  # bewusst gleicher UUID
            reason="Client-DR — soll im Event-Export NICHT auftauchen",
            requested_by=staff_user,
        )
        # EVENT-DR — soll auftauchen.
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="Echter Event-DR",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests([sample_event.pk])
        assert len(result) == 1
        assert result[0]["reason"] == "Echter Event-DR"

    def test_order_descending_by_created_at(self, facility, sample_event, staff_user, lead_user):
        """Mutation ``-created_at`` → ``created_at`` würde die Reihenfolge
        invertieren."""
        d1 = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="A",
            requested_by=staff_user,
            status=DeletionRequest.Status.REJECTED,
            reviewed_by=lead_user,
            reviewed_at=timezone.now(),
        )
        # zweiter DR — partial unique blockiert nur PENDING, deshalb
        # beide REJECTED.
        d2 = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="B",
            requested_by=staff_user,
            status=DeletionRequest.Status.REJECTED,
            reviewed_by=lead_user,
            reviewed_at=timezone.now(),
        )
        result = _gather_deletion_requests([sample_event.pk])
        # auto_now_add → d2 später als d1 → d2 zuerst.
        reasons = [r["reason"] for r in result]
        assert reasons[0] == "B"
        assert reasons[-1] == "A"
        assert d1 and d2  # noqa: B015


# ---------------------------------------------------------------------------
# _gather_workitems — Field-Selection, Filter, Sort
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherWorkItems:
    """Refs ``_gather_workitems`` (Line 116).

    Mutationen:
    - ``filter(client=client)`` → cross-Klient-Leak
    - ``order_by("-created_at")`` → ASC würde älteste zuerst zeigen
    - Einzelne Felder entfernt
    """

    def test_empty_when_no_workitems(self, client_identified):
        assert _gather_workitems(client_identified) == []

    def test_contains_all_six_fields(self, sample_workitem):
        result = _gather_workitems(sample_workitem.client)
        assert len(result) == 1
        assert set(result[0].keys()) == {
            "title",
            "description",
            "status",
            "priority",
            "created_at",
            "due_date",
        }

    def test_title_preserved(self, sample_workitem):
        result = _gather_workitems(sample_workitem.client)
        assert result[0]["title"] == "Test-Aufgabe"

    def test_description_preserved(self, facility, client_identified, staff_user):
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            description="Lange Workitem-Beschreibung",
        )
        assert _gather_workitems(client_identified)[0]["description"] == ("Lange Workitem-Beschreibung")

    def test_status_uses_display_label(self, sample_workitem):
        """Mutation ``get_status_display()`` → ``status`` würde Slug
        statt i18n-Label liefern."""
        result = _gather_workitems(sample_workitem.client)
        assert result[0]["status"] == "Offen"

    def test_status_done_label(self, facility, client_identified, staff_user):
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            status=WorkItem.Status.DONE,
        )
        assert _gather_workitems(client_identified)[0]["status"] == "Erledigt"

    def test_priority_uses_display_label(self, facility, client_identified, staff_user):
        """Mutation ``get_priority_display()`` → ``priority`` würde Slug
        liefern."""
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            priority=WorkItem.Priority.URGENT,
        )
        assert _gather_workitems(client_identified)[0]["priority"] == "Dringend"

    def test_priority_normal_label(self, sample_workitem):
        assert _gather_workitems(sample_workitem.client)[0]["priority"] == "Normal"

    def test_created_at_isoformat(self, sample_workitem):
        result = _gather_workitems(sample_workitem.client)
        assert isinstance(result[0]["created_at"], str)
        assert result[0]["created_at"] == sample_workitem.created_at.isoformat()

    def test_due_date_none_when_unset(self, sample_workitem):
        """Mutation ``... if wi.due_date else None`` → fester Wert würde
        bei fehlendem due_date crashen."""
        assert _gather_workitems(sample_workitem.client)[0]["due_date"] is None

    def test_due_date_isoformat_when_set(self, facility, client_identified, staff_user):
        due = timezone.now().date() + timedelta(days=7)
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            due_date=due,
        )
        result = _gather_workitems(client_identified)[0]
        assert result["due_date"] == due.isoformat()

    def test_excludes_workitems_of_other_clients(self, facility, client_identified, client_qualified, staff_user):
        """``filter(client=client)``-Boundary: Cross-Klient-Daten dürfen
        nicht in den Export leaken."""
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="Own",
        )
        WorkItem.objects.create(
            facility=facility,
            client=client_qualified,
            created_by=staff_user,
            title="Other",
        )
        result = _gather_workitems(client_identified)
        assert len(result) == 1
        assert result[0]["title"] == "Own"

    def test_order_descending_by_created_at(self, facility, client_identified, staff_user):
        """Mutation ``-created_at`` → ``created_at`` würde älteste zuerst
        liefern."""
        WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Erst")
        WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Zweite")
        WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Dritte")
        result = _gather_workitems(client_identified)
        titles = [r["title"] for r in result]
        assert titles == ["Dritte", "Zweite", "Erst"]
