"""Bestätigungsmarker des Offline-Replays (Refs #2289).

Der Offline-Replay deutete bis 0.99.1 die Antwort-*Form* als Erfolgsbeleg:
jede gefolgte Weiterleitung außer ``/login/`` galt als „gespeichert". Die
MFA-Middleware leitet eine Schreibanfrage aber **vor** der View auf
``/mfa/setup/`` bzw. ``/mfa/verify/`` um — die Speicherung fand nie statt,
die Antwort sah trotzdem nach Erfolg aus, und der Client verwarf die lokale,
serverseitig nirgends existierende Arbeit.

Diese Tests nageln die Serverseite des Vertrags fest: **nur** ein markierter
Replay bekommt nach einem festgeschriebenen Schreibvorgang die Antwort
``200`` + ``X-Offline-Replay-Result: saved``; jeder andere Aufrufer behält
seinen Redirect, und eine Middleware-Weiterleitung trägt den Marker nie.
"""

import json

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import Event, WorkItem
from core.views._offline_replay import (
    OFFLINE_REPLAY_HEADER,
    OFFLINE_REPLAY_PROTOCOL_CONFIRMED,
    OFFLINE_REPLAY_RESULT_HEADER,
    OFFLINE_REPLAY_RESULT_SAVED,
    is_offline_replay,
    saved_confirmation,
    wants_saved_confirmation,
)

# Replay der Protokollversion 2 — nur diese Fassung versteht die Bestätigung.
REPLAY_HEADERS = {
    "HTTP_X_OFFLINE_REPLAY": OFFLINE_REPLAY_PROTOCOL_CONFIRMED,
    "HTTP_ACCEPT": "application/json",
}

# Bestandsgerät mit dem Precache vor 0.99.2: sendet den alten Markerwert und
# muss byte-gleich den bisherigen Redirect-Kontrakt bekommen (Rollout-Schnitt).
LEGACY_REPLAY_HEADERS = {
    "HTTP_X_OFFLINE_REPLAY": "1",
    "HTTP_ACCEPT": "application/json",
}


def _event_payload(doc_type, client_record):
    return {
        "document_type": str(doc_type.pk),
        "client": str(client_record.pk),
        "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        "dauer": "15",
        "notiz": "Offline nachgetragen",
    }


def _assert_saved(response, expected_pk=None):
    assert response.status_code == 200, response.status_code
    assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) == OFFLINE_REPLAY_RESULT_SAVED
    body = json.loads(response.content)
    assert body["status"] == OFFLINE_REPLAY_RESULT_SAVED
    if expected_pk is not None:
        assert body["pk"] == str(expected_pk)


class TestMarkerHelper:
    def test_is_offline_replay_reads_the_request_marker(self, rf):
        assert is_offline_replay(rf.post("/events/new/", **{"HTTP_X_OFFLINE_REPLAY": "2"})) is True
        assert is_offline_replay(rf.post("/events/new/", **{"HTTP_X_OFFLINE_REPLAY": "1"})) is True
        assert is_offline_replay(rf.post("/events/new/")) is False

    def test_only_the_confirmed_protocol_version_wants_the_marker(self, rf):
        assert wants_saved_confirmation(rf.post("/events/new/", **{"HTTP_X_OFFLINE_REPLAY": "2"})) is True
        assert wants_saved_confirmation(rf.post("/events/new/", **{"HTTP_X_OFFLINE_REPLAY": "1"})) is False
        assert wants_saved_confirmation(rf.post("/events/new/")) is False

    def test_saved_confirmation_only_answers_marked_requests(self, rf):
        assert saved_confirmation(rf.post("/events/new/"), "pk-1") is None
        assert saved_confirmation(rf.post("/events/new/", **{"HTTP_X_OFFLINE_REPLAY": "1"}), "pk-1") is None
        response = saved_confirmation(rf.post("/events/new/", **{"HTTP_X_OFFLINE_REPLAY": "2"}), "pk-1")
        assert response is not None
        _assert_saved(response, "pk-1")

    def test_header_names_are_the_documented_contract(self):
        assert OFFLINE_REPLAY_HEADER == "X-Offline-Replay"
        assert OFFLINE_REPLAY_RESULT_HEADER == "X-Offline-Replay-Result"
        assert OFFLINE_REPLAY_RESULT_SAVED == "saved"
        assert OFFLINE_REPLAY_PROTOCOL_CONFIRMED == "2"


