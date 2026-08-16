"""Zeilenweise Tally-Swaps und serverautoritativer Feed-Refresh (Refs #1911)."""

from datetime import UTC, datetime, time
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, TimeFilter
from core.services.events import TALLY_IDEMPOTENCY_SCOPE, build_tally_summary, remember_idempotent_result
from tests._tally_helpers import post_tally


def _number_tally_type(facility, *, name="Zahl"):
    document_type = DocumentType.objects.create(facility=facility, name=name, tally_enabled=True)
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Anzahl",
        field_type=FieldTemplate.FieldType.NUMBER,
        is_required=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    return document_type, field


def _row(summary, document_type):
    return next(row for row in summary["rows"] if row["document_type"] == document_type)


TALLY_CONTEXT_BEFORE_BOUNDARY = datetime(2026, 6, 21, 23, 30, tzinfo=UTC)
TALLY_CONTEXT_AFTER_BOUNDARY = datetime(2026, 6, 22, 6, 30, tzinfo=UTC)


def _night_shift(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Nachtdienst",
        start_time=time(22, 0),
        end_time=time(8, 0),
        sort_order=0,
    )


@pytest.mark.django_db
class TestTallyRowRefresh:
    def test_success_replaces_only_its_row_and_keeps_other_row_out_of_response(self, client, facility, staff_user):
        first = DocumentType.objects.create(facility=facility, name="Erste", tally_enabled=True)
        second, number = _number_tally_type(facility, name="Zweite")
        client.force_login(staff_user)

        opened = client.get(
            reverse("core:tally_increment"),
            {"document_type": second.pk},
            HTTP_HX_REQUEST="true",
        )
        assert f'data-testid="tally-inline-form-{second.pk}"' in opened.content.decode()

        response = post_tally(
            client,
            {"document_type": str(first.pk)},
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert response["HX-Retarget"] == f"#tally-row-{first.pk}"
        assert response["HX-Reswap"] == "outerHTML"
        assert f'id="tally-row-{first.pk}"' in content
        assert f'id="tally-row-{second.pk}"' not in content
        assert 'data-testid="tally-panel"' not in content
        assert f'name="{number.slug}"' not in content

    def test_validation_and_cancel_replace_only_the_action_area(self, client, facility, staff_user):
        document_type, number = _number_tally_type(facility)
        other = DocumentType.objects.create(facility=facility, name="Andere", tally_enabled=True)
        client.force_login(staff_user)
        key = _row(build_tally_summary(facility, staff_user), document_type)["idempotency_key"]

        invalid = post_tally(
            client,
            {"document_type": str(document_type.pk), number.slug: "ungültig"},
            idempotency_key=key,
            HTTP_HX_REQUEST="true",
        )
        invalid_content = invalid.content.decode()
        assert f'id="tally-action-{document_type.pk}"' in invalid_content
        assert f'id="tally-row-{document_type.pk}"' not in invalid_content
        assert f'id="tally-row-{other.pk}"' not in invalid_content
        assert "HX-Trigger-After-Swap" not in invalid

        cancel = client.get(
            reverse("core:tally_increment"),
            {"document_type": document_type.pk, "cancel": "1"},
            HTTP_HX_REQUEST="true",
        )
        cancel_content = cancel.content.decode()
        assert f'id="tally-action-{document_type.pk}"' in cancel_content
        assert f'id="tally-row-{document_type.pk}"' not in cancel_content
        assert f'id="tally-row-{other.pk}"' not in cancel_content
        assert "HX-Trigger-After-Swap" not in cancel

    def test_only_success_and_replay_trigger_feed_refresh(self, client, facility, staff_user):
        document_type = DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)
        key = _row(build_tally_summary(facility, staff_user), document_type)["idempotency_key"]
        payload = {"document_type": str(document_type.pk), "idempotency_key": key}

        success = client.post(
            reverse("core:tally_increment"), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true"
        )
        replay = client.post(
            reverse("core:tally_increment"), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true"
        )

        assert success["HX-Trigger-After-Swap"] == "zeitstrom-changed"
        assert replay["HX-Trigger-After-Swap"] == "zeitstrom-changed"

    def test_feed_refresh_uses_one_server_rendered_filter_state_without_tally_oob(self, client, facility, staff_user):
        included, _number = _number_tally_type(facility, name="Enthalten")
        excluded = DocumentType.objects.create(facility=facility, name="Ausgeschlossen", tally_enabled=True)
        Event.objects.create(
            facility=facility,
            document_type=included,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
            is_anonymous=True,
        )
        Event.objects.create(
            facility=facility,
            document_type=excluded,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
            is_anonymous=True,
        )
        client.force_login(staff_user)
        params = {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "events",
            "doc_type": str(included.pk),
            "view": "feed",
        }

        page = client.get(reverse("core:zeitstrom"), params)
        page_content = page.content.decode()
        assert 'id="zeitstrom-filter-state"' in page_content
        for name, value in params.items():
            assert f'name="{name}" value="{value}"' in page_content
        assert 'hx-trigger="zeitstrom-changed from:body"' in page_content
        assert 'hx-sync="this:replace"' in page_content
        assert "#filter-type" not in page_content
        assert "next=%2F%3Fdate%3D" in page_content

        refreshed = client.get(reverse("core:zeitstrom_feed_partial"), {**params, "feed_only": "1"})
        content = refreshed.content.decode()
        assert refreshed.status_code == 200
        assert included.name in content
        assert excluded.name not in content
        assert 'hx-swap-oob="innerHTML"' not in content

    def test_non_htmx_direct_and_number_paths_are_full_pages_with_safe_redirect(self, client, facility, staff_user):
        direct = DocumentType.objects.create(facility=facility, name="Direkt", tally_enabled=True)
        number_type, number = _number_tally_type(facility)
        client.force_login(staff_user)
        next_url = f"{reverse('core:zeitstrom')}?type=events"

        direct_response = post_tally(
            client,
            {"document_type": str(direct.pk), "next": next_url},
        )
        assert direct_response.status_code == 302
        assert direct_response["Location"] == next_url

        form = client.get(
            reverse("core:tally_increment"),
            {"document_type": number_type.pk, "next": next_url},
        )
        assert form.status_code == 200
        assert b"<html" in form.content.lower()
        assert f'name="{number.slug}"'.encode() in form.content

        key = _row(build_tally_summary(facility, staff_user), number_type)["idempotency_key"]
        invalid = client.post(
            reverse("core:tally_increment"),
            {"document_type": number_type.pk, "idempotency_key": key, number.slug: "x", "next": next_url},
            HTTP_X_IDEMPOTENCY_KEY=key,
        )
        assert invalid.status_code == 200
        assert b"<html" in invalid.content.lower()
        assert f'value="{key}"'.encode() in invalid.content

        hostile = post_tally(
            client,
            {"document_type": str(direct.pk), "next": "//evil.example"},
        )
        assert hostile.status_code == 302
        assert hostile["Location"] == "/"

    def test_replay_after_policy_change_removes_only_the_stale_row(self, client, facility, staff_user):
        document_type = DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)
        key = _row(build_tally_summary(facility, staff_user), document_type)["idempotency_key"]
        payload = {"document_type": str(document_type.pk), "idempotency_key": key}

        first = client.post(
            reverse("core:tally_increment"), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true"
        )
        document_type.tally_enabled = False
        document_type.save(update_fields=["tally_enabled"])
        replay = client.post(
            reverse("core:tally_increment"), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true"
        )

        assert first.status_code == replay.status_code == 200
        assert replay.content == b""
        assert replay["HX-Retarget"] == f"#tally-row-{document_type.pk}"
        assert replay["HX-Trigger-After-Swap"] == "zeitstrom-changed"

    def test_stale_idempotency_cache_fails_closed_without_a_server_error(self, client, facility, staff_user):
        document_type = DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        client.force_login(staff_user)
        key = "stale-tally-key"
        remember_idempotent_result(TALLY_IDEMPOTENCY_SCOPE, staff_user.pk, key, "missing-event")

        response = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk), "idempotency_key": key},
            HTTP_X_IDEMPOTENCY_KEY=key,
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert "HX-Trigger-After-Swap" not in response


