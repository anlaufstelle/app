"""Idempotenz-Vertrag der Strichlisten-Erfassung (Refs #1909)."""

from __future__ import annotations

import threading

import pytest
from django.core.cache import cache
from django.db import close_old_connections
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, EventHistory, FieldTemplate
from core.services.events import build_tally_summary, normalize_idempotency_key


@pytest.fixture(autouse=True)
def _clear_idempotency_cache():
    cache.clear()
    yield
    cache.clear()


def _summary_row(facility, user, document_type):
    summary = build_tally_summary(facility, user)
    return next(row for row in summary["rows"] if row["document_type"].pk == document_type.pk)


def _number_tally_type(facility):
    document_type = DocumentType.objects.create(
        facility=facility,
        name="Idempotenter Spritzentausch",
        tally_enabled=True,
    )
    field_template = FieldTemplate.objects.create(
        facility=facility,
        name="Idempotente Ausgabe",
        field_type=FieldTemplate.FieldType.NUMBER,
        is_required=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field_template)
    return document_type, field_template


@pytest.mark.django_db
class TestDirectTallyIdempotency:
    def test_server_rendered_key_deduplicates_retry_and_rotates_after_success(
        self,
        client,
        facility,
        staff_user,
        doc_type_contact,
    ):
        client.force_login(staff_user)
        row = _summary_row(facility, staff_user, doc_type_contact)
        key = row["idempotency_key"]
        assert normalize_idempotency_key(key) == key
        action_html = render_to_string("core/zeitstrom/partials/tally_action.html", {"row": row})
        assert f'name="idempotency_key" value="{key}"' in action_html
        assert f'"X-Idempotency-Key": "{key}"' in action_html
        payload = {"document_type": str(doc_type_contact.pk), "idempotency_key": key}
        audit_before = AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count()

        first = client.post(
            reverse("core:tally_increment"),
            payload,
            HTTP_X_IDEMPOTENCY_KEY=key,
            HTTP_HX_REQUEST="true",
        )
        retry = client.post(
            reverse("core:tally_increment"),
            payload,
            HTTP_X_IDEMPOTENCY_KEY=key,
            HTTP_HX_REQUEST="true",
        )

        assert first.status_code == retry.status_code == 200
        assert Event.objects.filter(document_type=doc_type_contact, is_anonymous=True).count() == 1
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count() == audit_before + 1
        assert EventHistory.objects.filter(event__document_type=doc_type_contact).count() == 1
        next_row = next(
            row for row in first.context["tally_summary"]["rows"] if row["document_type"].pk == doc_type_contact.pk
        )
        assert next_row["idempotency_key"] != key

        second_action = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(doc_type_contact.pk), "idempotency_key": next_row["idempotency_key"]},
            HTTP_HX_REQUEST="true",
        )

        assert second_action.status_code == 200
        assert Event.objects.filter(document_type=doc_type_contact, is_anonymous=True).count() == 2

    def test_canonical_header_is_accepted_and_db_replay_keeps_key(
        self,
        client,
        facility,
        staff_user,
        doc_type_contact,
    ):
        client.force_login(staff_user)
        key = _summary_row(facility, staff_user, doc_type_contact)["idempotency_key"]
        payload = {"document_type": str(doc_type_contact.pk)}
        url = reverse("core:tally_increment")

        first = client.post(url, payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true")
        cache.clear()
        db_replay = client.post(url, payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true")

        assert first.status_code == db_replay.status_code == 200
        event = Event.objects.get(document_type=doc_type_contact)
        assert event.idempotency_key == key
        assert EventHistory.objects.filter(event=event).count() == 1
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE, target_id=str(event.pk)).count() == 1

    @pytest.mark.parametrize("invalid_key", [None, "ungueltig mit leerzeichen"])
    def test_missing_or_invalid_key_is_rejected(
        self,
        client,
        staff_user,
        doc_type_contact,
        invalid_key,
    ):
        client.force_login(staff_user)
        payload = {"document_type": str(doc_type_contact.pk)}
        if invalid_key is not None:
            payload["idempotency_key"] = invalid_key

        response = client.post(reverse("core:tally_increment"), payload, HTTP_HX_REQUEST="true")

        assert response.status_code == 400
        assert not Event.objects.filter(document_type=doc_type_contact).exists()

    def test_conflicting_header_and_body_keys_are_rejected(
        self,
        client,
        facility,
        staff_user,
        doc_type_contact,
    ):
        client.force_login(staff_user)
        body_key = _summary_row(facility, staff_user, doc_type_contact)["idempotency_key"]
        header_key = _summary_row(facility, staff_user, doc_type_contact)["idempotency_key"]

        response = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(doc_type_contact.pk), "idempotency_key": body_key},
            HTTP_X_IDEMPOTENCY_KEY=header_key,
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert not Event.objects.filter(document_type=doc_type_contact).exists()

    def test_failed_write_does_not_consume_key(
        self,
        client,
        facility,
        staff_user,
        doc_type_contact,
        monkeypatch,
    ):
        import core.views.events as events_view

        client.force_login(staff_user)
        key = _summary_row(facility, staff_user, doc_type_contact)["idempotency_key"]
        payload = {"document_type": str(doc_type_contact.pk), "idempotency_key": key}
        original_create_event = events_view.create_event
        attempts = 0

        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("simulierter Schreibfehler")
            return original_create_event(*args, **kwargs)

        monkeypatch.setattr(events_view, "create_event", fail_once)
        client.raise_request_exception = False

        failed = client.post(reverse("core:tally_increment"), payload, HTTP_HX_REQUEST="true")
        retry = client.post(reverse("core:tally_increment"), payload, HTTP_HX_REQUEST="true")

        assert failed.status_code == 500
        assert retry.status_code == 200
        assert attempts == 2
        assert Event.objects.filter(document_type=doc_type_contact, is_anonymous=True).count() == 1