@pytest.mark.django_db
class TestEventReplayConfirmation:
    def test_create_replay_is_confirmed_with_the_marker(self, client, staff_user, doc_type_contact, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            _event_payload(doc_type_contact, client_identified),
            **REPLAY_HEADERS,
        )
        event = Event.objects.get(document_type=doc_type_contact, created_by=staff_user)
        _assert_saved(response, event.pk)

    def test_create_replay_leaves_no_flash_message_behind(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        # Die Bestaetigung ersetzt den Redirect — ohne Seitenaufruf bliebe eine
        # Flash-Meldung in der Sitzung liegen und poppte spaeter grundlos auf.
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            _event_payload(doc_type_contact, client_identified),
            **REPLAY_HEADERS,
        )
        assert [str(m) for m in get_messages(response.wsgi_request)] == []

    def test_normal_submit_keeps_its_redirect(self, client, staff_user, doc_type_contact, client_identified):
        client.force_login(staff_user)
        response = client.post(reverse("core:event_create"), _event_payload(doc_type_contact, client_identified))
        assert response.status_code == 302
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None

    def test_json_client_without_the_replay_marker_keeps_its_redirect(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            _event_payload(doc_type_contact, client_identified),
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None

    def test_repeated_replay_with_the_same_key_confirms_without_a_duplicate(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        client.force_login(staff_user)
        payload = _event_payload(doc_type_contact, client_identified)
        headers = dict(REPLAY_HEADERS, HTTP_X_IDEMPOTENCY_KEY="11111111-1111-4111-8111-111111111111")
        first = client.post(reverse("core:event_create"), payload, **headers)
        second = client.post(reverse("core:event_create"), payload, **headers)
        event = Event.objects.get(document_type=doc_type_contact, created_by=staff_user)
        _assert_saved(first, event.pk)
        _assert_saved(second, event.pk)
        assert Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).count() == 1

    def test_invalid_form_stays_a_422_without_the_marker(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {"document_type": str(doc_type_contact.pk), "occurred_at": "", "dauer": "15"},
            **REPLAY_HEADERS,
        )
        assert response.status_code == 422
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None

    def test_edit_replay_is_confirmed_with_the_marker(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "occurred_at": timezone.localtime(sample_event.occurred_at).strftime("%Y-%m-%dT%H:%M"),
                "dauer": "30",
                "notiz": "Offline korrigiert",
                "expected_updated_at": sample_event.updated_at.isoformat(),
            },
            **REPLAY_HEADERS,
        )
        _assert_saved(response, sample_event.pk)

    def test_edit_replay_conflict_stays_a_409_without_the_marker(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "occurred_at": timezone.localtime(sample_event.occurred_at).strftime("%Y-%m-%dT%H:%M"),
                "dauer": "30",
                "expected_updated_at": "2020-01-01T00:00:00+00:00",
            },
            **REPLAY_HEADERS,
        )
        assert response.status_code == 409
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None


