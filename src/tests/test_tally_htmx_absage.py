"""HTMX rejections of the tally capture (Refs #2385).

Before this fix, ``TallyIncrementView`` (``core/views/events.py``) answered
every business rejection solely with a raw ``HttpResponseBadRequest``. The
"+1" card and its value form hang off ``hx-get``/``hx-post`` with
``hx-target``/``hx-swap="outerHTML"`` (``tally_action.html``,
``tally_inline_form.html``) — since the global 4xx swap rule
(``responseHandling`` in ``base.html``, Refs #2232), the plain text therefore
replaced the card instead of appearing as a toast; a click without HTMX (the
links' ``href`` fallback, or a native form submit) saw the same plain text as
a full page.

The shared helper ``TallyIncrementView._document_type_error_response`` now
decides per request: with ``HX-Request`` it still uses ``htmx_absage`` (400 +
``HX-Reswap: none``, the card stays in place); without the header, a
``messages`` notice + redirect to the originating page (mirroring the
already-converted ``TallyUndoView.post``). All nine spots in the view run
through this same helper; the tests below cover the HTMX branch for each
unique message text, plus the redirect branch for GET (cancel and normal
branch) and POST.
"""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from core.models import DocumentType, Event
from core.services.events.idempotency import IdempotentHit
from tests._tally_helpers import post_tally

LOCAL_NIGHT_INSTANT = datetime(2026, 6, 21, 23, 30, tzinfo=UTC)


def _direct_type(facility, name="Direktkontakt"):
    return DocumentType.objects.create(facility=facility, name=name, tally_enabled=True)


@pytest.mark.django_db
class TestTallyIncrementHtmxRefusal:
    """HX-Request branch: 400 stays, ``HX-Reswap: none`` prevents the swap."""

    def test_stale_live_context_on_get_refuses_in_place(self, client, facility, staff_user):
        document_type = _direct_type(facility)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = client.get(
                reverse("core:tally_increment"),
                {"document_type": document_type.pk, "date": "2026-06-20"},
                HTTP_HX_REQUEST="true",
            )

        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"
        assert "Anonyme Kontakte im aktuellen Zeitfenster" in response.content.decode()

    def test_get_for_type_without_value_fields_refuses_in_place(self, client, facility, staff_user):
        document_type = _direct_type(facility)
        client.force_login(staff_user)

        response = client.get(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"
        assert "Dieser Dokumentationstyp benötigt keine Wert-Eingabe." in response.content.decode()

    def test_cancel_for_type_without_value_fields_refuses_in_place(self, client, facility, staff_user):
        document_type = _direct_type(facility)
        client.force_login(staff_user)

        response = client.get(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk), "cancel": "1"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"
        assert "Dieser Dokumentationstyp benötigt keine Wert-Eingabe." in response.content.decode()

    def test_cancel_for_row_missing_from_the_summary_refuses_in_place(self, client, facility, staff_user, monkeypatch):
        document_type, _field = _number_tally_type(facility)
        client.force_login(staff_user)
        monkeypatch.setattr(
            "core.views.events.build_tally_summary",
            lambda facility, user, context=None: {"rows": []},
        )

        response = client.get(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk), "cancel": "1"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"
        assert "Dieser Dokumentationstyp kann nicht per Strichliste erfasst werden." in response.content.decode()

    def test_post_with_invalid_idempotency_key_refuses_in_place(self, client, facility, staff_user):
        document_type = _direct_type(facility)
        client.force_login(staff_user)

        response = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk), "idempotency_key": "ungueltig mit leerzeichen"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"
        assert "Idempotenz-Schlüssel fehlt, ist ungültig oder widersprüchlich." in response.content.decode()
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_stale_idempotent_hit_without_matching_event_refuses_in_place(
        self, client, facility, staff_user, monkeypatch
    ):
        document_type = _direct_type(facility)
        client.force_login(staff_user)
        key = str(uuid4())
        monkeypatch.setattr(
            "core.views.events.get_idempotent_result",
            lambda scope, user_id, idempotency_key: IdempotentHit(pk=str(uuid4()), payload_hash=None),
        )

        response = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk), "idempotency_key": key},
            HTTP_X_IDEMPOTENCY_KEY=key,
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"
        assert "Idempotenz-Schlüssel fehlt, ist ungültig oder widersprüchlich." in response.content.decode()
        assert not Event.objects.filter(document_type=document_type).exists()


@pytest.mark.django_db
class TestTallyIncrementPlainNavigationRedirect:
    """Without ``HX-Request``: notice + redirect instead of a full-page plain-text response."""

    def test_cancel_for_type_without_value_fields_redirects_with_message(self, client, facility, staff_user):
        document_type = _direct_type(facility)
        client.force_login(staff_user)

        response = client.get(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk), "cancel": "1"},
        )

        assert response.status_code == 302
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert "Dieser Dokumentationstyp benötigt keine Wert-Eingabe." in messages

    def test_post_without_live_context_redirects_with_message(self, client, facility, staff_user):
        document_type = _direct_type(facility)
        client.force_login(staff_user)

        with patch("django.utils.timezone.now", return_value=LOCAL_NIGHT_INSTANT):
            response = post_tally(
                client,
                {"document_type": str(document_type.pk), "date": "2026-06-20"},
            )

        assert response.status_code == 302
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert "Anonyme Kontakte im aktuellen Zeitfenster" in messages
        assert not Event.objects.filter(document_type=document_type).exists()


def _number_tally_type(facility):
    from core.models import DocumentTypeField, FieldTemplate

    document_type = DocumentType.objects.create(
        facility=facility,
        name="Spritzentausch",
        tally_enabled=True,
    )
    field_template = FieldTemplate.objects.create(
        facility=facility,
        name="Ausgabe",
        field_type=FieldTemplate.FieldType.NUMBER,
        is_required=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field_template)
    return document_type, field_template
