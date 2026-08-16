"""Kurzlebige, capability-gebundene Ruecknahme der Strichliste (Refs #1913)."""

import re
import threading
from datetime import timedelta
from html import unescape
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import close_old_connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Event, User
from core.services.events import create_event


def _tally_event(facility, user, document_type, *, key="tally-undo-key"):
    return create_event(
        facility=facility,
        user=user,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={},
        is_anonymous=True,
        idempotency_key=key,
        created_via=Event.CreatedVia.TALLY,
    )


@pytest.mark.django_db
class TestTallyUndoService:
    def test_only_tally_creation_sets_the_persistent_provenance(self, facility, staff_user, doc_type_contact):
        regular = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        tally = _tally_event(facility, staff_user, doc_type_contact)

        assert regular.created_via == Event.CreatedVia.STANDARD
        assert tally.created_via == Event.CreatedVia.TALLY

    def test_own_fresh_tally_event_can_be_undone_exactly_once_idempotently(
        self, facility, staff_user, doc_type_contact
    ):
        from core.services.events.tally_undo import mint_tally_undo_token, undo_tally_event

        event = _tally_event(facility, staff_user, doc_type_contact)
        token = mint_tally_undo_token(event)

        first = undo_tally_event(facility, staff_user, token)
        second = undo_tally_event(facility, staff_user, token)

        event.refresh_from_db()
        assert first.pk == second.pk == event.pk
        assert event.is_deleted is True
        delete_audits = AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(event.pk))
        assert delete_audits.count() == 1
        assert delete_audits.get().detail["source"] == "tally_undo"

    def test_regular_events_and_unrelated_deletions_are_never_idempotent_undo_replays(
        self, facility, staff_user, doc_type_contact
    ):
        from core.services.events.tally_undo import TallyUndoDenied, mint_tally_undo_token, undo_tally_event

        event = _tally_event(facility, staff_user, doc_type_contact)
        token = mint_tally_undo_token(event)
        event.is_deleted = True
        event.save(update_fields=["is_deleted"])

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

    def test_policy_drift_rejects_a_previously_minted_capability(self, facility, staff_user, doc_type_contact):
        from core.services.events.tally_undo import TallyUndoDenied, mint_tally_undo_token, undo_tally_event

        event = _tally_event(facility, staff_user, doc_type_contact)
        token = mint_tally_undo_token(event)
        doc_type_contact.tally_enabled = False
        doc_type_contact.save(update_fields=["tally_enabled"])

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

    def test_expired_signature_cannot_be_reused_for_a_fresh_event(self, facility, staff_user, doc_type_contact):
        from core.services.events.tally_undo import TallyUndoDenied, mint_tally_undo_token, undo_tally_event

        event = _tally_event(facility, staff_user, doc_type_contact)
        old_epoch = timezone.now().timestamp() - 61
        with patch("django.core.signing.time.time", return_value=old_epoch):
            token = mint_tally_undo_token(event)

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

    @pytest.mark.parametrize("mutation", ["tamper", "wrong_user", "regular", "expired", "modified", "client", "case"])
    def test_capability_rejects_every_non_matching_event_state(
        self, facility, staff_user, doc_type_contact, case_open, client_identified, mutation
    ):
        from core.services.events.tally_undo import TallyUndoDenied, mint_tally_undo_token, undo_tally_event

        event = _tally_event(facility, staff_user, doc_type_contact)
        token = mint_tally_undo_token(event)
        if mutation == "tamper":
            token += "x"
        elif mutation == "wrong_user":
            other = User.objects.create_user(
                username="other-tally-user", role=User.Role.STAFF, facility=facility, is_staff=True
            )
            staff_user = other
        elif mutation == "regular":
            event.created_via = Event.CreatedVia.STANDARD
            event.save(update_fields=["created_via"])
        elif mutation == "expired":
            Event.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(seconds=61))
        elif mutation == "modified":
            event.data_json = {"changed": True}
            event.save()
        elif mutation == "client":
            event.client = client_identified
            event.save(update_fields=["client"])
        elif mutation == "case":
            event.case = case_open
            event.save(update_fields=["case"])

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

        event.refresh_from_db()
        assert event.is_deleted is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
def test_parallel_undo_posts_are_serialized_and_replay_the_same_delete(facility, staff_user, doc_type_contact):
    """Zwei PostgreSQL-Transaktionen dürfen nur einen Delete-Audit erzeugen."""
    from core.services.events.tally_undo import mint_tally_undo_token, undo_tally_event

    event = _tally_event(facility, staff_user, doc_type_contact)
    token = mint_tally_undo_token(event)
    barrier = threading.Barrier(2)
    errors = []
    result_ids = []

    def undo():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            result_ids.append(undo_tally_event(facility, staff_user, token).pk)
        except Exception as exc:  # pragma: no cover - Assertion below exposes failures
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=undo), threading.Thread(target=undo)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert result_ids == [event.pk, event.pk]
    assert AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(event.pk)).count() == 1