@pytest.mark.django_db
class TestLegacyReplayKeepsTheOldContract:
    """Rollout-Schnitt: Bestandsgeräte behalten bis zum SW-Update ihren Kontrakt.

    Das Service-Worker-Update hängt an einer Nutzerbestätigung (ADR-030 §4),
    auf Bestandsgeräten läuft nach dem Deploy also weiter das alte Skript. Es
    kennt die Bestätigung nicht und würde eine ``200``-Antwort als ungültiges
    Formular bzw. transienten Fehler deuten — gespeicherte Arbeit erschiene
    dauerhaft als „nicht synchronisiert".
    """

    def test_event_create_with_the_old_marker_keeps_its_redirect(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            _event_payload(doc_type_contact, client_identified),
            **LEGACY_REPLAY_HEADERS,
        )
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/events/")
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None
        assert Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).count() == 1

    def test_event_update_with_the_old_marker_keeps_its_redirect(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "occurred_at": timezone.localtime(sample_event.occurred_at).strftime("%Y-%m-%dT%H:%M"),
                "dauer": "30",
                "expected_updated_at": sample_event.updated_at.isoformat(),
            },
            **LEGACY_REPLAY_HEADERS,
        )
        assert response.status_code == 302
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None

    def test_workitem_create_with_the_old_marker_keeps_its_redirect(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "client": str(client_identified.pk),
                "item_type": WorkItem.ItemType.TASK,
                "title": "Alt-Client-Aufgabe",
                "priority": WorkItem.Priority.NORMAL,
            },
            **LEGACY_REPLAY_HEADERS,
        )
        assert response.status_code == 302
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None
        assert WorkItem.objects.filter(title="Alt-Client-Aufgabe").count() == 1

    def test_workitem_update_with_the_old_marker_keeps_its_redirect(self, client, staff_user, sample_workitem):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            {
                "item_type": sample_workitem.item_type,
                "title": "Alt-Client-Korrektur",
                "priority": sample_workitem.priority,
                "expected_updated_at": sample_workitem.updated_at.isoformat(),
            },
            **LEGACY_REPLAY_HEADERS,
        )
        assert response.status_code == 302
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None

    def test_repeated_old_marker_replay_still_deduplicates(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Der Idempotenz-Schutz gilt fuer beide Protokollversionen."""
        client.force_login(staff_user)
        payload = _event_payload(doc_type_contact, client_identified)
        headers = dict(LEGACY_REPLAY_HEADERS, HTTP_X_IDEMPOTENCY_KEY="22222222-2222-4222-8222-222222222222")
        first = client.post(reverse("core:event_create"), payload, **headers)
        second = client.post(reverse("core:event_create"), payload, **headers)
        assert first.status_code == 302
        assert second.status_code == 302
        assert second.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None
        assert Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).count() == 1


@pytest.mark.django_db
class TestWorkItemReplayConfirmation:
    def test_create_replay_is_confirmed_with_the_marker(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "client": str(client_identified.pk),
                "item_type": WorkItem.ItemType.TASK,
                "title": "Offline angelegt",
                "priority": WorkItem.Priority.NORMAL,
            },
            **REPLAY_HEADERS,
        )
        workitem = WorkItem.objects.get(title="Offline angelegt")
        _assert_saved(response, workitem.pk)

    def test_edit_replay_is_confirmed_with_the_marker(self, client, staff_user, sample_workitem):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            {
                "item_type": sample_workitem.item_type,
                "title": "Offline korrigiert",
                "priority": sample_workitem.priority,
                "expected_updated_at": sample_workitem.updated_at.isoformat(),
            },
            **REPLAY_HEADERS,
        )
        _assert_saved(response, sample_workitem.pk)


@pytest.mark.django_db
class TestAuthRedirectsCarryNoConfirmation:
    """Der Kern von #2289: eine Middleware-Weiterleitung ist keine Bestätigung."""

    def test_mfa_setup_redirect_neither_saves_nor_confirms(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        # Die Zwei-Faktor-Pflicht wird waehrend der Offline-Phase scharf
        # gestellt; der Replay trifft die Middleware VOR der View.
        client.force_login(staff_user)
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])

        response = client.post(
            reverse("core:event_create"),
            _event_payload(doc_type_contact, client_identified),
            **REPLAY_HEADERS,
        )

        assert response.status_code == 302
        assert response.headers["Location"].startswith("/mfa/setup/")
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None
        assert not Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).exists()

    def test_anonymous_replay_bounces_to_login_without_confirmation(self, client, doc_type_contact, client_identified):
        response = client.post(
            reverse("core:event_create"),
            _event_payload(doc_type_contact, client_identified),
            **REPLAY_HEADERS,
        )
        assert response.status_code == 302
        assert "/login/" in response.headers["Location"]
        assert response.headers.get(OFFLINE_REPLAY_RESULT_HEADER) is None
        assert not Event.objects.filter(document_type=doc_type_contact).exists()