@pytest.mark.django_db
class TestTallyContextBoundary:
    @pytest.mark.parametrize("clear_cache", [False, True], ids=["cache", "database"])
    def test_matching_replay_after_a_context_change_returns_the_existing_event_without_a_write(
        self,
        client,
        facility,
        staff_user,
        clear_cache,
    ):
        """A retry is not a backdated write when the shift has already changed."""
        document_type = DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        night_shift = _night_shift(facility)
        client.force_login(staff_user)
        key = _row(build_tally_summary(facility, staff_user), document_type)["idempotency_key"]
        payload = {
            "document_type": str(document_type.pk),
            "idempotency_key": key,
            "date": "2026-06-21",
            "time_filter": str(night_shift.pk),
        }

        with patch("django.utils.timezone.now", return_value=TALLY_CONTEXT_BEFORE_BOUNDARY):
            first = client.post(
                reverse("core:tally_increment"),
                payload,
                HTTP_X_IDEMPOTENCY_KEY=key,
                HTTP_HX_REQUEST="true",
            )

        if clear_cache:
            cache.clear()

        with patch("django.utils.timezone.now", return_value=TALLY_CONTEXT_AFTER_BOUNDARY):
            # Die simulierte Schichtgrenze liegt mehrere Stunden später; für
            # diese Request-Grenze darf keine Test-Session-Laufzeit entscheiden.
            client.force_login(staff_user)
            replay = client.post(
                reverse("core:tally_increment"),
                payload,
                HTTP_X_IDEMPOTENCY_KEY=key,
                HTTP_HX_REQUEST="true",
            )

        assert first.status_code == replay.status_code == 200
        assert Event.objects.filter(document_type=document_type).count() == 1
        assert replay["HX-Trigger-After-Swap"] == "zeitstrom-changed"
        replay_html = replay.content.decode()
        assert 'name="date" value="2026-06-22"' in replay_html
        assert 'name="time_filter" value="all"' in replay_html

    def test_cancel_after_a_context_change_restores_a_current_action_without_the_write_guard(
        self,
        client,
        facility,
        staff_user,
    ):
        document_type, _number = _number_tally_type(facility)
        night_shift = _night_shift(facility)
        client.force_login(staff_user)
        params = {
            "document_type": str(document_type.pk),
            "date": "2026-06-21",
            "time_filter": str(night_shift.pk),
        }

        with patch("django.utils.timezone.now", return_value=TALLY_CONTEXT_BEFORE_BOUNDARY):
            opened = client.get(reverse("core:tally_increment"), params, HTTP_HX_REQUEST="true")

        with patch("django.utils.timezone.now", return_value=TALLY_CONTEXT_AFTER_BOUNDARY):
            client.force_login(staff_user)
            cancel = client.get(
                reverse("core:tally_increment"),
                {**params, "cancel": "1"},
                HTTP_HX_REQUEST="true",
            )
            full_page_cancel = client.get(reverse("core:tally_increment"), {**params, "cancel": "1"})

        assert opened.status_code == 200
        assert cancel.status_code == 200
        cancel_html = cancel.content.decode()
        assert "Anonyme Kontakte im aktuellen Zeitfenster" not in cancel_html
        assert f'data-testid="tally-increment-{document_type.pk}"' in cancel_html
        assert "date=2026-06-22" in cancel_html
        assert "time_filter=all" in cancel_html
        assert full_page_cancel.status_code == 302
        assert full_page_cancel["Location"] == "/?date=2026-06-22&time_filter=all&type=&doc_type=&view="

    def test_fresh_post_from_the_previous_context_stays_rejected(self, client, facility, staff_user):
        document_type = DocumentType.objects.create(facility=facility, name="Kontakt", tally_enabled=True)
        night_shift = _night_shift(facility)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=TALLY_CONTEXT_AFTER_BOUNDARY):
            response = post_tally(
                client,
                {
                    "document_type": str(document_type.pk),
                    "date": "2026-06-21",
                    "time_filter": str(night_shift.pk),
                },
                HTTP_HX_REQUEST="true",
            )

        assert response.status_code == 400
        assert Event.objects.filter(document_type=document_type).count() == 0