@pytest.mark.django_db
class TestNumberTallyIdempotency:
    def test_key_survives_get_validation_error_and_corrected_retry(self, client, facility, staff_user):
        document_type, number = _number_tally_type(facility)
        client.force_login(staff_user)
        key = _summary_row(facility, staff_user, document_type)["idempotency_key"]
        url = reverse("core:tally_increment")

        inline = client.get(
            url,
            {"document_type": str(document_type.pk), "idempotency_key": key},
            HTTP_HX_REQUEST="true",
        )
        assert inline.status_code == 200
        inline_content = inline.content.decode()
        assert f'name="idempotency_key" value="{key}"' in inline_content
        assert f'"X-Idempotency-Key": "{key}"' in inline_content

        invalid = client.post(
            url,
            {"document_type": str(document_type.pk), "idempotency_key": key, number.slug: "keine-zahl"},
            HTTP_HX_REQUEST="true",
        )
        assert invalid.status_code == 200
        invalid_content = invalid.content.decode()
        assert f'name="idempotency_key" value="{key}"' in invalid_content
        assert f'"X-Idempotency-Key": "{key}"' in invalid_content
        assert not Event.objects.filter(document_type=document_type).exists()

        corrected = {"document_type": str(document_type.pk), "idempotency_key": key, number.slug: "7"}
        first = client.post(url, corrected, HTTP_HX_REQUEST="true")
        retry = client.post(url, corrected, HTTP_HX_REQUEST="true")

        assert first.status_code == retry.status_code == 200
        assert key not in first.content.decode()
        assert "idempotency_key" in first.content.decode()
        event = Event.objects.get(document_type=document_type)
        assert event.data_json == {number.slug: 7}
        assert EventHistory.objects.filter(event=event).count() == 1
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE, target_id=str(event.pk)).count() == 1

    def test_same_key_with_different_numbers_returns_422(self, client, facility, staff_user):
        document_type, number = _number_tally_type(facility)
        client.force_login(staff_user)
        key = _summary_row(facility, staff_user, document_type)["idempotency_key"]
        url = reverse("core:tally_increment")

        first = client.post(
            url,
            {"document_type": str(document_type.pk), "idempotency_key": key, number.slug: "7"},
            HTTP_HX_REQUEST="true",
        )
        conflict = client.post(
            url,
            {"document_type": str(document_type.pk), "idempotency_key": key, number.slug: "8"},
            HTTP_HX_REQUEST="true",
        )

        assert first.status_code == 200
        assert conflict.status_code == 422
        assert "__all__" in conflict.json()["errors"]
        assert Event.objects.filter(document_type=document_type).count() == 1

    def test_cache_clear_replay_with_different_number_returns_422(self, client, facility, staff_user):
        document_type, number = _number_tally_type(facility)
        client.force_login(staff_user)
        key = _summary_row(facility, staff_user, document_type)["idempotency_key"]
        url = reverse("core:tally_increment")

        first = client.post(
            url,
            {"document_type": str(document_type.pk), "idempotency_key": key, number.slug: "7"},
            HTTP_HX_REQUEST="true",
        )
        cache.clear()
        conflict = client.post(
            url,
            {"document_type": str(document_type.pk), "idempotency_key": key, number.slug: "8"},
            HTTP_HX_REQUEST="true",
        )

        assert first.status_code == 200
        assert conflict.status_code == 422
        event = Event.objects.get(document_type=document_type)
        assert event.data_json == {number.slug: 7}
        assert event.idempotency_payload_hash is not None

    def test_header_only_then_body_fallback_is_same_payload(self, client, facility, staff_user):
        document_type, number = _number_tally_type(facility)
        client.force_login(staff_user)
        key = _summary_row(facility, staff_user, document_type)["idempotency_key"]
        url = reverse("core:tally_increment")
        business_payload = {"document_type": str(document_type.pk), number.slug: "7"}

        header_only = client.post(
            url,
            business_payload,
            HTTP_X_IDEMPOTENCY_KEY=key,
            HTTP_HX_REQUEST="true",
        )
        body_fallback = client.post(
            url,
            {**business_payload, "idempotency_key": key},
            HTTP_HX_REQUEST="true",
        )

        assert header_only.status_code == body_fallback.status_code == 200
        assert Event.objects.filter(document_type=document_type).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
