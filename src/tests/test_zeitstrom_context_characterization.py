"""Characterization of the Zeitstrom page context.

Pins what ``ZeitstromView.get_context_data`` hands to the template for
representative cases: every role, feed and handover mode, the running and a
historical shift, the document-type filter, an empty facility and an active
house ban. Each context part is compared against the public service that
owns it, recomputed at the same instant, so a restructured view that computes
any part differently fails here.
"""

import dataclasses
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import UUID

import pytest
from django.db import connection
from django.db.models import Model, QuerySet
from django.forms import BaseForm
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.functional import Promise

from core.models import DocumentType, DocumentTypeField, FieldTemplate, TimeFilter, WorkItem
from core.services.case import build_handover_summary
from core.services.case.handover_note import Shift, build_handover_note_context
from core.services.dashboard import build_focus_box, staff_dashboard_context
from core.services.events import build_tally_summary
from core.services.system import get_active_bans
from core.services.zeitstrom_filters import resolve_zeitstrom_state
from core.services.zeitstrom_watch import (
    build_current_shift_summary,
    build_zeitstrom_page_scope,
    build_zeitstrom_tally_topology_validator,
)
from tests._factories import make_event

#: 12:00 in Berlin (CEST) on a Monday: the early shift is running.
INSTANT = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
TODAY = date(2026, 6, 22)
YESTERDAY = date(2026, 6, 21)

ROLES = ["assistant_user", "staff_user", "lead_user", "admin_user"]

BASE_KEYS = {
    "view",
    "feed_items",
    "handover_summary",
    "current_shift_summary",
    "tally_summary",
    "show_tally_panel",
    "time_filters",
    "selected_filter",
    "selected_filter_id",
    "target_date",
    "prev_date",
    "next_date",
    "today",
    "selected_type",
    "filter_options",
    "document_types",
    "selected_doc_type",
    "events_partial_url",
    "focus_box",
    "active_bans",
    "zeitstrom_filter_state",
    "contacts_navigation",
    "contacts_list_params",
    "tally_next",
    "zeitstrom_page_scope",
    "zeitstrom_tally_topology",
}
COCKPIT_KEYS = {"show_cockpit", "cockpit"}
HANDOVER_KEYS = {"uebergabe_mode", "uebergabe_summary", "handover_notes", "handover_note_display"}


def _berlin(day, hour, minute=0):
    """Berlin wall-clock time in June (UTC+2) as an aware UTC datetime."""
    return datetime.combine(day, time(hour, minute), tzinfo=UTC) - timedelta(hours=2)


def _norm(value):
    """Reduce a context value to comparable primitives (no object identities)."""
    if isinstance(value, Model):
        return (type(value).__name__, str(value.pk))
    if isinstance(value, BaseForm):
        return ("form", type(value).__name__, _norm(dict(value.initial)))
    if isinstance(value, dict):
        return {str(key): _norm(item) for key, item in value.items()}
    if isinstance(value, QuerySet | list | tuple):
        return [_norm(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(repr(_norm(item)) for item in value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, str | int | float | bool | UUID | Decimal) or value is None:
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _norm(getattr(value, field.name)) for field in dataclasses.fields(value)}
    return ("object", type(value).__name__)


def _norm_tally(summary):
    """Tally rows carry a fresh idempotency key per build; compare the rest."""
    if summary is None:
        return None
    rows = [
        {key: item for key, item in row.items() if key not in {"idempotency_key", "tally_increment_url"}}
        for row in summary["rows"]
    ]
    return _norm({**summary, "rows": rows})


def _feed_snapshot(items):
    return [
        (item["type"], str(item["object"].pk), _norm(getattr(item["object"], "preview_fields", None))) for item in items
    ]


@pytest.fixture
def shifts(facility):
    early = TimeFilter.objects.create(
        facility=facility, label="Frühdienst", start_time=time(8, 0), end_time=time(16, 0), sort_order=0
    )
    late = TimeFilter.objects.create(
        facility=facility, label="Spätdienst", start_time=time(16, 0), end_time=time(22, 0), sort_order=1
    )
    night = TimeFilter.objects.create(
        facility=facility, label="Nachtdienst", start_time=time(22, 0), end_time=time(8, 0), sort_order=2
    )
    return {"early": early, "late": late, "night": night}


@pytest.fixture
def ban_doc_type(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )
    for name, field_type in (
        ("Grund", FieldTemplate.FieldType.TEXTAREA),
        ("Bis", FieldTemplate.FieldType.DATE),
        ("Aktiv", FieldTemplate.FieldType.BOOLEAN),
    ):
        ft = FieldTemplate.objects.create(
            facility=facility, name=name, field_type=field_type, sensitivity=DocumentType.Sensitivity.NORMAL
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)
    return doc_type


@pytest.fixture
def frozen():
    with patch("django.utils.timezone.now", return_value=INSTANT):
        yield


@pytest.fixture
def populated(frozen, facility, shifts, staff_user, client_identified, doc_type_contact, doc_type_crisis, ban_doc_type):
    """Events in the running early shift, yesterday's late and night shift, a ban and a task."""

    def event(doc_type, when, data):
        return make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=when,
            data_json=data,
            created_by=staff_user,
        )

    return {
        "contact_now": event(doc_type_contact, _berlin(TODAY, 9, 30), {"dauer": 15, "notiz": "heute"}),
        "crisis_now": event(doc_type_crisis, _berlin(TODAY, 10, 0), {"notiz-krise": "vertraulich"}),
        "contact_late": event(doc_type_contact, _berlin(YESTERDAY, 18, 0), {"dauer": 30}),
        "contact_night": event(doc_type_contact, _berlin(YESTERDAY, 23, 30), {"dauer": 5}),
        "ban": event(ban_doc_type, _berlin(TODAY, 9, 0), {"grund": "Bedrohung", "bis": "2026-07-30", "aktiv": True}),
        "task": WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            client=client_identified,
            title="Rückruf",
            status=WorkItem.Status.OPEN,
            assigned_to=staff_user,
        ),
    }