@pytest.mark.django_db
class TestTallyUndoView:
    @pytest.mark.parametrize("replay_path", ["cache", "database"])
    def test_replay_after_event_change_never_mints_a_new_undo_capability(
        self, client, facility, staff_user, doc_type_contact, replay_path
    ):
        client.force_login(staff_user)
        payload = {"document_type": str(doc_type_contact.pk), "idempotency_key": "changed-replay-key"}
        created = client.post(
            reverse("core:tally_increment"),
            payload,
            HTTP_X_IDEMPOTENCY_KEY="changed-replay-key",
            HTTP_HX_REQUEST="true",
        )
        original_token = created.context["tally_undo_token"]
        event = Event.objects.get(document_type=doc_type_contact)
        event.data_json = {"changed": True}
        event.save()
        if replay_path == "database":
            cache.clear()

        replay = client.post(
            reverse("core:tally_increment"),
            payload,
            HTTP_X_IDEMPOTENCY_KEY="changed-replay-key",
            HTTP_HX_REQUEST="true",
        )

        assert replay.status_code == 200
        assert replay.context["tally_undo_token"] is None
        assert 'data-testid="tally-undo"' not in replay.content.decode()
        denied = client.post(reverse("core:tally_undo"), {"token": original_token})
        assert denied.status_code == 404
        event.refresh_from_db()
        assert event.is_deleted is False

    def test_htmx_undo_preserves_the_complete_visible_zeitstrom_filter_state(
        self, client, facility, staff_user, doc_type_contact
    ):
        client.force_login(staff_user)
        filter_state = {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "events",
            "doc_type": str(doc_type_contact.pk),
            "view": "",
        }
        payload = {
            "document_type": str(doc_type_contact.pk),
            "idempotency_key": "undo-filter-state-key",
            **filter_state,
        }
        created = client.post(
            reverse("core:tally_increment"),
            payload,
            HTTP_X_IDEMPOTENCY_KEY="undo-filter-state-key",
            HTTP_HX_REQUEST="true",
        )

        created_html = created.content.decode()
        undo_form = re.search(
            r'<form[^>]*data-tally-undo-expires-at="[^"]*"[^>]*>(.*?)</form>',
            created_html,
            re.DOTALL,
        )
        assert undo_form is not None
        undo_payload = {
            name: unescape(value)
            for name, value in re.findall(r'<input[^>]*name="([^"]+)" value="([^"]*)"', undo_form.group(1))
        }
        assert set(filter_state).issubset(undo_payload)
        assert set(created.context["tally_context"]).issubset(undo_payload)
        for name, value in filter_state.items():
            assert f'name="{name}" value="{value}"' in undo_form.group(1)
        undone = client.post(reverse("core:tally_undo"), undo_payload, HTTP_HX_REQUEST="true")

        assert undone.status_code == 200
        assert undone.context["tally_filter_state"] == filter_state
        undone_html = undone.content.decode()
        for name, value in filter_state.items():
            assert f'name="{name}" value="{value}"' in undone_html

    def test_assistant_can_undo_own_tally_without_general_delete_permission(
        self, client, facility, assistant_user, doc_type_contact
    ):
        from core.services.events.tally_undo import mint_tally_undo_token

        event = _tally_event(facility, assistant_user, doc_type_contact)
        client.force_login(assistant_user)

        response = client.post(reverse("core:tally_undo"), {"token": mint_tally_undo_token(event)})

        assert response.status_code == 302
        event.refresh_from_db()
        assert event.is_deleted is True

    def test_success_replaces_only_the_affected_row_and_preserves_no_js_redirect(
        self, client, facility, staff_user, doc_type_contact
    ):
        client.force_login(staff_user)
        created = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(doc_type_contact.pk), "idempotency_key": "undo-view-key"},
            HTTP_X_IDEMPOTENCY_KEY="undo-view-key",
            HTTP_HX_REQUEST="true",
        )
        token = created.context["tally_undo_token"]
        assert 'data-tally-undo-expires-at="' in created.content.decode()

        undone = client.post(reverse("core:tally_undo"), {"token": token}, HTTP_HX_REQUEST="true")

        content = undone.content.decode()
        assert undone.status_code == 200
        assert undone["HX-Retarget"] == f"#tally-row-{doc_type_contact.pk}"
        assert undone["HX-Reswap"] == "outerHTML"
        assert undone["HX-Trigger-After-Swap"] == "zeitstrom-changed"
        assert f'id="tally-row-{doc_type_contact.pk}"' in content
        assert 'data-testid="tally-undo"' not in content
        assert Event.objects.get(document_type=doc_type_contact).is_deleted is True

        replay = client.post(reverse("core:tally_undo"), {"token": token, "next": "//evil.example"})
        assert replay.status_code == 302
        assert replay["Location"] == "/"

    def test_foreign_or_tampered_token_has_the_same_non_disclosing_response(
        self, client, facility, staff_user, doc_type_contact
    ):
        from core.services.events.tally_undo import mint_tally_undo_token

        event = _tally_event(facility, staff_user, doc_type_contact)
        token = mint_tally_undo_token(event)
        other = User.objects.create_user(
            username="different-user", role=User.Role.STAFF, facility=facility, is_staff=True
        )
        client.force_login(other)

        foreign = client.post(reverse("core:tally_undo"), {"token": token})
        tampered = client.post(reverse("core:tally_undo"), {"token": token + "x"})

        assert foreign.status_code == tampered.status_code == 404

    def test_endpoint_requires_csrf(self, facility, staff_user, doc_type_contact):
        from core.services.events.tally_undo import mint_tally_undo_token

        event = _tally_event(facility, staff_user, doc_type_contact)
        client = Client(enforce_csrf_checks=True)
        client.force_login(staff_user)

        response = client.post(reverse("core:tally_undo"), {"token": mint_tally_undo_token(event)})

        assert response.status_code == 403
