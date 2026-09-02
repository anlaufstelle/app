"""Facility-Scoping der Sichtbarkeits-Subquery im Zeitstrom (Refs #2028).

``_feed_activities`` blendet Activities aus, deren Ziel ein fuer die Nutzerin
unsichtbares ``Event`` ist (Refs #562). Der dafuer verwendete NOT-IN-Subselect
war bis #2028 ausschliesslich nach Sensitivity gefiltert — ohne
``facility``-Praedikat lief er ueber ``core_event`` **aller** Einrichtungen.

Das ist kein Sichtbarkeitsfehler (``Event``-PKs sind global eindeutig, und ein
``Activity.target_id`` derselben Einrichtung zeigt nie auf ein Event einer
anderen), sondern ein Query-Form-Problem: die Eingabemenge des Subselects
waechst mit der Gesamtzahl aller Einrichtungen statt mit der eigenen. Die
Ergebnis-Tests unten pinnen genau das — die Trefferliste bleibt gleich.
"""

import re
import uuid
from datetime import datetime, time

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, Event
from core.services.dashboard import log_activity
from core.services.events import build_feed_items

# Der Activity-Zweig ist die einzige Query, die ``core_activity`` liest und
# ``core_event`` nur im Subselect erwaehnt.
ACTIVITY_QUERY_MARKER = 'FROM "core_activity"'
EVENT_TABLE_MARKER = 'FROM "core_event"'
FACILITY_PREDICATE_RE = re.compile(r'"facility_id"\s*=\s*\'([0-9a-fA-F-]+)\'')


def _balanced_subselects(sql):
    """Alle ``(SELECT ...)``-Gruppen der Query, klammer-balanciert extrahiert.

    Ohne Balancierung laesst sich der ``core_event``-Subselect nicht von dem
    daneben stehenden ``core_case``-Subselect trennen — beide tragen ein
    ``facility_id``-Praedikat auf dem Alias ``U0``.
    """
    out = []
    for match in re.finditer(r"\(SELECT ", sql):
        depth = 0
        for pos in range(match.start(), len(sql)):
            if sql[pos] == "(":
                depth += 1
            elif sql[pos] == ")":
                depth -= 1
                if depth == 0:
                    out.append(sql[match.start() : pos + 1])
                    break
    return out


def _event_visibility_subselect(sql):
    candidates = [s for s in _balanced_subselects(sql) if EVENT_TABLE_MARKER in s]
    assert len(candidates) == 1, f"Erwartet genau einen core_event-Subselect, gefunden {len(candidates)}: {sql}"
    return candidates[0]


def _create_event_with_activity(facility, doc_type, user, when, note):
    from core.models import Activity

    event = Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=when,
        data_json={"note": note},
        created_by=user,
    )
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=event,
        action=Activity.Action.EVENT_CREATED,
    )
    return event


def _activity_feed_sql(facility, target_date, user):
    """Fuehre den Activity-Zweig aus und liefere das SQL mit dem Event-Subselect."""
    with CaptureQueriesContext(connection) as ctx:
        build_feed_items(facility, target_date, feed_type="activities", user=user)
    matching = [
        q["sql"] for q in ctx.captured_queries if ACTIVITY_QUERY_MARKER in q["sql"] and EVENT_TABLE_MARKER in q["sql"]
    ]
    assert matching, (
        "Keine Activity-Query mit core_event-Subselect gefunden — "
        f"erfasste Queries: {[q['sql'][:120] for q in ctx.captured_queries]}"
    )
    return matching[0]