def _get(client, user, params=None):
    client.force_login(user)
    response = client.get(reverse("core:zeitstrom"), params or {})
    assert response.status_code == 200
    return response


def _context_keys(response):
    return set(response.context.keys()) & (BASE_KEYS | COCKPIT_KEYS | HANDOVER_KEYS)


def _assert_matches_services(response, facility, user, params):
    """Every context part equals the owning service, recomputed now."""
    ctx = response.context
    state = resolve_zeitstrom_state(params, facility)

    assert ctx["target_date"] == state.target_date
    assert ctx["prev_date"] == state.target_date - timedelta(days=1)
    assert ctx["next_date"] == state.target_date + timedelta(days=1)
    assert ctx["today"] == TODAY
    assert ctx["selected_filter"] == state.time_filter
    assert ctx["selected_filter_id"] == state.time_filter_id
    assert list(ctx["time_filters"]) == list(state.time_filters)
    assert ctx["selected_type"] == state.feed_type
    assert ctx["selected_doc_type"] == state.doc_type_id
    assert ctx["zeitstrom_filter_state"] == state.params
    assert ctx["events_partial_url"] == "core:zeitstrom_feed_partial"
    assert [(value, str(label)) for value, label in ctx["filter_options"]] == [
        ("", "Alle"),
        ("events", "Kontakte"),
        ("activities", "Aktivitäten"),
        ("workitems", "Aufgaben"),
        ("bans", "Hausverbote"),
    ]
    assert ctx["tally_next"] == f"{reverse('core:zeitstrom')}?{urlencode(state.params)}"

    expected_handover = (
        build_handover_summary(facility, state.target_date, state.time_filter, user) if state.time_filter else None
    )
    assert _norm(ctx["handover_summary"]) == _norm(expected_handover)
    current_state = resolve_zeitstrom_state({}, facility)
    assert _norm(ctx["current_shift_summary"]) == _norm(build_current_shift_summary(facility, user, current_state))
    assert _norm(ctx["focus_box"]) == _norm(build_focus_box(facility))
    assert _norm(ctx["active_bans"]) == _norm(get_active_bans(facility, user=user))
    assert ctx["zeitstrom_page_scope"] == build_zeitstrom_page_scope(user, facility)
    assert ctx["zeitstrom_tally_topology"] == build_zeitstrom_tally_topology_validator(
        user, facility, state, tally_summary=ctx["tally_summary"]
    )

    if state.is_handover:
        assert ctx["uebergabe_mode"] is True
        assert _norm(ctx["uebergabe_summary"]) == _norm(
            build_handover_summary(facility, state.target_date, state.time_filter, user)
        )
        displayed = Shift(state.target_date, state.time_filter)
        assert ctx["handover_note_display"] == displayed
        assert _norm(ctx["handover_notes"]) == _norm(
            build_handover_note_context(facility, user, displayed, now=INSTANT)
        )
        assert ctx["show_tally_panel"] is False
        assert ctx["tally_summary"] is None
    else:
        assert "uebergabe_mode" not in ctx
        assert ctx["show_tally_panel"] is state.is_live_tally_context
        if state.is_live_tally_context:
            assert _norm_tally(ctx["tally_summary"]) == _norm_tally(
                build_tally_summary(facility, user, context=state.live_context)
            )
            for row in ctx["tally_summary"]["rows"]:
                assert row["tally_increment_url"].startswith(reverse("core:tally_increment") + "?")
        else:
            assert ctx["tally_summary"] is None

    if user.is_lead_or_admin or user.is_super_admin:
        assert "show_cockpit" not in ctx
    else:
        assert ctx["show_cockpit"] is True
        assert _norm(ctx["cockpit"]) == _norm(staff_dashboard_context(user, facility))


