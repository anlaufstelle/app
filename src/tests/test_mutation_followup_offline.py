"""Follow-Up-Tests für Mutation-Survivors in ``core.services.offline``.

Refs Welle 7 (#930). Ziel: Mutationen in ``build_client_offline_bundle``
und seinen Helfern (``_serialize_event``, ``_serialize_case``,
``_serialize_workitem``, ``_serialize_document_type``,
``_serialize_field_template``, ``_visible_data_fields``) killen.

Schwerpunkte (Survivor-Cluster):

- ``build_client_offline_bundle`` (33 Survivors): Slice ``[:50]``,
  90-Tage-Cutoff, Soft-Delete-Filter ``is_deleted=False``, Notes-Gate
  per ``is_staff_or_above``, WorkItem-Status-IN-Filter,
  TTL/Schema-Version, Client-Felder, DocumentType-Dedup, fester
  Order ``-occurred_at``/``-created_at``.
- ``_serialize_event``: jedes Feld einzeln (case_pk/episode_pk
  None-Handling, is_anonymous, created_by_display Fallback auf
  ``username``).
- ``_serialize_case``: closed_at None-Handling, lead_user-Display.
- ``_serialize_workitem``: due_date None-Handling.
- ``_serialize_document_type``: optional icon/color → "" Default,
  Sort-Order der Fields.
- ``_visible_data_fields``: ``__file__``/``__files__``-Marker,
  ``__encrypted__``-Branch via ``safe_decrypt``, Sensitivity-Strip.

Die Tests bauen Helper-Factories für die Felder, die in
``conftest.py`` nicht fertig vorhanden sind (FieldTemplate-mit-
spezifischer-Sensitivity, DocumentType mit überschriebenem
Sensitivity-Level, Event mit beliebigem ``data_json``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings
from django.utils import timezone

from core.models import (
    Case,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    FieldTemplate,
    WorkItem,
)
from core.services.encryption import encrypt_field
from core.services.offline import (
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_TTL_SECONDS,
    LOOKBACK_DAYS,
    MAX_EVENTS_PER_BUNDLE,
    _serialize_case,
    _serialize_document_type,
    _serialize_event,
    _serialize_field_template,
    _serialize_workitem,
    _visible_data_fields,
    build_client_offline_bundle,
)

# ---------------------------------------------------------------------------
# Helper-Factories
# ---------------------------------------------------------------------------


def _make_field_template(
    facility,
    *,
    name: str,
    field_type: str = FieldTemplate.FieldType.TEXT,
    sensitivity: str = "",
    is_encrypted: bool = False,
    options_json=None,
) -> FieldTemplate:
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        sensitivity=sensitivity,
        is_encrypted=is_encrypted,
        options_json=options_json or [],
    )


def _make_doc_type(
    facility,
    *,
    name: str = "DT",
    sensitivity: str = DocumentType.Sensitivity.NORMAL,
    icon: str = "",
    color: str = "",
) -> DocumentType:
    return DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        sensitivity=sensitivity,
        name=name,
        icon=icon,
        color=color,
    )


def _attach(doc_type: DocumentType, field_template: FieldTemplate, sort_order: int = 0) -> None:
    DocumentTypeField.objects.create(
        document_type=doc_type, field_template=field_template, sort_order=sort_order
    )


def _make_event(
    facility,
    client,
    doc_type,
    staff_user,
    *,
    data_json=None,
    occurred_at=None,
    case=None,
    episode=None,
    is_anonymous: bool = False,
    is_deleted: bool = False,
    created_by=None,
) -> Event:
    event = Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=occurred_at or timezone.now(),
        data_json=data_json or {},
        case=case,
        episode=episode,
        is_anonymous=is_anonymous,
        created_by=created_by if created_by is not None else staff_user,
    )
    if is_deleted:
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        event.refresh_from_db()
    return event


# ---------------------------------------------------------------------------
# build_client_offline_bundle — Top-Level-Aggregat (33 Survivors)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBundleEnvelope:
    """Schema-Version, TTL, generated_at/expires_at — Konstanten-Mutationen."""

    def test_schema_version_constant_is_set(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        # Mutation BUNDLE_SCHEMA_VERSION 1→2 oder Verschmelzung mit anderem Key.
        assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION == 1

    def test_ttl_is_48_hours_in_seconds(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        # Mutation 48*3600 → 24*3600 / Multiplikator weglassen.
        assert bundle["ttl"] == BUNDLE_TTL_SECONDS
        assert bundle["ttl"] == 48 * 3600

    def test_expires_at_is_generated_at_plus_ttl(
        self, facility, client_identified, staff_user
    ):
        """Mutation ``generated_at + timedelta(seconds=BUNDLE_TTL_SECONDS)``
        → ``-`` oder Vertauschung der Operanden würde den Abstand killen."""
        from datetime import datetime

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        generated = datetime.fromisoformat(bundle["generated_at"])
        expires = datetime.fromisoformat(bundle["expires_at"])
        delta = (expires - generated).total_seconds()
        # Toleranz fuer datetime-Aufloesung (zwei now()-Aufrufe sind nicht identisch
        # — wir lesen ``generated_at`` aus dem Bundle, das im Service als
        # ``generated_at`` gemerged wurde und sowohl in ``generated_at`` als
        # auch in ``expires_at`` benutzt wird).
        assert abs(delta - BUNDLE_TTL_SECONDS) < 1.0

    def test_top_level_keys_present(self, facility, client_identified, staff_user):
        """Mutation ``"events": [...]`` → key weglassen oder umbenennen."""
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        expected_keys = {
            "schema_version",
            "generated_at",
            "ttl",
            "expires_at",
            "client",
            "cases",
            "workitems",
            "events",
            "document_types",
        }
        assert set(bundle.keys()) == expected_keys


@pytest.mark.django_db
class TestBundleClientFields:
    """``bundle["client"]`` enthält genau die acht spezifizierten Felder."""

    def test_client_dict_contains_all_required_keys(
        self, facility, client_identified, staff_user
    ):
        """Mutation eines Feld-Keys (``pseudonym`` → ``pseudo`` etc.) wird
        gefangen, weil jeder Key explizit geprueft wird."""
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert set(bundle["client"].keys()) == {
            "pk",
            "pseudonym",
            "contact_stage",
            "contact_stage_display",
            "age_cluster",
            "age_cluster_display",
            "notes",
            "is_active",
        }

    def test_client_pk_is_stringified_uuid(self, facility, client_identified, staff_user):
        """Mutation ``str(client.pk)`` → ``client.pk`` würde UUID-Objekt liefern."""
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["pk"] == str(client_identified.pk)
        assert isinstance(bundle["client"]["pk"], str)

    def test_client_pseudonym_matches_source(
        self, facility, client_identified, staff_user
    ):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["pseudonym"] == client_identified.pseudonym

    def test_client_contact_stage_and_display_both_set(
        self, facility, client_qualified, staff_user
    ):
        """Mutation ``get_contact_stage_display()`` → ``contact_stage`` würde
        identische Werte liefern."""
        bundle = build_client_offline_bundle(staff_user, facility, client_qualified)
        assert bundle["client"]["contact_stage"] == "qualified"
        assert bundle["client"]["contact_stage_display"] == "Qualifiziert"
        assert bundle["client"]["contact_stage"] != bundle["client"]["contact_stage_display"]

    def test_client_age_cluster_and_display(self, facility, staff_user):
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="Age-1",
            age_cluster=Client.AgeCluster.AGE_18_26,
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, c)
        assert bundle["client"]["age_cluster"] == "18_26"
        assert bundle["client"]["age_cluster_display"] == "18–26"

    def test_client_is_active_passes_through(
        self, facility, client_identified, staff_user
    ):
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["is_active"] is False


@pytest.mark.django_db
class TestNotesVisibilityGate:
    """Notes-Gate: ``is_staff_or_above`` schaltet ``client.notes`` frei.

    Mutation der Negation, des Property-Namens oder des Fallback-``""``
    werden gefangen.
    """

    def test_assistant_sees_empty_notes(
        self, facility, client_identified, assistant_user
    ):
        client_identified.notes = "geheim"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        # Mutation ``"" → "geheim"`` würde Klartext leaken.
        assert bundle["client"]["notes"] == ""

    def test_staff_sees_full_notes(self, facility, client_identified, staff_user):
        client_identified.notes = "Aktennotiz"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["notes"] == "Aktennotiz"

    def test_lead_sees_notes(self, facility, client_identified, lead_user):
        client_identified.notes = "Lead-Sicht"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(lead_user, facility, client_identified)
        assert bundle["client"]["notes"] == "Lead-Sicht"

    def test_facility_admin_sees_notes(
        self, facility, client_identified, admin_user
    ):
        client_identified.notes = "Admin-Sicht"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(admin_user, facility, client_identified)
        assert bundle["client"]["notes"] == "Admin-Sicht"

    def test_user_without_property_falls_back_to_empty(
        self, facility, client_identified
    ):
        """Mutation ``hasattr(user, "is_staff_or_above")`` → ``True`` oder
        Removal des Fallbacks ``False`` würde den Branch killen."""

        class _StubUser:
            is_authenticated = True
            role = "assistant"
            pk = 999

        client_identified.notes = "wuerde-leaken"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(_StubUser(), facility, client_identified)
        # Ohne is_staff_or_above-Property → notes_visible False → leerer String.
        assert bundle["client"]["notes"] == ""


# ---------------------------------------------------------------------------
# Event-Filter (Slice / Cutoff / Soft-Delete / visible_to)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEventFilter:
    """Cutoff (90 Tage), Slice (50), is_deleted=False, visible_to(user)."""

    def test_slice_limits_events_to_max_per_bundle(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``[:50]`` → ``[:51]`` oder Removal wird gefangen."""
        for i in range(MAX_EVENTS_PER_BUNDLE + 3):
            _make_event(
                facility,
                client_identified,
                doc_type_contact,
                staff_user,
                occurred_at=timezone.now() - timedelta(minutes=i),
            )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert len(bundle["events"]) == MAX_EVENTS_PER_BUNDLE == 50

    def test_lookback_cutoff_excludes_old_events(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``timedelta(days=90)`` → 91/89 verschiebt den Cutoff —
        Event direkt jenseits 90 Tage muss draußen bleiben."""
        old = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.now() - timedelta(days=LOOKBACK_DAYS + 1),
        )
        new = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.now() - timedelta(days=1),
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(old.pk) not in pks
        assert str(new.pk) in pks

    def test_lookback_constant_is_90_days(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation der Konstante LOOKBACK_DAYS (90 → 89/91) verschoebe das
        Eligibility-Fenster — wir verifizieren, dass ein Event 89 Tage alt
        drin ist und 91 Tage alt draußen.

        ``occurred_at__gte`` schließt den Cutoff exklusiv älterer Events ein.
        """
        assert LOOKBACK_DAYS == 90
        ev_in = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.now() - timedelta(days=89),
        )
        ev_out = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.now() - timedelta(days=91),
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(ev_in.pk) in pks
        assert str(ev_out.pk) not in pks

    def test_soft_deleted_events_are_excluded(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``is_deleted=False`` → ``True`` würde nur Gelöschte zeigen."""
        alive = _make_event(facility, client_identified, doc_type_contact, staff_user)
        dead = _make_event(
            facility, client_identified, doc_type_contact, staff_user, is_deleted=True
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(alive.pk) in pks
        assert str(dead.pk) not in pks

    def test_events_ordered_descending_by_occurred_at(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``order_by("-occurred_at")`` → ``order_by("occurred_at")``
        oder Negation würde DESC kippen."""
        old = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.now() - timedelta(days=10),
        )
        mid = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.now() - timedelta(days=5),
        )
        new = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.now() - timedelta(days=1),
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = [e["pk"] for e in bundle["events"]]
        assert pks == [str(new.pk), str(mid.pk), str(old.pk)]

    def test_events_for_other_client_excluded(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``filter(client=client)`` → kein client-Filter würde alle
        Facility-Events leaken."""
        from core.models import Client

        other = Client.objects.create(
            facility=facility,
            pseudonym="OTHER",
            created_by=staff_user,
        )
        own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        foreign = _make_event(facility, other, doc_type_contact, staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(own.pk) in pks
        assert str(foreign.pk) not in pks

    def test_events_from_other_facility_excluded(
        self, facility, client_identified, doc_type_contact, staff_user, other_facility
    ):
        """Mutation ``for_facility(facility)`` → kein Facility-Filter würde
        Cross-Tenant-Leak provozieren."""
        own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        # Doc-Type in der anderen Facility und Event darauf — derselbe Client
        # passt nicht, also wir nehmen einen Client der anderen Facility.
        from core.models import Client

        other_client = Client.objects.create(
            facility=other_facility, pseudonym="OF-1", created_by=staff_user
        )
        other_dt = _make_doc_type(other_facility, name="Other DT")
        _make_event(other_facility, other_client, other_dt, staff_user)

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(own.pk) in pks
        assert len(bundle["events"]) == 1

    def test_high_sensitivity_event_hidden_from_staff(
        self, facility, client_identified, staff_user
    ):
        """visible_to(user) muss greifen — STAFF sieht HIGH-DocumentType-Event
        nicht. Mutation ``visible_to(user)`` → ``all()`` würde es leaken.
        """
        dt = _make_doc_type(
            facility, name="HIGH-DT", sensitivity=DocumentType.Sensitivity.HIGH
        )
        ev = _make_event(facility, client_identified, dt, staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(ev.pk) not in pks


# ---------------------------------------------------------------------------
# DocumentTypes-Aggregat — Dedup und korrekte Felder
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentTypesAggregate:
    """Refs Welle 7 — ``doc_types`` dedup + Felder-Serialisierung."""

    def test_document_types_deduped_across_events(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation des ``if ev.document_type_id not in doc_types:``-Branch
        würde doppelte DocumentType-Einträge produzieren."""
        for _ in range(3):
            _make_event(facility, client_identified, doc_type_contact, staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        dt_pks = [dt["pk"] for dt in bundle["document_types"]]
        assert len(dt_pks) == 1
        assert dt_pks == [str(doc_type_contact.pk)]

    def test_document_types_multiple_distinct(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        dt2 = _make_doc_type(facility, name="Zweiter DT")
        ft = _make_field_template(facility, name="F")
        _attach(dt2, ft)
        _make_event(facility, client_identified, doc_type_contact, staff_user)
        _make_event(facility, client_identified, dt2, staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        dt_pks = {dt["pk"] for dt in bundle["document_types"]}
        assert dt_pks == {str(doc_type_contact.pk), str(dt2.pk)}

    def test_document_types_empty_when_no_events(
        self, facility, client_identified, staff_user
    ):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["document_types"] == []


# ---------------------------------------------------------------------------
# WorkItem-Filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkitemFilter:
    """Status-IN-Filter (OPEN, IN_PROGRESS) + Order DESC + Facility-Scope."""

    def test_open_workitem_included(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="Open-WI",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {w["pk"] for w in bundle["workitems"]}
        assert str(wi.pk) in pks

    def test_in_progress_workitem_included(
        self, facility, client_identified, staff_user
    ):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.IN_PROGRESS,
            title="WIP-WI",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {w["pk"] for w in bundle["workitems"]}
        assert str(wi.pk) in pks

    def test_done_workitem_excluded(self, facility, client_identified, staff_user):
        """Mutation des Status-IN-Filters (DONE hinzunehmen) würde diesen
        Eintrag mit reinschmuggeln."""
        done = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.DONE,
            title="Done-WI",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {w["pk"] for w in bundle["workitems"]}
        assert str(done.pk) not in pks

    def test_dismissed_workitem_excluded(
        self, facility, client_identified, staff_user
    ):
        dismissed = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.DISMISSED,
            title="Verworfen",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {w["pk"] for w in bundle["workitems"]}
        assert str(dismissed.pk) not in pks

    def test_workitems_for_other_client_excluded(
        self, facility, client_identified, staff_user
    ):
        from core.models import Client

        other = Client.objects.create(
            facility=facility, pseudonym="WIP-OTHER", created_by=staff_user
        )
        own = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="own",
        )
        WorkItem.objects.create(
            facility=facility,
            client=other,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="foreign",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {w["pk"] for w in bundle["workitems"]}
        assert pks == {str(own.pk)}

    def test_workitems_from_other_facility_excluded(
        self, facility, client_identified, staff_user, other_facility
    ):
        from core.models import Client

        own = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="own",
        )
        other_client = Client.objects.create(
            facility=other_facility, pseudonym="OF-WIP", created_by=staff_user
        )
        WorkItem.objects.create(
            facility=other_facility,
            client=other_client,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="foreign",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {w["pk"] for w in bundle["workitems"]}
        assert pks == {str(own.pk)}

    def test_workitems_order_descending_by_created_at(
        self, facility, client_identified, staff_user
    ):
        """Mutation ``order_by("-created_at")`` → ``"created_at"`` würde
        ASC liefern."""
        first = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="first",
        )
        second = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="second",
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = [w["pk"] for w in bundle["workitems"]]
        assert pks[0] == str(second.pk)
        assert pks[-1] == str(first.pk)


# ---------------------------------------------------------------------------
# Cases-Filter (kein Status-Filter, ABER Facility + Client)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCasesFilter:
    """Cases werden NICHT nach Status gefiltert — closed UND open beide drin."""

    def test_open_case_included(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="open",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {x["pk"] for x in bundle["cases"]}
        assert str(c.pk) in pks

    def test_closed_case_also_included(self, facility, client_identified, staff_user):
        """Mutation ``filter(client=client)`` → ``filter(client=client, status=OPEN)``
        würde geschlossene Fälle verstecken — wir lassen einen geschlossenen
        Fall sichtbar bleiben.
        """
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="closed",
            status=Case.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {x["pk"] for x in bundle["cases"]}
        assert str(c.pk) in pks

    def test_cases_for_other_client_excluded(
        self, facility, client_identified, staff_user
    ):
        from core.models import Client

        other = Client.objects.create(
            facility=facility, pseudonym="CASE-OTHER", created_by=staff_user
        )
        own = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="own",
            created_by=staff_user,
        )
        Case.objects.create(
            facility=facility, client=other, title="foreign", created_by=staff_user
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {x["pk"] for x in bundle["cases"]}
        assert pks == {str(own.pk)}

    def test_cases_order_descending_by_created_at(
        self, facility, client_identified, staff_user
    ):
        first = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="alpha",
            created_by=staff_user,
        )
        second = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="beta",
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = [x["pk"] for x in bundle["cases"]]
        assert pks[0] == str(second.pk)
        assert pks[-1] == str(first.pk)


# ---------------------------------------------------------------------------
# _serialize_event — Felder einzeln verifizieren (Mutmut killt einzelne Keys)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeEvent:
    """Pro Feld ein Test — Mutmut entfernt typischerweise einzelne Key/Value-
    Paare und das fällt nur auf, wenn jedes Feld einzeln verifiziert ist."""

    def test_pk_field_is_stringified(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["pk"] == str(event.pk)

    def test_occurred_at_field_is_isoformat(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        when = timezone.now()
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=when,
        )
        out = _serialize_event(staff_user, event)
        # Mutation ``isoformat()`` → ``__str__()`` oder Removal würde failen.
        assert out["occurred_at"] == when.isoformat()

    def test_document_type_pk_is_stringified(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["document_type_pk"] == str(doc_type_contact.pk)

    def test_document_type_name_is_set(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["document_type_name"] == doc_type_contact.name

    def test_created_by_display_uses_full_name_when_set(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        staff_user.first_name = "Max"
        staff_user.last_name = "Muster"
        staff_user.save()
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["created_by_display"] == "Max Muster"

    def test_created_by_display_falls_back_to_username(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``or event.created_by.username`` → leerer String würde failen.

        ``staff_user`` hat per Fixture keinen full_name → Fallback greift.
        """
        assert staff_user.get_full_name() == ""  # Vorbedingung
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["created_by_display"] == staff_user.username

    def test_created_by_display_empty_when_user_none(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation des ``if event.created_by else ""``-Fallbacks: None-User
        muss leeren String liefern, nicht AttributeError."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            created_by=staff_user,
        )
        # Setze created_by nach Erstellung auf None (Direct-Update damit save()
        # nicht reset).
        Event.objects.filter(pk=event.pk).update(created_by=None)
        event.refresh_from_db()
        out = _serialize_event(staff_user, event)
        assert out["created_by_display"] == ""

    def test_case_pk_is_none_when_no_case(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        # Mutation ``None`` → ``""`` würde diese Variante killen.
        assert out["case_pk"] is None

    def test_case_pk_set_when_event_has_case(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Case",
            created_by=staff_user,
        )
        event = _make_event(
            facility, client_identified, doc_type_contact, staff_user, case=case
        )
        out = _serialize_event(staff_user, event)
        assert out["case_pk"] == str(case.pk)

    def test_episode_pk_is_none_when_no_episode(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["episode_pk"] is None

    def test_episode_pk_set_when_event_has_episode(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="C",
            created_by=staff_user,
        )
        episode = Episode.objects.create(
            case=case,
            title="Ep",
            started_at=timezone.now().date(),
            created_by=staff_user,
        )
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            case=case,
            episode=episode,
        )
        out = _serialize_event(staff_user, event)
        assert out["episode_pk"] == str(episode.pk)

    def test_is_anonymous_passes_through_true(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            is_anonymous=True,
        )
        out = _serialize_event(staff_user, event)
        assert out["is_anonymous"] is True

    def test_is_anonymous_default_false(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["is_anonymous"] is False

    def test_data_fields_key_exists_even_for_empty_data(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        # data_fields immer dict — Mutation ``[]`` → ``None`` würde scheitern.
        assert out["data_fields"] == {}
        assert isinstance(out["data_fields"], dict)

    def test_serialized_keys_complete(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation eines Key-Strings (``case_pk`` → ``casepk``) wird durch
        Vergleich der erwarteten Schluesselmenge gefangen."""
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert set(out.keys()) == {
            "pk",
            "occurred_at",
            "document_type_pk",
            "document_type_name",
            "created_by_display",
            "case_pk",
            "episode_pk",
            "is_anonymous",
            "data_fields",
        }


# ---------------------------------------------------------------------------
# _serialize_case — pro Feld
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeCase:
    def test_pk_stringified(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["pk"] == str(c.pk)

    def test_title_and_description_passthrough(
        self, facility, client_identified, staff_user
    ):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Mein Fall",
            description="Beschreibung X",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["title"] == "Mein Fall"
        assert out["description"] == "Beschreibung X"

    def test_status_and_display_both_set(
        self, facility, client_identified, staff_user
    ):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            status=Case.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["status"] == "closed"
        assert out["status_display"] == "Geschlossen"

    def test_created_at_is_isoformat(
        self, facility, client_identified, staff_user
    ):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["created_at"] == c.created_at.isoformat()

    def test_closed_at_none_for_open_case(
        self, facility, client_identified, staff_user
    ):
        """Mutation ``case.closed_at.isoformat() if case.closed_at else None``
        → leerer String wuerde failen."""
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["closed_at"] is None

    def test_closed_at_isoformat_when_set(
        self, facility, client_identified, staff_user
    ):
        when = timezone.now()
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            status=Case.Status.CLOSED,
            closed_at=when,
            created_by=staff_user,
        )
        out = _serialize_case(c)
        # closed_at wird in DB ggf. mit µs-Aufloesung gespeichert → über das
        # Modell lesen statt direkt ``when.isoformat()`` vergleichen.
        c.refresh_from_db()
        assert out["closed_at"] == c.closed_at.isoformat()

    def test_lead_user_display_empty_when_none(
        self, facility, client_identified, staff_user
    ):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
            lead_user=None,
        )
        out = _serialize_case(c)
        assert out["lead_user_display"] == ""

    def test_lead_user_display_falls_back_to_username(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Mutation ``or lead_user.username``-Fallback: Lead ohne full_name
        muss username liefern."""
        assert lead_user.get_full_name() == ""
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
            lead_user=lead_user,
        )
        out = _serialize_case(c)
        assert out["lead_user_display"] == lead_user.username

    def test_lead_user_display_uses_full_name(
        self, facility, client_identified, staff_user, lead_user
    ):
        lead_user.first_name = "Lea"
        lead_user.last_name = "Direktorin"
        lead_user.save()
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
            lead_user=lead_user,
        )
        out = _serialize_case(c)
        assert out["lead_user_display"] == "Lea Direktorin"

    def test_case_serialized_keys_complete(
        self, facility, client_identified, staff_user
    ):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert set(out.keys()) == {
            "pk",
            "title",
            "description",
            "status",
            "status_display",
            "created_at",
            "closed_at",
            "lead_user_display",
        }


# ---------------------------------------------------------------------------
# _serialize_workitem — pro Feld
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeWorkitem:
    def test_workitem_all_keys_present(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.IMPORTANT,
            title="T",
            description="D",
        )
        out = _serialize_workitem(wi)
        assert set(out.keys()) == {
            "pk",
            "title",
            "description",
            "status",
            "priority",
            "item_type",
            "due_date",
        }

    def test_workitem_pk_stringified(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
        )
        assert _serialize_workitem(wi)["pk"] == str(wi.pk)

    def test_workitem_title_and_description(
        self, facility, client_identified, staff_user
    ):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="MyTitle",
            description="MyDesc",
        )
        out = _serialize_workitem(wi)
        assert out["title"] == "MyTitle"
        assert out["description"] == "MyDesc"

    def test_workitem_status_passthrough(
        self, facility, client_identified, staff_user
    ):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            status=WorkItem.Status.IN_PROGRESS,
        )
        out = _serialize_workitem(wi)
        # Mutation ``workitem.status`` → ``workitem.get_status_display()``
        # würde "In Bearbeitung" liefern.
        assert out["status"] == "in_progress"

    def test_workitem_priority_passthrough(
        self, facility, client_identified, staff_user
    ):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            priority=WorkItem.Priority.URGENT,
        )
        out = _serialize_workitem(wi)
        assert out["priority"] == "urgent"

    def test_workitem_item_type_passthrough(
        self, facility, client_identified, staff_user
    ):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            item_type=WorkItem.ItemType.HINT,
        )
        out = _serialize_workitem(wi)
        assert out["item_type"] == "hint"

    def test_workitem_due_date_none(self, facility, client_identified, staff_user):
        """Mutation ``workitem.due_date.isoformat() if workitem.due_date else None``
        → leerer String würde failen."""
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            due_date=None,
        )
        out = _serialize_workitem(wi)
        assert out["due_date"] is None

    def test_workitem_due_date_isoformat_when_set(
        self, facility, client_identified, staff_user
    ):
        when = timezone.now().date()
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            due_date=when,
        )
        out = _serialize_workitem(wi)
        assert out["due_date"] == when.isoformat()


# ---------------------------------------------------------------------------
# _serialize_document_type / _serialize_field_template
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeDocumentType:
    def test_dt_keys_complete(self, facility):
        dt = _make_doc_type(facility, name="DT", icon="ico", color="red")
        out = _serialize_document_type(dt)
        assert set(out.keys()) == {
            "pk",
            "name",
            "category",
            "sensitivity",
            "icon",
            "color",
            "fields",
        }

    def test_dt_pk_stringified(self, facility):
        dt = _make_doc_type(facility, name="X")
        assert _serialize_document_type(dt)["pk"] == str(dt.pk)

    def test_dt_name_and_category(self, facility):
        dt = _make_doc_type(facility, name="N")
        out = _serialize_document_type(dt)
        assert out["name"] == "N"
        assert out["category"] == "contact"

    def test_dt_sensitivity_passes_through_value_not_display(self, facility):
        """Mutation ``doc_type.sensitivity`` → ``.get_sensitivity_display()``
        wuerde "Hoch" statt "high" liefern."""
        dt = _make_doc_type(
            facility, name="HD", sensitivity=DocumentType.Sensitivity.HIGH
        )
        out = _serialize_document_type(dt)
        assert out["sensitivity"] == "high"

    def test_dt_icon_empty_default(self, facility):
        """Mutation ``dt.icon or ""`` → ``dt.icon`` wuerde None oder ""
        durchreichen — wir prueffen explizit "" bei leerem icon."""
        dt = _make_doc_type(facility, name="NoIcon", icon="")
        assert _serialize_document_type(dt)["icon"] == ""

    def test_dt_color_empty_default(self, facility):
        dt = _make_doc_type(facility, name="NoColor", color="")
        assert _serialize_document_type(dt)["color"] == ""

    def test_dt_icon_passthrough(self, facility):
        dt = _make_doc_type(facility, name="Ico", icon="bi-bell")
        assert _serialize_document_type(dt)["icon"] == "bi-bell"

    def test_dt_color_passthrough(self, facility):
        dt = _make_doc_type(facility, name="Col", color="#abc")
        assert _serialize_document_type(dt)["color"] == "#abc"

    def test_dt_fields_sorted_by_sort_order(self, facility):
        """Mutation ``order_by("sort_order")`` → unsortiert wuerde Reihenfolge
        kippen, wenn man die Eintraege in umgekehrter Order erstellt."""
        dt = _make_doc_type(facility, name="Ord")
        ft_b = _make_field_template(facility, name="B")
        ft_a = _make_field_template(facility, name="A")
        # B mit sort_order=0 EINGETRAGEN, A mit sort_order=1 — erwartet:
        # Reihenfolge im Output ist [B, A].
        _attach(dt, ft_b, sort_order=0)
        _attach(dt, ft_a, sort_order=1)
        out = _serialize_document_type(dt)
        slugs = [f["slug"] for f in out["fields"]]
        assert slugs == [ft_b.slug, ft_a.slug]

    def test_dt_fields_empty_list_when_no_fields(self, facility):
        dt = _make_doc_type(facility, name="Empty")
        assert _serialize_document_type(dt)["fields"] == []


class TestSerializeFieldTemplate:
    """``_serialize_field_template`` ist DB-frei testbar via SimpleNamespace."""

    def _make_ft_stub(
        self,
        *,
        slug: str = "s",
        name: str = "N",
        field_type: str = "text",
        sensitivity: str = "",
        is_encrypted: bool = False,
    ):
        from types import SimpleNamespace

        return SimpleNamespace(
            slug=slug,
            name=name,
            field_type=field_type,
            sensitivity=sensitivity,
            is_encrypted=is_encrypted,
        )

    def test_keys_complete(self):
        out = _serialize_field_template(self._make_ft_stub())
        assert set(out.keys()) == {
            "slug",
            "name",
            "field_type",
            "sensitivity",
            "is_encrypted",
        }

    def test_sensitivity_none_becomes_empty_string(self):
        """Mutation ``field_template.sensitivity or ""`` → ``.sensitivity``
        wuerde None durchreichen."""
        out = _serialize_field_template(self._make_ft_stub(sensitivity=None))
        assert out["sensitivity"] == ""

    def test_sensitivity_passthrough_when_set(self):
        out = _serialize_field_template(self._make_ft_stub(sensitivity="high"))
        assert out["sensitivity"] == "high"

    def test_is_encrypted_passthrough_true(self):
        out = _serialize_field_template(self._make_ft_stub(is_encrypted=True))
        assert out["is_encrypted"] is True

    def test_is_encrypted_passthrough_false(self):
        out = _serialize_field_template(self._make_ft_stub(is_encrypted=False))
        assert out["is_encrypted"] is False

    def test_name_and_slug_and_field_type(self):
        out = _serialize_field_template(
            self._make_ft_stub(slug="my-slug", name="My", field_type="number")
        )
        assert out["slug"] == "my-slug"
        assert out["name"] == "My"
        assert out["field_type"] == "number"


# ---------------------------------------------------------------------------
# _visible_data_fields — Marker und Encrypt-Branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVisibleDataFields:
    """``_visible_data_fields`` ist der zentrale Filter-Helper.

    Schwerpunkte: leeres ``data_json``, sensitivity-Strip, single-/multi-file
    Marker, ``__encrypted__``-Branch via ``safe_decrypt``.
    """

    def test_empty_data_returns_empty_dict(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(
            facility, client_identified, doc_type_contact, staff_user, data_json={}
        )
        assert _visible_data_fields(staff_user, event) == {}

    def test_plain_value_kept_for_visible_field(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": "Hallo"},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == "Hallo"

    def test_field_stripped_when_user_cannot_see(
        self, facility, client_identified, staff_user, assistant_user
    ):
        dt = _make_doc_type(
            facility, name="HD", sensitivity=DocumentType.Sensitivity.HIGH
        )
        ft = _make_field_template(
            facility, name="X", sensitivity="high", is_encrypted=True
        )
        _attach(dt, ft)
        event = _make_event(
            facility,
            client_identified,
            dt,
            staff_user,
            data_json={ft.slug: "secret"},
        )
        # Re-fetch fuer Encryption-Marker
        event.refresh_from_db()
        result = _visible_data_fields(assistant_user, event)
        # Assistant sieht weder HIGH doc noch HIGH field — Strip greift.
        assert ft.slug not in result

    def test_single_file_marker_keeps_name_only(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``"name": value.get("name", "")`` → ``"id"`` wuerde id leaken."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={
                "notiz": {
                    "__file__": True,
                    "name": "report.pdf",
                    "attachment_id": "secret-id",
                    "content_type": "application/pdf",
                }
            },
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__file__": True, "name": "report.pdf"}
        # Defensive: keine internen IDs leaken.
        import json

        assert "secret-id" not in json.dumps(result)

    def test_single_file_marker_missing_name_uses_empty_string(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``value.get("name", "")`` → ``value.get("name")``
        wuerde None liefern."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": {"__file__": True}},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__file__": True, "name": ""}

    def test_multi_file_marker_returns_count_only(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Refs #786: ``__files__``-Branch reduziert auf count, KEINE entries/IDs."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={
                "notiz": {
                    "__files__": True,
                    "entries": [
                        {"id": "aaa", "sort": 0},
                        {"id": "bbb", "sort": 1},
                    ],
                }
            },
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__files__": True, "count": 2}
        import json

        # Keine internen IDs, kein "entries"-Key.
        body = json.dumps(result)
        assert "aaa" not in body
        assert "bbb" not in body
        assert "entries" not in body

    def test_multi_file_marker_counts_only_entries_with_id(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``sum(1 for e in entries if isinstance(e, dict) and e.get("id"))``
        → ``len(entries)`` wuerde auch Eintraege ohne ID mitzaehlen."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={
                "notiz": {
                    "__files__": True,
                    "entries": [
                        {"id": "a"},
                        {},  # ohne id
                        "garbage",  # nicht dict
                        {"id": "b"},
                    ],
                }
            },
        )
        result = _visible_data_fields(staff_user, event)
        # Nur 2 echte Entries mit id → count = 2.
        assert result["notiz"] == {"__files__": True, "count": 2}

    def test_multi_file_marker_empty_entries_count_zero(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": {"__files__": True, "entries": []}},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__files__": True, "count": 0}

    def test_multi_file_marker_missing_entries_defaults_to_empty(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``value.get("entries") or []`` → ``value.get("entries")``
        wuerde None liefern, was beim sum() crashen würde."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": {"__files__": True}},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__files__": True, "count": 0}

    def test_encrypted_value_is_decrypted(
        self, facility, client_identified, staff_user
    ):
        """``__encrypted__``-Branch: ``safe_decrypt`` muss aufgerufen werden.

        Mit gueltigem ENCRYPTION_KEY laeuft decrypt durch — Klartext landet
        im Bundle. Mutation des Branchs wuerde stattdessen Marker-Dict oder
        Default leaken.
        """
        key = Fernet.generate_key().decode("utf-8")
        with override_settings(ENCRYPTION_KEY=key, ENCRYPTION_KEYS=""):
            dt = _make_doc_type(facility, name="Enc")
            ft = _make_field_template(
                facility,
                name="EncField",
                # Sensitivity leer lassen, damit STAFF sehen darf.
                is_encrypted=True,
            )
            _attach(dt, ft)
            encrypted_marker = encrypt_field("Geheim123")
            # Direkt das encrypted dict in data_json schreiben — Event.save()
            # encryptet nicht doppelt, weil is_encrypted_value() True ist.
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=dt,
                occurred_at=timezone.now(),
                data_json={ft.slug: encrypted_marker},
                created_by=staff_user,
            )
            event.refresh_from_db()
            result = _visible_data_fields(staff_user, event)
            assert result[ft.slug] == "Geheim123"

    def test_dict_value_without_encryption_marker_passes_through(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """``safe_decrypt`` liefert das Dict 1:1 zurueck, wenn kein
        ``__encrypted__``-Marker dran ist — Mutation der ``else``-Klausel
        wird gefangen."""
        payload = {"a": 1, "b": "x"}
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": payload},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == payload

    def test_non_dict_value_passes_through_for_visible_field(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """List/int/str/None werden 1:1 durchgereicht — keiner der dict-
        Branches greift, der ``else``-Pfad sichtbar."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": ["a", "b", "c"]},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == ["a", "b", "c"]