def test_parallel_direct_tally_requests_create_one_event_and_one_audit(
    facility,
    staff_user,
    doc_type_contact,
    monkeypatch,
):
    """Zwei echte PostgreSQL-Verbindungen treffen gleichzeitig den DB-Backstop."""
    import core.views.events as events_view

    key = _summary_row(facility, staff_user, doc_type_contact)["idempotency_key"]
    payload = {"document_type": str(doc_type_contact.pk), "idempotency_key": key}
    barrier = threading.Barrier(2)
    original_create_event = events_view.create_event

    def synchronized_create_event(*args, **kwargs):
        barrier.wait(timeout=10)
        return original_create_event(*args, **kwargs)

    monkeypatch.setattr(events_view, "create_event", synchronized_create_event)
    clients = [Client(), Client()]
    for test_client in clients:
        test_client.force_login(staff_user)
    responses = []
    errors = []

    def submit(test_client):
        close_old_connections()
        try:
            responses.append(test_client.post(reverse("core:tally_increment"), payload, HTTP_HX_REQUEST="true"))
        except Exception as exc:  # pragma: no cover - im Erfolgsfall leer, Assertion darunter zeigt Details
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=submit, args=(test_client,)) for test_client in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert len(responses) == 2
    assert all(response.status_code == 200 for response in responses)
    assert sum(b'data-testid="tally-undo"' in response.content for response in responses) == 1
    event = Event.objects.get(document_type=doc_type_contact)
    assert EventHistory.objects.filter(event=event).count() == 1
    assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE, target_id=str(event.pk)).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
def test_parallel_number_tally_payload_collision_returns_422(
    facility,
    staff_user,
    monkeypatch,
):
    """7 und 8 mit demselben Key dürfen im Unique-Constraint-Race nicht beide erfolgreich sein."""
    import core.views.events as events_view

    document_type, number = _number_tally_type(facility)
    key = _summary_row(facility, staff_user, document_type)["idempotency_key"]
    barrier = threading.Barrier(2)
    original_create_event = events_view.create_event

    def synchronized_create_event(*args, **kwargs):
        barrier.wait(timeout=10)
        return original_create_event(*args, **kwargs)

    monkeypatch.setattr(events_view, "create_event", synchronized_create_event)
    clients = [Client(), Client()]
    for test_client in clients:
        test_client.force_login(staff_user)
    responses = []
    errors = []

    def submit(test_client, value):
        close_old_connections()
        try:
            responses.append(
                test_client.post(
                    reverse("core:tally_increment"),
                    {
                        "document_type": str(document_type.pk),
                        "idempotency_key": key,
                        number.slug: str(value),
                    },
                    HTTP_HX_REQUEST="true",
                )
            )
        except Exception as exc:  # pragma: no cover - im Erfolgsfall leer
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [
        threading.Thread(target=submit, args=(clients[0], 7)),
        threading.Thread(target=submit, args=(clients[1], 8)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert len(responses) == 2
    assert sorted(response.status_code for response in responses) == [200, 422]
    event = Event.objects.get(document_type=document_type)
    assert event.data_json in ({number.slug: 7}, {number.slug: 8})
    assert EventHistory.objects.filter(event=event).count() == 1
    assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE, target_id=str(event.pk)).count() == 1
