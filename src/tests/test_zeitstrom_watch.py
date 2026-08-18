"""Security and response contracts for the Zeitstrom live watcher (Refs #1916)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, AuditLog, Client, DocumentType, Event, TimeFilter, User, WorkItem
from core.services.zeitstrom_filters import resolve_zeitstrom_state
from core.services.zeitstrom_watch import (
    build_zeitstrom_component_validator,
    build_zeitstrom_page_scope,
    build_zeitstrom_tally_topology_validator,
)


def _scope_for_client(client):
    user_id = client.session.get("_auth_user_id")
    if not user_id:
        return None
    user = User.objects.select_related("facility").get(pk=user_id)
    if user.facility is None:
        return None
    return build_zeitstrom_page_scope(user, user.facility)


def _watch(client, *, params=None, etag=None, validators=None, scope="auto"):
    headers = {"If-None-Match": etag} if etag else {}
    if scope == "auto":
        scope = _scope_for_client(client)
    if scope is not None:
        headers["X-Zeitstrom-Page-Scope"] = scope
    if validators is not None:
        headers["X-Zeitstrom-Validators"] = json.dumps(validators, separators=(",", ":"))
    return client.get(reverse("core:zeitstrom_watch"), params or {}, headers=headers)


def _page_scope(response):
    match = re.search(r'data-page-scope="([^"]+)"', response.content.decode())
    assert match is not None
    return match.group(1)


def _filter_transition_headers(page):
    return {
        "HX-Request": "true",
        "X-Zeitstrom-Filter-Transition": "time_filter",
        "X-Zeitstrom-Page-Scope": _page_scope(page),
        "X-Zeitstrom-Previous-State": json.dumps(page.context["zeitstrom_filter_state"]),
        "X-Zeitstrom-Validators": json.dumps({"tally_topology": page.context["zeitstrom_tally_topology"]}),
    }


@pytest.mark.django_db
class TestZeitstromWatchHttpContract:
    def test_anonymous_get_is_json_401_instead_of_login_html(self, client):
        response = _watch(client)

        assert response.status_code == 401
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["detail"] == "authentication_required"
        assert response.json()["login_url"].startswith("/login/")

    def test_non_facility_role_is_json_403(self, client, super_admin_user):
        client.force_login(super_admin_user)

        response = _watch(client)

        assert response.status_code == 403
        assert response["Content-Type"].startswith("application/json")
        assert response.json() == {"detail": "forbidden"}

    def test_response_has_private_no_store_cookie_vary_and_opaque_validators(self, client, assistant_user):
        client.force_login(assistant_user)

        response = _watch(client)

        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store, private"
        assert "Cookie" in response["Vary"]
        assert response["ETag"].startswith('"') and response["ETag"].endswith('"')
        payload = response.json()
        assert set(payload) == {"components", "validators"}
        assert set(payload["components"]) == {
            "feed",
            "tally_counts",
            "tally_topology",
            "current_shift",
            "focus_box",
            # Refs #1917: Hausverbotswarnungen werden über eine opake
            # Revision mitüberwacht.
            "bans",
        }
        assert set(payload["validators"]) == set(payload["components"])
        assert all(value.startswith('"') and value.endswith('"') for value in payload["validators"].values())
        serialized = response.content.decode()
        assert "html" not in serialized.lower()
        assert "count" not in serialized.lower().replace("tally_counts", "")

    def test_matching_if_none_match_returns_empty_304(self, client, staff_user):
        client.force_login(staff_user)
        first = _watch(client)
        audit_count = AuditLog.objects.count()

        response = _watch(client, etag=f'"other", {first["ETag"]}')

        assert response.status_code == 304
        assert response.content == b""
        assert response["ETag"] == first["ETag"]
        assert response["Cache-Control"] == "no-store, private"
        assert "Cookie" in response["Vary"]
        assert AuditLog.objects.count() == audit_count

    def test_component_validators_report_only_changed_components(self, client, staff_user, facility, doc_type_contact):
        client.force_login(staff_user)
        params = {"type": "workitems"}
        first = _watch(client, params=params)
        previous = first.json()["validators"]

        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            is_anonymous=True,
            data_json={},
            created_by=staff_user,
        )
        changed = _watch(client, params=params, etag=first["ETag"], validators=previous)

        assert changed.status_code == 200
        assert changed.json()["components"] == ["tally_counts"]
        assert set(changed.json()["validators"]) == {"tally_counts"}

    def test_type_filter_change_invalidates_only_the_feed_component(self, client, staff_user):
        client.force_login(staff_user)
        first = _watch(client)

        changed = _watch(
            client,
            params={"type": "events"},
            validators=first.json()["validators"],
        )

        assert changed.status_code == 200
        assert changed.json()["components"] == ["feed"]
        assert set(changed.json()["validators"]) == {"feed"}

    def test_tally_component_is_absent_outside_live_context(self, client, staff_user, facility, doc_type_contact):
        client.force_login(staff_user)
        yesterday = timezone.localdate() - timedelta(days=1)
        params = {"date": yesterday.isoformat(), "time_filter": "all", "type": "workitems"}
        first = _watch(client, params=params)
        assert "tally_counts" not in first.json()["components"]

        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            is_anonymous=True,
            data_json={},
            created_by=staff_user,
        )

        unchanged = _watch(client, params=params, etag=first["ETag"])
        assert unchanged.status_code == 304

    def test_uebergabe_has_no_live_watch_components(self, client, assistant_user):
        client.force_login(assistant_user)

        response = _watch(client, params={"view": "uebergabe"})

        assert response.json()["components"] == []
        assert response.json()["validators"] == {}

    def test_page_scope_is_opaque_and_facility_or_role_change_requires_reload(
        self,
        client,
        staff_user,
        second_facility,
    ):
        client.force_login(staff_user)
        page = client.get(reverse("core:zeitstrom"))
        scope = _page_scope(page)
        serialized = page.content.decode()
        assert re.fullmatch(r"[0-9a-f]{64}", scope)
        assert scope != str(staff_user.pk)
        assert scope != str(staff_user.facility_id)
        assert scope in serialized

        staff_user.facility = second_facility
        staff_user.save(update_fields=["facility"])
        facility_changed = _watch(client, scope=scope)
        assert facility_changed.status_code == 409
        assert facility_changed.json() == {"detail": "reload_required"}
        assert str(second_facility.pk) not in facility_changed.content.decode()

        staff_user.facility = second_facility
        staff_user.role = User.Role.ASSISTANT
        staff_user.save(update_fields=["facility", "role"])
        client.force_login(staff_user)
        role_changed = _watch(client, scope=scope)
        assert role_changed.status_code == 409
        assert role_changed.json() == {"detail": "reload_required"}


@pytest.mark.django_db
class TestZeitstromWatchVisibility:
    def test_low_user_high_event_keeps_etag_and_returns_304(self, client, assistant_user, staff_user, facility):
        """A hidden HIGH event must not become a timing side channel."""
        high_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Leitung sichtbar",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        client.force_login(assistant_user)
        first = _watch(client)

        Event.objects.create(
            facility=facility,
            document_type=high_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        response = _watch(client, etag=first["ETag"])
        assert response.status_code == 304
        assert response["ETag"] == first["ETag"]

    def test_hidden_field_change_does_not_change_low_user_validator(
        self, client, assistant_user, staff_user, facility, doc_type_contact
    ):
        hidden_field = doc_type_contact.fields.get(field_template__name="Notiz").field_template
        hidden_field.sensitivity = DocumentType.Sensitivity.HIGH
        hidden_field.save(update_fields=["sensitivity"])
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "erster geheimer Wert"},
            created_by=staff_user,
        )
        client.force_login(assistant_user)
        first = _watch(client)

        event.data_json = {"notiz": "zweiter geheimer Wert"}
        event.save(update_fields=["data_json", "updated_at"])

        response = _watch(client, etag=first["ETag"])
        assert response.status_code == 304

    def test_visible_event_changes_feed_validator(self, client, assistant_user, facility, doc_type_contact):
        client.force_login(assistant_user)
        first = _watch(client)

        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=assistant_user,
        )

        response = _watch(client, etag=first["ETag"])
        assert response.status_code == 200
        assert "feed" in response.json()["components"]

    def test_visible_dynamic_field_change_changes_feed_but_hidden_only_does_not(
        self, client, assistant_user, facility, doc_type_contact
    ):
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
            created_by=assistant_user,
        )
        client.force_login(assistant_user)
        first = _watch(client)

        event.data_json = {"dauer": 10}
        event.save(update_fields=["data_json", "updated_at"])

        response = _watch(client, etag=first["ETag"])
        assert response.status_code == 200
        assert "feed" in response.json()["components"]

    def test_event_card_client_pseudonym_change_changes_only_feed(
        self,
        client,
        staff_user,
        facility,
        client_identified,
        doc_type_contact,
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        params = {"type": "events"}
        first = _watch(client, params=params)

        Client.objects.filter(pk=client_identified.pk).update(pseudonym="Neu sichtbares Pseudonym")

        response = _watch(
            client,
            params=params,
            etag=first["ETag"],
            validators=first.json()["validators"],
        )
        assert response.status_code == 200
        assert response.json()["components"] == ["feed"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("status", WorkItem.Status.IN_PROGRESS),
            ("description", "Neue sichtbare Beschreibung"),
            ("title", "Neuer sichtbarer Titel"),
            ("priority", WorkItem.Priority.URGENT),
        ],
    )
    def test_visible_workitem_source_changes_feed_revision(self, client, staff_user, facility, field, value):
        item = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Ausgangstitel",
        )
        client.force_login(staff_user)
        params = {"type": "workitems"}
        first = _watch(client, params=params)

        setattr(item, field, value)
        item.save(update_fields=[field, "updated_at"])

        response = _watch(client, params=params, etag=first["ETag"])
        assert response.status_code == 200
        assert "feed" in response.json()["components"]

    def test_other_facility_change_keeps_validator(self, client, staff_user, second_facility, second_facility_user):
        client.force_login(staff_user)
        first = _watch(client)
        other_type = DocumentType.objects.create(facility=second_facility, name="Fremder Kontakt")
        Event.objects.create(
            facility=second_facility,
            document_type=other_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=second_facility_user,
        )

        response = _watch(client, etag=first["ETag"])
        assert response.status_code == 304

    def test_validator_is_bound_to_user(self, client, staff_user, assistant_user):
        client.force_login(staff_user)
        staff_response = _watch(client)
        client.force_login(assistant_user)

        assistant_response = _watch(client)

        assert assistant_response["ETag"] != staff_response["ETag"]

    def test_activity_targeting_hidden_event_does_not_change_low_etag(
        self, client, assistant_user, staff_user, facility
    ):
        high_type = DocumentType.objects.create(
            facility=facility,
            name="Verdecktes Ereignis",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        hidden_event = Event.objects.create(
            facility=facility,
            document_type=high_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(assistant_user)
        first = _watch(client, params={"type": "activities"})

        Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(Event),
            target_id=hidden_event.pk,
            summary="Unsichtbare Änderung",
        )

        response = _watch(client, params={"type": "activities"}, etag=first["ETag"])
        assert response.status_code == 304

    def test_visible_activity_summary_change_changes_feed_revision(
        self, client, staff_user, facility, client_identified
    ):
        activity = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(client_identified),
            target_id=client_identified.pk,
            summary="Alter sichtbarer Text",
        )
        client.force_login(staff_user)
        params = {"type": "activities"}
        first = _watch(client, params=params)

        Activity.objects.filter(pk=activity.pk).update(summary="Neuer sichtbarer Text")

        response = _watch(client, params=params, etag=first["ETag"])
        assert response.status_code == 200
        assert "feed" in response.json()["components"]

    def test_activity_card_actor_name_change_changes_feed_revision(
        self,
        client,
        staff_user,
        facility,
        client_identified,
    ):
        Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(client_identified),
            target_id=client_identified.pk,
            summary="Sichtbare Aktivität",
        )
        client.force_login(staff_user)
        params = {"type": "activities"}
        first = _watch(client, params=params)

        type(staff_user).objects.filter(pk=staff_user.pk).update(display_name="Neuer Anzeigename")

        response = _watch(client, params=params, etag=first["ETag"])
        assert response.status_code == 200
        assert "feed" in response.json()["components"]

    def test_ban_feed_card_creator_name_change_changes_feed_revision(
        self,
        client,
        staff_user,
        facility,
        client_identified,
    ):
        ban_type = DocumentType.objects.create(
            facility=facility,
            name="Hausverbot Feed",
            system_type=DocumentType.SystemType.BAN,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=ban_type,
            occurred_at=timezone.now(),
            data_json={"aktiv": True},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        params = {"type": "bans"}
        first = _watch(client, params=params)

        type(staff_user).objects.filter(pk=staff_user.pk).update(display_name="Neue Erstellerin")

        response = _watch(client, params=params, etag=first["ETag"])
        assert response.status_code == 200
        assert "feed" in response.json()["components"]

    def test_safety_banner_changes_are_not_part_of_the_live_watcher(
        self, client, assistant_user, staff_user, facility, client_identified
    ):
        ban_type = DocumentType.objects.create(
            facility=facility,
            name="Sicherheits-Hausverbot",
            system_type=DocumentType.SystemType.BAN,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        client.force_login(assistant_user)
        initial = _watch(client, params={"view": "uebergabe"})
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=ban_type,
            occurred_at=timezone.now(),
            data_json={"aktiv": True, "bis": "", "grund": "nicht sichtbar"},
            created_by=staff_user,
        )
        unchanged = _watch(client, params={"view": "uebergabe"}, etag=initial["ETag"])

        assert unchanged.status_code == 304


@pytest.mark.django_db
class TestZeitstromWatchQueryBudget:
    def test_watch_never_calls_render_builders(self, client, staff_user):
        client.force_login(staff_user)
        with (
            patch("core.views.zeitstrom.render", side_effect=AssertionError("template render called")),
            patch("core.services.events.build_feed_items", side_effect=AssertionError("feed renderer called")),
            patch("core.services.events.enrich_events_with_preview", side_effect=AssertionError("preview called")),
            patch("core.services.case.build_handover_summary", side_effect=AssertionError("handover builder called")),
            patch("core.services.dashboard.build_focus_box", side_effect=AssertionError("focus builder called")),
        ):
            response = _watch(client)

        assert response.status_code == 200

    @pytest.mark.parametrize("total_rows", [50, 200])
    def test_query_count_is_constant_for_one_vs_many_feed_rows(
        self, client, staff_user, facility, doc_type_contact, total_rows
    ):
        client.force_login(staff_user)
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        scope = _scope_for_client(client)
        with CaptureQueriesContext(connection) as one:
            assert _watch(client, params={"type": "events"}, scope=scope).status_code == 200

        Event.objects.bulk_create(
            [
                Event(
                    facility=facility,
                    document_type=doc_type_contact,
                    occurred_at=now,
                    data_json={},
                    created_by=staff_user,
                )
                for _ in range(total_rows - 1)
            ]
        )
        with CaptureQueriesContext(connection) as many:
            assert _watch(client, params={"type": "events"}, scope=scope).status_code == 200

        assert len(one) <= 20
        assert len(many) <= 20
        assert len(many) <= len(one) + 2, (
            f"Watch queries must stay constant: 1 row={len(one)}, {total_rows} rows={len(many)}"
        )
        event_projection_queries = [
            query["sql"]
            for query in many.captured_queries
            if 'FROM "core_event"' in query["sql"] and '"core_event"."data_json"' in query["sql"]
        ]
        assert event_projection_queries
        assert any("LIMIT 200" in sql for sql in event_projection_queries)


@pytest.mark.django_db
class TestZeitstromWatchChromeProjections:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("title", "Neuer Fokustitel"),
            ("remind_at", timezone.localdate()),
            ("recurrence", WorkItem.Recurrence.WEEKLY),
            ("item_type", WorkItem.ItemType.HINT),
            ("priority", WorkItem.Priority.URGENT),
        ],
    )
    def test_focus_box_revision_tracks_rendered_badges_without_feed_noise(
        self,
        client,
        staff_user,
        facility,
        field,
        value,
    ):
        item = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Alter Fokustitel",
            due_date=timezone.localdate() - timedelta(days=1),
        )
        client.force_login(staff_user)
        params = {"type": "events"}
        first = _watch(client, params=params)
        previous = first.json()["validators"]

        setattr(item, field, value)
        item.save(update_fields=[field, "updated_at"])

        response = _watch(client, params=params, etag=first["ETag"], validators=previous)
        assert response.status_code == 200
        assert response.json()["components"] == ["focus_box"]

    def test_current_shift_revision_tracks_visible_highlight_content(self, client, staff_user, facility):
        TimeFilter.objects.create(
            facility=facility,
            label="Ganztagstest",
            start_time="00:00",
            end_time="23:59",
        )
        item = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Dringender Diensthinweis",
            status=WorkItem.Status.DONE,
            priority=WorkItem.Priority.URGENT,
        )
        client.force_login(staff_user)
        params = {"type": "events"}
        first = _watch(client, params=params)
        previous = first.json()["validators"]

        item.title = "Geänderter Diensthinweis"
        item.save(update_fields=["title", "updated_at"])

        response = _watch(client, params=params, etag=first["ETag"], validators=previous)
        assert response.status_code == 200
        assert response.json()["components"] == ["current_shift"]

    def test_current_shift_revision_tracks_rendered_client_stat(self, client, staff_user, facility):
        TimeFilter.objects.create(
            facility=facility,
            label="Ganztag Statistik",
            start_time="00:00",
            end_time="23:59",
        )
        client.force_login(staff_user)
        params = {"type": "events"}
        first = _watch(client, params=params)

        Client.objects.create(
            facility=facility,
            pseudonym="Neu im aktuellen Dienst",
            created_by=staff_user,
        )

        response = _watch(
            client,
            params=params,
            etag=first["ETag"],
            validators=first.json()["validators"],
        )
        assert response.status_code == 200
        assert response.json()["components"] == ["current_shift"]


@pytest.mark.django_db
class TestZeitstromWatchRefreshPartials:
    def test_normal_time_filter_partial_advertises_its_consistent_feed_and_topology_snapshot(
        self,
        client,
        staff_user,
        facility,
    ):
        selected = TimeFilter.objects.create(
            facility=facility,
            label="Tagdienst",
            start_time="06:00",
            end_time="21:59",
        )
        client.force_login(staff_user)
        params = {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "events",
            "doc_type": "",
            "view": "",
        }

        page = client.get(reverse("core:zeitstrom"), params)
        assert page.content.decode().count('hx-sync="#feed-list:replace"') == 2
        requested = {**params, "time_filter": str(selected.pk)}

        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            requested,
            headers=_filter_transition_headers(page),
        )

        assert response.status_code == 200
        assert 'hx-swap-oob="innerHTML"' in response.content.decode()
        validators = json.loads(response["X-Zeitstrom-Validators"])
        assert set(validators) == {"feed", "tally_topology"}
        assert response["ETag"] == validators["feed"]
        assert all(re.fullmatch(r'"[0-9a-f]{64}"', value) for value in validators.values())
        state = resolve_zeitstrom_state(requested, facility)
        assert validators["feed"] == build_zeitstrom_component_validator(
            staff_user,
            facility,
            state,
            "feed",
        )
        assert validators["tally_topology"] == build_zeitstrom_tally_topology_validator(
            staff_user,
            facility,
            state,
            tally_summary=response.context["tally_summary"],
        )

    @pytest.mark.parametrize("position", ["previous", "requested"])
    @pytest.mark.parametrize("change", ["delete", "deactivate"])
    def test_time_filter_transition_reloads_when_requested_filter_is_no_longer_canonical(
        self,
        client,
        staff_user,
        facility,
        change,
        position,
    ):
        previous = TimeFilter.objects.create(
            facility=facility,
            label="Verschwindender Dienst",
            start_time="00:00",
            end_time="23:59",
        )
        requested = TimeFilter.objects.create(
            facility=facility,
            label="Zieldienst",
            start_time="00:00",
            end_time="23:59",
        )
        client.force_login(staff_user)
        params = {
            "date": timezone.localdate().isoformat(),
            "time_filter": str(previous.pk),
            "type": "events",
            "doc_type": "",
            "view": "",
        }
        page = client.get(reverse("core:zeitstrom"), params)
        headers = _filter_transition_headers(page)
        requested_id = str(requested.pk)
        stale = previous if position == "previous" else requested
        if change == "delete":
            stale.delete()
        else:
            stale.is_active = False
            stale.save(update_fields=["is_active"])

        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {**params, "time_filter": requested_id},
            headers=headers,
        )

        assert response.status_code == 409
        assert response.content == b""
        assert "X-Zeitstrom-Validators" not in response

    @pytest.mark.parametrize("change", ["rename", "add", "eligibility"])
    def test_time_filter_transition_reloads_when_tally_structure_changed_since_page_render(
        self,
        client,
        staff_user,
        doc_type_contact,
        change,
    ):
        doc_type_contact.tally_enabled = True
        doc_type_contact.save(update_fields=["tally_enabled"])
        client.force_login(staff_user)
        params = {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "events",
            "doc_type": "",
            "view": "",
        }
        page = client.get(reverse("core:zeitstrom"), params)
        headers = _filter_transition_headers(page)
        if change == "rename":
            doc_type_contact.name = "Nach Seitenaufbau umbenannt"
            doc_type_contact.save(update_fields=["name"])
        elif change == "add":
            DocumentType.objects.create(
                facility=doc_type_contact.facility,
                name="Nach Seitenaufbau ergänzt",
                tally_enabled=True,
            )
        else:
            doc_type_contact.tally_enabled = False
            doc_type_contact.save(update_fields=["tally_enabled"])

        response = client.get(reverse("core:zeitstrom_feed_partial"), params, headers=headers)

        assert response.status_code == 409
        assert response.content == b""
        assert "X-Zeitstrom-Validators" not in response

    @pytest.mark.parametrize(
        ("header", "value"),
        [
            ("X-Zeitstrom-Previous-State", None),
            (
                "X-Zeitstrom-Previous-State",
                json.dumps(
                    {
                        "date": timezone.localdate().isoformat(),
                        "time_filter": "all",
                        "type": "activities",
                        "doc_type": "",
                        "view": "",
                    }
                ),
            ),
            ("X-Zeitstrom-Validators", json.dumps({"tally_topology": '"wrong"'})),
        ],
    )
    def test_time_filter_transition_rejects_missing_or_mismatched_baseline(
        self,
        client,
        staff_user,
        header,
        value,
    ):
        client.force_login(staff_user)
        params = {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "events",
            "doc_type": "",
            "view": "",
        }
        page = client.get(reverse("core:zeitstrom"), params)
        headers = _filter_transition_headers(page)
        if value is None:
            del headers[header]
        else:
            headers[header] = value

        response = client.get(reverse("core:zeitstrom_feed_partial"), params, headers=headers)

        assert response.status_code == 409
        assert response.content == b""
        assert "X-Zeitstrom-Validators" not in response

    def test_time_filter_snapshot_contract_is_limited_to_scoped_normal_htmx_transitions(
        self,
        client,
        staff_user,
    ):
        client.force_login(staff_user)
        params = {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "events",
            "doc_type": "",
            "view": "",
        }
        scope = _scope_for_client(client)

        ordinary = client.get(
            reverse("core:zeitstrom_feed_partial"),
            params,
            headers={"HX-Request": "true", "X-Zeitstrom-Page-Scope": scope},
        )
        feed_only = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {**params, "feed_only": "1"},
            headers={
                "HX-Request": "true",
                "X-Zeitstrom-Filter-Transition": "time_filter",
                "X-Zeitstrom-Page-Scope": scope,
            },
        )
        stale_scope = client.get(
            reverse("core:zeitstrom_feed_partial"),
            params,
            headers={
                "HX-Request": "true",
                "X-Zeitstrom-Filter-Transition": "time_filter",
                "X-Zeitstrom-Page-Scope": "0" * 64,
            },
        )

        assert "X-Zeitstrom-Validators" not in ordinary
        assert "X-Zeitstrom-Validators" not in feed_only
        assert stale_scope.status_code == 409
        assert stale_scope.content == b""
        assert stale_scope["Cache-Control"] == "no-store, private"

    def test_followup_is_bound_to_scope_and_exact_watch_snapshot(
        self,
        client,
        staff_user,
        facility,
        doc_type_contact,
    ):
        client.force_login(staff_user)
        scope = _scope_for_client(client)
        watched = _watch(client, params={"type": "events"}, scope=scope)
        expected = watched.json()["validators"]["feed"]

        changed_event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"type": "events", "feed_only": "1"},
            headers={
                "X-Zeitstrom-Watch": "1",
                "X-Zeitstrom-Page-Scope": scope,
                "If-Match": expected,
            },
        )

        assert response.status_code == 412
        assert response.content == b""
        assert response["ETag"] != expected

        changed_event.delete()
        returned_to_a = _watch(
            client,
            params={"type": "events"},
            etag=watched["ETag"],
            validators=watched.json()["validators"],
            scope=scope,
        )
        assert returned_to_a.status_code == 304

    def test_watch_followup_rejects_missing_or_stale_page_scope(self, client, staff_user, second_facility):
        client.force_login(staff_user)
        old_scope = _scope_for_client(client)
        missing = client.get(
            reverse("core:zeitstrom_current_shift_partial"),
            headers={"X-Zeitstrom-Watch": "1", "If-Match": '"invalid"'},
        )
        assert missing.status_code == 409

        staff_user.facility = second_facility
        staff_user.save(update_fields=["facility"])
        stale = client.get(
            reverse("core:zeitstrom_current_shift_partial"),
            headers={
                "X-Zeitstrom-Watch": "1",
                "X-Zeitstrom-Page-Scope": old_scope,
                "If-Match": '"invalid"',
            },
        )
        assert stale.status_code == 409
        assert stale.content == b""

        current_scope = _scope_for_client(client)
        staff_user.role = User.Role.ASSISTANT
        staff_user.save(update_fields=["role"])
        client.force_login(staff_user)
        stale_role = client.get(
            reverse("core:zeitstrom_focus_box_partial"),
            headers={
                "X-Zeitstrom-Watch": "1",
                "X-Zeitstrom-Page-Scope": current_scope,
                "If-Match": '"invalid"',
            },
        )
        assert stale_role.status_code == 409
        assert stale_role.content == b""

    def test_tally_topology_revision_tracks_visible_name_and_eligibility(
        self,
        client,
        staff_user,
        doc_type_contact,
    ):
        client.force_login(staff_user)
        first = _watch(client)
        previous = first.json()["validators"]

        doc_type_contact.name = "Umbenannte Strichliste"
        doc_type_contact.save(update_fields=["name"])
        renamed = _watch(client, validators=previous)
        assert renamed.json()["components"] == ["tally_topology"]

        previous.update(renamed.json()["validators"])
        doc_type_contact.tally_enabled = False
        doc_type_contact.save(update_fields=["tally_enabled"])
        removed = _watch(client, validators=previous)
        assert "tally_topology" in removed.json()["components"]

        previous.update(removed.json()["validators"])
        DocumentType.objects.create(
            facility=doc_type_contact.facility,
            name="Neue Strichliste",
            tally_enabled=True,
        )
        added = _watch(client, validators=previous)
        assert "tally_topology" in added.json()["components"]

    def test_tally_topology_changes_at_shift_boundary(self, staff_user, facility):
        early = TimeFilter.objects.create(
            facility=facility,
            label="Früh",
            start_time="06:00",
            end_time="13:59",
        )
        late = TimeFilter.objects.create(
            facility=facility,
            label="Spät",
            start_time="14:00",
            end_time="21:59",
        )
        day = timezone.localdate()
        early_now = timezone.make_aware(datetime.combine(day, datetime.min.time()).replace(hour=10))
        late_now = early_now.replace(hour=16)
        early_state = resolve_zeitstrom_state({}, facility, today=day, now=early_now)
        late_state = resolve_zeitstrom_state({}, facility, today=day, now=late_now)

        assert early_state.live_context.time_filter == early
        assert late_state.live_context.time_filter == late
        assert build_zeitstrom_tally_topology_validator(
            staff_user,
            facility,
            early_state,
        ) != build_zeitstrom_tally_topology_validator(
            staff_user,
            facility,
            late_state,
        )

    def test_page_exposes_initial_tally_topology_validator(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        match = re.search(r'data-tally-topology="([^"]+)"', response.content.decode())
        assert match is not None
        assert match.group(1).startswith("&quot;")

    def test_tally_counts_partial_contains_only_authoritative_count_spans(
        self, client, staff_user, facility, doc_type_contact
    ):
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            is_anonymous=True,
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        scope = _scope_for_client(client)
        validator = _watch(client, scope=scope).json()["validators"]["tally_counts"]

        response = client.get(
            reverse("core:zeitstrom_tally_counts_partial"),
            headers={
                "X-Zeitstrom-Watch": "1",
                "X-Zeitstrom-Page-Scope": scope,
                "If-Match": validator,
            },
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert f'id="tally-count-{doc_type_contact.pk}"' in content
        assert 'data-confirmed-count="1"' in content
        assert "1 Kontakt für Kontakt" in content
        assert "tally-row-" not in content
        assert "<form" not in content
        assert "hx-post" not in content
        assert "tally-panel" not in content

    def test_historical_tally_counts_partial_is_empty(self, client, staff_user):
        client.force_login(staff_user)
        yesterday = timezone.localdate() - timedelta(days=1)

        response = client.get(
            reverse("core:zeitstrom_tally_counts_partial"),
            {"date": yesterday.isoformat(), "time_filter": "all"},
        )

        assert response.status_code == 204
        assert response.content == b""

    def test_watch_feed_refresh_never_contains_tally_oob(self, client, staff_user):
        client.force_login(staff_user)
        params = {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "events",
            "doc_type": "",
            "view": "",
            "feed_only": "1",
        }
        scope = _scope_for_client(client)
        validator = _watch(client, params=params, scope=scope).json()["validators"]["feed"]

        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            params,
            headers={
                "X-Zeitstrom-Watch": "1",
                "X-Zeitstrom-Page-Scope": scope,
                "If-Match": validator,
            },
        )

        assert response.status_code == 200
        assert 'hx-swap-oob="innerHTML"' not in response.content.decode()

    @pytest.mark.parametrize(
        ("url_name", "wrapper_id"),
        [
            ("zeitstrom_current_shift_partial", "zeitstrom-current-shift-slot"),
            ("zeitstrom_focus_box_partial", "zeitstrom-focus-box-slot"),
        ],
    )
    def test_chrome_partials_return_stable_wrappers(self, client, staff_user, url_name, wrapper_id):
        client.force_login(staff_user)
        scope = _scope_for_client(client)
        component = {
            "zeitstrom_current_shift_partial": "current_shift",
            "zeitstrom_focus_box_partial": "focus_box",
        }[url_name]
        validator = _watch(client, scope=scope).json()["validators"][component]

        response = client.get(
            reverse(f"core:{url_name}"),
            headers={
                "X-Zeitstrom-Watch": "1",
                "X-Zeitstrom-Page-Scope": scope,
                "If-Match": validator,
            },
        )

        assert response.status_code == 200
        assert response["ETag"] == validator
        assert f'id="{wrapper_id}"' in response.content.decode()

    def test_page_exposes_watch_contract_and_stable_slots(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()

        assert f'data-watch-url="{reverse("core:zeitstrom_watch")}"' in content
        assert 'id="zeitstrom-bans-slot"' in content
        # Refs #1917: Hausverbote sind nicht mehr an den Seiten-Reload
        # gebunden — der Watcher kennt jetzt eine eigene Banner-URL.
        assert f'data-bans-url="{reverse("core:zeitstrom_bans_partial")}"' in content
        assert 'id="zeitstrom-current-shift-slot"' in content
        assert 'id="zeitstrom-focus-box-slot"' in content
        assert content.index("zeitstrom-filter-state.js") < content.index("zeitstrom-live-watch.js")
        assert content.index("zeitstrom-live-watch.js") < content.index("offline-tally.js")