@pytest.mark.django_db
class TestActivityVisibilitySubqueryFacilityScope:
    """Der NOT-IN-Subselect auf ``core_event`` traegt ein facility-Praedikat (#2028)."""

    def test_subquery_carries_facility_predicate(self, facility, staff_user, doc_type_contact):
        today = timezone.localdate()
        when = timezone.make_aware(datetime.combine(today, time(12, 0)))
        _create_event_with_activity(facility, doc_type_contact, staff_user, when, "eigene")

        subselect = _event_visibility_subselect(_activity_feed_sql(facility, today, staff_user))

        assert FACILITY_PREDICATE_RE.search(subselect), (
            "Die Sichtbarkeits-Subquery auf core_event laeuft ohne facility-Praedikat "
            "und damit ueber die Events aller Einrichtungen (#2028). Subselect: " + subselect
        )

    def test_subquery_binds_the_current_facility_id(self, facility, staff_user, doc_type_contact, second_facility):
        """Das Praedikat bindet die aktuelle Einrichtung, nicht irgendeine."""
        today = timezone.localdate()
        when = timezone.make_aware(datetime.combine(today, time(12, 0)))
        _create_event_with_activity(facility, doc_type_contact, staff_user, when, "eigene")

        subselect = _event_visibility_subselect(_activity_feed_sql(facility, today, staff_user))

        match = FACILITY_PREDICATE_RE.search(subselect)
        assert match, f"Kein gebundenes facility_id-Praedikat im Subselect: {subselect}"
        assert uuid.UUID(match.group(1)) == facility.pk
        assert facility.pk != second_facility.pk


@pytest.mark.django_db
class TestActivityFeedResultUnchangedByFacilityScope:
    """Regression: das Ergebnis bleibt identisch — #2028 ist Perf, kein Leak."""

    def test_hidden_event_activity_still_excluded_with_second_facility_present(
        self, facility, staff_user, second_facility, second_facility_user
    ):
        today = timezone.localdate()
        when = timezone.make_aware(datetime.combine(today, time(12, 0)))

        dt_normal = DocumentType.objects.create(
            facility=facility,
            name="Kontakt (sichtbar)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        dt_high = DocumentType.objects.create(
            facility=facility,
            name="Vertraulich",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        # Fremde Einrichtung mit eigenen Events — darf das Ergebnis nicht beeinflussen.
        dt_other = DocumentType.objects.create(
            facility=second_facility,
            name="Kontakt (fremd)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        _create_event_with_activity(second_facility, dt_other, second_facility_user, when, "fremd")

        visible = _create_event_with_activity(facility, dt_normal, staff_user, when, "sichtbar")
        hidden = _create_event_with_activity(facility, dt_high, staff_user, when, "verborgen")

        items = build_feed_items(facility, today, feed_type="activities", user=staff_user)
        target_ids = [i["object"].target_id for i in items if i["type"] == "activity"]

        assert visible.pk in target_ids
        assert hidden.pk not in target_ids

    def test_foreign_facility_activities_never_appear(
        self, facility, staff_user, doc_type_contact, second_facility, second_facility_user
    ):
        today = timezone.localdate()
        when = timezone.make_aware(datetime.combine(today, time(12, 0)))

        dt_other = DocumentType.objects.create(
            facility=second_facility,
            name="Kontakt (fremd)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        foreign = _create_event_with_activity(second_facility, dt_other, second_facility_user, when, "fremd")
        own = _create_event_with_activity(facility, doc_type_contact, staff_user, when, "eigen")

        items = build_feed_items(facility, today, feed_type="activities", user=staff_user)
        target_ids = [i["object"].target_id for i in items if i["type"] == "activity"]

        assert own.pk in target_ids
        assert foreign.pk not in target_ids

    def test_event_branch_result_matches_across_facilities(
        self, facility, staff_user, doc_type_contact, second_facility, second_facility_user
    ):
        """Der Event-Zweig war schon immer gescopt — als Gegenprobe zur Activity-Seite."""
        today = timezone.localdate()
        when = timezone.make_aware(datetime.combine(today, time(12, 0)))

        dt_other = DocumentType.objects.create(
            facility=second_facility,
            name="Kontakt (fremd)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        Event.objects.create(
            facility=second_facility,
            document_type=dt_other,
            occurred_at=when,
            data_json={"note": "fremd"},
            created_by=second_facility_user,
        )
        own = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=when,
            data_json={"note": "eigen"},
            created_by=staff_user,
        )

        items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        assert [i["object"].pk for i in items] == [own.pk]