@pytest.mark.django_db
class TestZeitstromContextCharacterization:
    @pytest.mark.parametrize("role", ROLES)
    def test_running_shift_feed(self, client, request, facility, shifts, populated, role):
        user = request.getfixturevalue(role)

        response = _get(client, user)

        ctx = response.context
        expected = BASE_KEYS | (set() if user.is_lead_or_admin else COCKPIT_KEYS)
        assert _context_keys(response) == expected
        assert ctx["selected_filter"] == shifts["early"]
        assert ctx["target_date"] == TODAY
        assert ctx["show_tally_panel"] is True
        assert ctx["handover_summary"]["shift_label"] == "Frühdienst"
        assert _norm(ctx["handover_summary"]) == _norm(ctx["current_shift_summary"])
        feed_events = [str(item["object"].pk) for item in ctx["feed_items"] if item["type"] == "event"]
        expected_events = [str(populated["contact_now"].pk)]
        if role != "assistant_user":
            expected_events.insert(0, str(populated["crisis_now"].pk))
        assert feed_events == expected_events
        assert [item["object"].pk for item in ctx["feed_items"] if item["type"] == "ban"] == (
            [] if role == "assistant_user" else [populated["ban"].pk]
        )
        _assert_matches_services(response, facility, user, {})

    @pytest.mark.parametrize("role", ROLES)
    def test_historical_shift_feed(self, client, request, facility, shifts, populated, role):
        user = request.getfixturevalue(role)
        params = {"date": YESTERDAY.isoformat(), "time_filter": str(shifts["late"].pk)}

        response = _get(client, user, params)

        ctx = response.context
        assert ctx["selected_filter"] == shifts["late"]
        assert ctx["target_date"] == YESTERDAY
        assert ctx["show_tally_panel"] is False
        assert ctx["tally_summary"] is None
        assert ctx["handover_summary"]["shift_label"] == "Spätdienst"
        assert ctx["handover_summary"]["date"] == YESTERDAY
        # The current-shift box stays on the running shift.
        assert ctx["current_shift_summary"]["shift_label"] == "Frühdienst"
        assert ctx["current_shift_summary"]["date"] == TODAY
        assert [item["object"].pk for item in ctx["feed_items"] if item["type"] == "event"] == [
            populated["contact_late"].pk
        ]
        _assert_matches_services(response, facility, user, params)

    @pytest.mark.parametrize("role", ROLES)
    def test_whole_day_without_shift_filter(self, client, request, facility, shifts, populated, role):
        user = request.getfixturevalue(role)
        params = {"time_filter": "all"}

        response = _get(client, user, params)

        ctx = response.context
        assert ctx["selected_filter"] is None
        assert ctx["selected_filter_id"] == "all"
        assert ctx["handover_summary"] is None
        assert ctx["current_shift_summary"]["shift_label"] == "Frühdienst"
        _assert_matches_services(response, facility, user, params)

    @pytest.mark.parametrize("role", ROLES)
    def test_automatic_handover(self, client, request, facility, shifts, populated, role):
        user = request.getfixturevalue(role)
        params = {"view": "uebergabe"}

        response = _get(client, user, params)

        ctx = response.context
        expected = BASE_KEYS | HANDOVER_KEYS | (set() if user.is_lead_or_admin else COCKPIT_KEYS)
        assert _context_keys(response) == expected
        # At 12:00 the latest completed shift is last night's.
        assert ctx["selected_filter"] == shifts["night"]
        assert ctx["selected_filter_id"] == str(shifts["night"].pk)
        assert ctx["target_date"] == YESTERDAY
        assert ctx["uebergabe_summary"]["shift_label"] == "Nachtdienst"
        assert _norm(ctx["handover_summary"]) == _norm(ctx["uebergabe_summary"])
        assert ctx["current_shift_summary"]["shift_label"] == "Frühdienst"
        assert (ctx["handover_notes"] is None) is (role == "assistant_user")
        _assert_matches_services(response, facility, user, params)

    @pytest.mark.parametrize("role", ["assistant_user", "staff_user"])
    def test_handover_for_a_whole_historical_day(self, client, request, facility, shifts, populated, role):
        user = request.getfixturevalue(role)
        params = {"view": "uebergabe", "date": YESTERDAY.isoformat(), "time_filter": "all"}

        response = _get(client, user, params)

        ctx = response.context
        assert ctx["selected_filter"] is None
        assert ctx["selected_filter_id"] == "all"
        assert ctx["handover_summary"] is None
        assert ctx["uebergabe_summary"]["date"] == YESTERDAY
        _assert_matches_services(response, facility, user, params)

    def test_handover_for_an_explicit_shift(self, client, facility, shifts, populated, staff_user):
        params = {"view": "uebergabe", "date": YESTERDAY.isoformat(), "time_filter": str(shifts["late"].pk)}

        response = _get(client, staff_user, params)

        assert response.context["uebergabe_summary"]["shift_label"] == "Spätdienst"
        _assert_matches_services(response, facility, staff_user, params)

    def test_document_type_filter(self, client, facility, shifts, populated, staff_user, doc_type_crisis):
        params = {"doc_type": str(doc_type_crisis.pk)}

        response = _get(client, staff_user, params)

        ctx = response.context
        assert ctx["selected_doc_type"] == str(doc_type_crisis.pk)
        assert [item["object"].pk for item in ctx["feed_items"] if item["type"] == "event"] == [
            populated["crisis_now"].pk
        ]
        assert [item["object"].pk for item in ctx["feed_items"] if item["type"] == "ban"] == [populated["ban"].pk]
        _assert_matches_services(response, facility, staff_user, params)

    @pytest.mark.parametrize("role", ROLES)
    def test_document_types_follow_the_role(self, client, request, facility, populated, role):
        user = request.getfixturevalue(role)

        response = _get(client, user)

        names = [doc_type.name for doc_type in response.context["document_types"]]
        if role == "assistant_user":
            assert names == ["Kontakt"]
        else:
            assert names == ["Hausverbot", "Kontakt", "Krisengespräch"]

    def test_feed_previews_and_ban_hint(self, client, facility, shifts, populated, lead_user):
        response = _get(client, lead_user)

        ctx = response.context
        assert _feed_snapshot(ctx["feed_items"])[:3] == [
            ("workitem", str(populated["task"].pk), None),
            (
                "event",
                str(populated["crisis_now"].pk),
                [],
            ),
            ("event", str(populated["contact_now"].pk), [{"label": "Dauer", "value": "15", "is_textarea": False}]),
        ]
        bans = ctx["active_bans"]
        assert len(bans) == 1
        assert bans[0]["client"] == populated["ban"].client

    @pytest.mark.parametrize("role", ROLES)
    def test_empty_facility(self, client, request, facility, frozen, role):
        user = request.getfixturevalue(role)

        response = _get(client, user)

        ctx = response.context
        assert ctx["feed_items"] == []
        assert ctx["active_bans"] == []
        assert list(ctx["time_filters"]) == []
        assert ctx["selected_filter"] is None
        assert ctx["handover_summary"] is None
        assert ctx["current_shift_summary"] is None
        _assert_matches_services(response, facility, user, {})

    def test_empty_facility_handover(self, client, facility, frozen, staff_user):
        params = {"view": "uebergabe"}

        response = _get(client, staff_user, params)

        ctx = response.context
        assert ctx["selected_filter"] is None
        assert ctx["handover_summary"] is None
        assert ctx["uebergabe_summary"]["date"] == TODAY
        _assert_matches_services(response, facility, staff_user, params)


@pytest.mark.django_db
class TestZeitstromContextQueryBudget:
    """Query counts of the full page, measured before the context was split up."""

    @pytest.mark.parametrize(
        ("role", "params", "budget"),
        [
            ("staff_user", {}, 59),
            ("lead_user", {}, 56),
            ("staff_user", {"view": "uebergabe"}, 62),
            ("staff_user", {"date": YESTERDAY.isoformat(), "time_filter": "late"}, 50),
        ],
        ids=["staff-feed", "lead-feed", "staff-handover", "staff-historical"],
    )
    def test_full_page_query_budget(self, client, request, facility, shifts, populated, role, params, budget):
        user = request.getfixturevalue(role)
        params = {key: (str(shifts[value].pk) if key == "time_filter" else value) for key, value in params.items()}
        client.force_login(user)
        client.get(reverse("core:zeitstrom"), params)  # warm per-process caches

        with CaptureQueriesContext(connection) as queries:
            response = client.get(reverse("core:zeitstrom"), params)

        assert response.status_code == 200
        assert len(queries) <= budget, f"{len(queries)} queries, budget {budget}"
