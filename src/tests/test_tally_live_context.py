"""Live-Zeitkontext der Strichliste (Refs #1910)."""

from datetime import UTC, date, datetime, time
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, TimeFilter  # noqa: I001
from core.services.events import TallyLiveContext, build_tally_summary, current_tally_context

LOCAL_NIGHT_INSTANT = datetime(2026, 6, 21, 23, 30, tzinfo=UTC)


def _night_shift(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Nachtdienst",
        start_time=time(22, 0),
        end_time=time(8, 0),
        sort_order=0,
    )


def _number_tally_type(facility):
    document_type = DocumentType.objects.create(facility=facility, name="Zählwert", tally_enabled=True)
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Anzahl",
        field_type=FieldTemplate.FieldType.NUMBER,
        is_required=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    return document_type


@pytest.mark.django_db
class TestTallyLiveContext:
    def test_night_shift_uses_local_previous_shift_date_and_exact_window(self, facility):
        night_shift = _night_shift(facility)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            context = current_tally_context(facility)

        assert context.time_filter == night_shift
        assert context.shift_date == date(2026, 6, 21)
        assert context.window_start.isoformat() == "2026-06-21T22:00:00+02:00"
        assert context.window_end.isoformat() == "2026-06-22T08:00:00+02:00"

    def test_summary_exposes_the_live_filter_shift_date_and_window(self, facility, staff_user):
        night_shift = _night_shift(facility)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            summary = build_tally_summary(facility, staff_user)

        assert summary["time_filter"] == night_shift
        assert summary["shift_date"] == date(2026, 6, 21)
        assert summary["window_start"].isoformat() == "2026-06-21T22:00:00+02:00"
        assert summary["window_end"].isoformat() == "2026-06-22T08:00:00+02:00"

    def test_summary_counts_an_event_at_the_same_authoritative_live_instant(self, facility, staff_user):
        _night_shift(facility)
        document_type = DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        Event.objects.create(
            facility=facility,
            document_type=document_type,
            occurred_at=LOCAL_NIGHT_INSTANT,
            data_json={},
            created_by=staff_user,
            is_anonymous=True,
        )

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            summary = build_tally_summary(facility, staff_user)

        row = next(row for row in summary["rows"] if row["document_type"] == document_type)
        assert summary["window_start"] <= LOCAL_NIGHT_INSTANT <= summary["window_end"]
        assert row["count"] == 1

    def test_summary_rows_follow_curated_sort_order_name_and_pk(self, facility, staff_user):
        later = DocumentType.objects.create(facility=facility, name="Alpha", sort_order=20, tally_enabled=True)
        first = DocumentType.objects.create(facility=facility, name="Zulu", sort_order=1, tally_enabled=True)
        same_order_b = DocumentType.objects.create(facility=facility, name="Beta", sort_order=5, tally_enabled=True)
        same_order_a = DocumentType.objects.create(
            facility=facility,
            name="Alpha zwei",
            sort_order=5,
            tally_enabled=True,
        )

        summary = build_tally_summary(facility, staff_user)

        ids = [row["document_type"].pk for row in summary["rows"]]
        assert ids == [first.pk, same_order_a.pk, same_order_b.pk, later.pk]


