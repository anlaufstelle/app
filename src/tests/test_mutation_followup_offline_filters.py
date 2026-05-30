"""Mutation-Followup-Tests für ``core.services.offline`` — Filter und Aggregate.

Refs #930. Sub-File aus ``test_mutation_followup_offline``;
enthält die Test-Klassen ``TestEventFilter``, ``TestDocumentTypesAggregate``,
``TestWorkitemFilter`` und ``TestCasesFilter`` — also alle Cutoff-/Slice-/
Soft-Delete-/Visibility-Filter sowie die DocumentType-Dedup-Logik.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import (
    Case,
    DocumentType,
    WorkItem,
)
from core.services.system import (
    LOOKBACK_DAYS,
    MAX_EVENTS_PER_BUNDLE,
    build_client_offline_bundle,
)
from tests._mutation_followup_offline_helpers import (
    _attach,
    _make_doc_type,
    _make_event,
    _make_field_template,
)

# ---------------------------------------------------------------------------
# Event-Filter (Slice / Cutoff / Soft-Delete / visible_to)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEventFilter:
    """Cutoff (90 Tage), Slice (50), is_deleted=False, visible_to(user)."""

    def test_slice_limits_events_to_max_per_bundle(self, facility, client_identified, doc_type_contact, staff_user):
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

    def test_lookback_cutoff_excludes_old_events(self, facility, client_identified, doc_type_contact, staff_user):
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

    def test_soft_deleted_events_are_excluded(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``is_deleted=False`` → ``True`` würde nur Gelöschte zeigen."""
        alive = _make_event(facility, client_identified, doc_type_contact, staff_user)
        dead = _make_event(facility, client_identified, doc_type_contact, staff_user, is_deleted=True)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(alive.pk) in pks
        assert str(dead.pk) not in pks

    def test_events_ordered_descending_by_occurred_at(self, facility, client_identified, doc_type_contact, staff_user):
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

    def test_events_for_other_client_excluded(self, facility, client_identified, doc_type_contact, staff_user):
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

        other_client = Client.objects.create(facility=other_facility, pseudonym="OF-1", created_by=staff_user)
        other_dt = _make_doc_type(other_facility, name="Other DT")
        _make_event(other_facility, other_client, other_dt, staff_user)

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(own.pk) in pks
        assert len(bundle["events"]) == 1

    def test_high_sensitivity_event_hidden_from_staff(self, facility, client_identified, staff_user):
        """visible_to(user) muss greifen — STAFF sieht HIGH-DocumentType-Event
        nicht. Mutation ``visible_to(user)`` → ``all()`` würde es leaken.
        """
        dt = _make_doc_type(facility, name="HIGH-DT", sensitivity=DocumentType.Sensitivity.HIGH)
        ev = _make_event(facility, client_identified, dt, staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {e["pk"] for e in bundle["events"]}
        assert str(ev.pk) not in pks


# ---------------------------------------------------------------------------
# DocumentTypes-Aggregat — Dedup und korrekte Felder
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentTypesAggregate:
    """Refs ``doc_types`` dedup + Felder-Serialisierung."""

    def test_document_types_deduped_across_events(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation des ``if ev.document_type_id not in doc_types:``-Branch
        würde doppelte DocumentType-Einträge produzieren."""
        for _ in range(3):
            _make_event(facility, client_identified, doc_type_contact, staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        dt_pks = [dt["pk"] for dt in bundle["document_types"]]
        assert len(dt_pks) == 1
        assert dt_pks == [str(doc_type_contact.pk)]

    def test_document_types_multiple_distinct(self, facility, client_identified, doc_type_contact, staff_user):
        dt2 = _make_doc_type(facility, name="Zweiter DT")
        ft = _make_field_template(facility, name="F")
        _attach(dt2, ft)
        _make_event(facility, client_identified, doc_type_contact, staff_user)
        _make_event(facility, client_identified, dt2, staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        dt_pks = {dt["pk"] for dt in bundle["document_types"]}
        assert dt_pks == {str(doc_type_contact.pk), str(dt2.pk)}

    def test_document_types_empty_when_no_events(self, facility, client_identified, staff_user):
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

    def test_in_progress_workitem_included(self, facility, client_identified, staff_user):
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

    def test_dismissed_workitem_excluded(self, facility, client_identified, staff_user):
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

    def test_workitems_for_other_client_excluded(self, facility, client_identified, staff_user):
        from core.models import Client

        other = Client.objects.create(facility=facility, pseudonym="WIP-OTHER", created_by=staff_user)
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

    def test_workitems_from_other_facility_excluded(self, facility, client_identified, staff_user, other_facility):
        from core.models import Client

        own = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            title="own",
        )
        other_client = Client.objects.create(facility=other_facility, pseudonym="OF-WIP", created_by=staff_user)
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

    def test_workitems_order_descending_by_created_at(self, facility, client_identified, staff_user):
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

    def test_cases_for_other_client_excluded(self, facility, client_identified, staff_user):
        from core.models import Client

        other = Client.objects.create(facility=facility, pseudonym="CASE-OTHER", created_by=staff_user)
        own = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="own",
            created_by=staff_user,
        )
        Case.objects.create(facility=facility, client=other, title="foreign", created_by=staff_user)
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        pks = {x["pk"] for x in bundle["cases"]}
        assert pks == {str(own.pk)}

    def test_cases_order_descending_by_created_at(self, facility, client_identified, staff_user):
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