@pytest.mark.django_db
class TestTallyLiveContextView:
    def test_historical_date_hides_the_tally_panel(self, client, facility, staff_user):
        DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(reverse("core:zeitstrom"), {"date": "2026-06-20"})

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is False
        assert b'data-testid="tally-panel"' not in response.content

    def test_non_running_shift_hides_the_tally_panel(self, client, facility, staff_user):
        _night_shift(facility)
        other_shift = TimeFilter.objects.create(
            facility=facility,
            label="Frühdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            sort_order=1,
        )
        DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(
                reverse("core:zeitstrom"),
                {"date": "2026-06-22", "time_filter": str(other_shift.pk)},
            )

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is False
        assert b'data-testid="tally-panel"' not in response.content

    def test_non_running_shift_replaces_the_panel_with_an_empty_oob_slot(self, client, facility, staff_user):
        _night_shift(facility)
        other_shift = TimeFilter.objects.create(
            facility=facility,
            label="Frühdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            sort_order=1,
        )
        DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(
                reverse("core:zeitstrom_feed_partial"),
                {"date": "2026-06-22", "time_filter": str(other_shift.pk)},
            )

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is False
        content = response.content.decode()
        assert 'id="tally-panel-slot" hx-swap-oob="innerHTML"' in content
        assert 'data-testid="tally-panel"' not in content

    def test_running_night_shift_shows_its_live_window(self, client, facility, staff_user):
        night_shift = _night_shift(facility)
        DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(reverse("core:zeitstrom"))

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is True
        assert response.context["tally_summary"]["time_filter"] == night_shift
        assert response.context["target_date"] == date(2026, 6, 21)
        content = response.content.decode()
        assert 'data-testid="tally-panel"' in content
        assert 'data-testid="tally-live-context"' in content
        assert "Nachtdienst" in content
        assert "22:00" in content
        assert "08:00" in content

    def test_historical_post_context_is_rejected_without_creating_an_event(self, client, facility, staff_user):
        document_type = DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.post(
                reverse("core:tally_increment"),
                {"document_type": document_type.pk, "date": "2026-06-20"},
                HTTP_HX_REQUEST="true",
            )

        assert response.status_code == 400
        assert "Anonyme Kontakte im aktuellen Zeitfenster" in response.content.decode()
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_historical_get_context_is_rejected(self, client, facility, staff_user):
        document_type = _number_tally_type(facility)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(
                reverse("core:tally_increment"),
                {"document_type": document_type.pk, "date": "2026-06-20"},
            )

        assert response.status_code == 400
        assert "Anonyme Kontakte im aktuellen Zeitfenster" in response.content.decode()

    @pytest.mark.parametrize(
        ("raw_params", "is_live"),
        [
            ({}, True),
            ({"date": "2026-06-21", "time_filter": "live"}, True),
            ({"time_filter": "live"}, False),
            ({"date": "2026-06-22", "time_filter": "live"}, False),
            ({"date": "2026-06-21", "time_filter": "all"}, False),
            ({"date": "2026-06-22", "time_filter": "all"}, True),
            ({"date": "2026-06-21"}, False),
            ({"date": "2026-06-22"}, True),
            ({"time_filter": "other"}, False),
        ],
        ids=[
            "empty",
            "night-shift-date",
            "night-without-date",
            "night-today-date",
            "all-shift-date",
            "all-today-date",
            "no-filter-shift-date",
            "no-filter-today-date",
            "other-filter",
        ],
    )
    def test_tally_get_and_post_only_accept_parameter_pairs_matching_the_live_panel(
        self, client, facility, staff_user, raw_params, is_live
    ):
        night_shift = _night_shift(facility)
        other_shift = TimeFilter.objects.create(
            facility=facility,
            label="Frühdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            sort_order=1,
        )
        number_type = _number_tally_type(facility)
        direct_type = DocumentType.objects.create(facility=facility, name="Direkt", tally_enabled=True)
        params = {
            key: str(night_shift.pk) if value == "live" else str(other_shift.pk) if value == "other" else value
            for key, value in raw_params.items()
        }
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            get_response = client.get(
                reverse("core:tally_increment"),
                {"document_type": number_type.pk, **params},
            )
            post_response = client.post(
                reverse("core:tally_increment"),
                {"document_type": direct_type.pk, **params},
                HTTP_HX_REQUEST="true",
            )

        expected_status = 200 if is_live else 400
        assert get_response.status_code == expected_status
        assert post_response.status_code == expected_status
        assert Event.objects.filter(document_type=direct_type).count() == int(is_live)

    @pytest.mark.parametrize(
        ("url_name", "params"),
        [
            ("core:zeitstrom", {"date": "2026-06-20"}),
            ("core:zeitstrom_feed_partial", {"date": "2026-06-20"}),
        ],
        ids=["full", "partial"],
    )
    def test_non_live_views_do_not_build_the_tally_summary(
        self, client, facility, staff_user, monkeypatch, url_name, params
    ):
        from core.views import zeitstrom as zeitstrom_views

        summary_builder = Mock(return_value={"time_filter": None, "shift_date": date(2026, 6, 22), "rows": []})
        monkeypatch.setattr(zeitstrom_views, "build_tally_summary", summary_builder)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(reverse(url_name), params)

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is False
        assert response.context["tally_summary"] is None
        summary_builder.assert_not_called()

    def test_live_full_view_reuses_its_single_context_for_the_summary(self, client, facility, staff_user, monkeypatch):
        from core.views import zeitstrom as zeitstrom_views

        night_shift = _night_shift(facility)
        live_context = TallyLiveContext(
            night_shift,
            date(2026, 6, 21),
            datetime(2026, 6, 21, 22, tzinfo=UTC),
            datetime(2026, 6, 22, 8, tzinfo=UTC),
        )
        context_lookup = Mock(return_value=live_context)
        summary = {
            "time_filter": night_shift,
            "shift_date": date(2026, 6, 21),
            "window_start": live_context.window_start,
            "window_end": live_context.window_end,
            "rows": [],
        }
        summary_builder = Mock(return_value=summary)
        monkeypatch.setattr(zeitstrom_views, "current_tally_context", context_lookup)
        monkeypatch.setattr(zeitstrom_views, "build_tally_summary", summary_builder)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(reverse("core:zeitstrom"))

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is True
        assert response.context["tally_summary"] == summary
        assert context_lookup.call_count == 1
        summary_builder.assert_called_once_with(facility, staff_user, context=live_context)

    def test_non_live_partial_avoids_tally_policy_assignment_queries(self, client, facility, staff_user):
        _number_tally_type(facility)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            with CaptureQueriesContext(connection) as queries:
                response = client.get(reverse("core:zeitstrom_feed_partial"), {"date": "2026-06-20"})

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is False
        assert not any("core_documenttypefield" in query["sql"] for query in queries.captured_queries)

    @pytest.mark.parametrize(
        ("boundary_instant", "other_label", "other_start", "other_end", "expected_shift_date"),
        [
            (datetime(2026, 6, 22, 6, tzinfo=UTC), "Frühdienst", time(8, 0), time(16, 0), date(2026, 6, 21)),
            (datetime(2026, 6, 22, 20, tzinfo=UTC), "Spätdienst", time(16, 0), time(22, 0), date(2026, 6, 22)),
        ],
        ids=["08-00", "22-00"],
    )
    def test_auto_selection_uses_the_same_tie_breaker_as_the_live_tally_context(
        self,
        client,
        facility,
        staff_user,
        boundary_instant,
        other_label,
        other_start,
        other_end,
        expected_shift_date,
    ):
        night_shift = TimeFilter.objects.create(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            facility=facility,
            label="Nachtdienst",
            start_time=time(22, 0),
            end_time=time(8, 0),
            sort_order=0,
        )
        TimeFilter.objects.create(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            facility=facility,
            label=other_label,
            start_time=other_start,
            end_time=other_end,
            sort_order=0,
        )
        DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=boundary_instant):
            live_context = current_tally_context(facility)
            response = client.get(reverse("core:zeitstrom"))

        assert live_context.time_filter == night_shift
        assert response.context["selected_filter"] == night_shift
        assert response.context["target_date"] == expected_shift_date
        assert response.context["show_tally_panel"] is True

    def test_handover_mode_explicitly_marks_the_tally_panel_as_hidden(self, client, facility, staff_user):
        _night_shift(facility)
        DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})

        assert response.status_code == 200
        assert response.context["show_tally_panel"] is False
        assert response.context["tally_summary"] is None
