"""Tests for Streetwork-Offline Stage 3 conflict handling (Refs #575, #572).

The server-side contract under test is narrow but load-bearing:

* ``EventUpdateView`` must continue to re-render the edit form (HTML) for
  normal browser submissions when ``update_event`` raises a stale
  ``expected_updated_at`` conflict — otherwise we would break the existing
  UX for non-offline users.
* The same view must switch to a ``409 Conflict`` JSON payload when the
  caller signals a JSON/HTMX response via ``Accept: application/json`` or
  ``HX-Request: true``. The body must carry the current server state
  (``data_json``, ``updated_at``, ``document_type_name``) plus a copy of
  the stale timestamp the client sent, so that
  :file:`src/static/js/conflict-resolver.js` can render a diff without a
  second round-trip.
* The filtered ``data_json`` in the response must respect field-level
  sensitivity — a downgraded user must not learn restricted values by
  triggering a conflict on purpose.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import (
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
)


@pytest.fixture
def doc_type_with_high_field(facility):
    """NORMAL document type with a HIGH-sensitivity field override.

    Used to assert that the conflict-response filter drops fields the
    caller cannot read; staff may see NORMAL docs but not HIGH fields.
    """
    dt = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        sensitivity=DocumentType.Sensitivity.NORMAL,
        name="NormalMitHighField",
    )
    ft_bemerkung = FieldTemplate.objects.create(
        facility=facility,
        name="Bemerkung",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_risiko = FieldTemplate.objects.create(
        facility=facility,
        name="Risiko",
        field_type=FieldTemplate.FieldType.TEXT,
        sensitivity="high",
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_bemerkung, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_risiko, sort_order=1)
    return dt


def _stale_timestamp():
    """Return an ISO-8601 timestamp that is guaranteed to be stale.

    ``update_event`` compares the string form of ``updated_at.isoformat()``
    against the ``expected_updated_at`` POST value, so any value that is
    not identical triggers the concurrency branch.
    """
    return "2000-01-01T00:00:00+00:00"


@pytest.mark.django_db
class TestEventUpdateConflict:
    """Optimistic-concurrency contract of :class:`EventUpdateView.post`."""

    def test_event_update_returns_409_json_on_conflict(self, client, staff_user, sample_event):
        """A JSON/HTMX client sending a stale ``expected_updated_at`` must
        receive a 409 Conflict with a machine-readable server-state body."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "dauer": "99",
                "notiz": "offline-edit",
                "expected_updated_at": _stale_timestamp(),
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "conflict"
        assert "server_state" in payload
        assert payload["client_expected"] == _stale_timestamp()

    def test_event_update_409_triggered_by_htmx_header(self, client, staff_user, sample_event):
        """HTMX does not set Accept: application/json but ``HX-Request: true``
        must also opt into the JSON conflict branch.
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "dauer": "99",
                "notiz": "offline-edit",
                "expected_updated_at": _stale_timestamp(),
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "conflict"

    def test_event_update_html_fallback_remains_rerender(self, client, staff_user, sample_event):
        """A normal browser POST (no Accept header, no HX-Request) must keep
        the existing redirect-with-flash fallback — *not* a 409 JSON.
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "dauer": "99",
                "notiz": "offline-edit",
                "expected_updated_at": _stale_timestamp(),
            },
        )
        # redirect-to-edit flow keeps the user on the form with a flash message
        assert response.status_code == 302
        assert response["Content-Type"].startswith("text/html") or "Location" in response

    def test_conflict_response_includes_server_state(self, client, staff_user, sample_event):
        """Body carries the three fields the conflict resolver needs:
        ``data_json``, ``updated_at``, ``document_type_name``.
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "dauer": "5",
                "notiz": "local",
                "expected_updated_at": _stale_timestamp(),
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        server_state = response.json()["server_state"]
        # The event was created with dauer=15/notiz="Testnotiz" in the fixture.
        assert server_state["data_json"]["dauer"] == 15
        assert server_state["data_json"]["notiz"] == "Testnotiz"
        assert server_state["document_type_name"] == sample_event.document_type.name
        assert server_state["updated_at"] is not None

    def test_conflict_response_filters_restricted_fields(
        self,
        client,
        staff_user,
        facility,
        client_identified,
        doc_type_with_high_field,
    ):
        """Staff may see NORMAL doc types but not HIGH fields. A triggered
        conflict must not surface the HIGH value in ``server_state.data_json``.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_with_high_field,
            occurred_at=timezone.now(),
            data_json={"bemerkung": "öffentlich", "risiko": "streng-geheim"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "bemerkung": "local",
                "expected_updated_at": _stale_timestamp(),
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        data_json = response.json()["server_state"]["data_json"]
        assert data_json.get("bemerkung") == "öffentlich"
        assert "risiko" not in data_json, "HIGH field must not leak via conflict response"

    def test_no_conflict_json_still_redirects(self, client, staff_user, sample_event):
        """A successful JSON edit with a fresh (non-stale) expected_updated_at
        keeps the normal 302 redirect. The 409 branch is only for the
        conflict case.

        Refs #1338: seit der Token-Pflicht im JSON-Pfad muss ein gueltiger
        Token mitgeschickt werden -- vorher testete dieser Fall den JSON-Pfad
        ganz ohne ``expected_updated_at``, was jetzt (korrekterweise) den
        neuen 409-``missing-token``-Zweig treffen wuerde statt den hier
        geprueften Erfolgsfall.
        """
        client.force_login(staff_user)
        sample_event.refresh_from_db()
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {
                "dauer": "30",
                "notiz": "update",
                "expected_updated_at": sample_event.updated_at.isoformat(),
            },
            HTTP_ACCEPT="application/json",
        )
        # No conflict → regular success redirect to event_detail
        assert response.status_code == 302

    def test_event_update_returns_409_missing_token_without_expected_updated_at(self, client, staff_user, sample_event):
        """Refs #1338: JSON-/Offline-Replay-Clients MUESSEN ``expected_updated_at``
        mitschicken. Ein fehlender/leerer Token ist seit #1338 kein stilles
        No-Op mehr (silent Last-Write-Wins, K3), sondern ein expliziter 409
        mit eigener Fehlerkennung -- das Event bleibt dabei unveraendert.
        """
        client.force_login(staff_user)
        original_notiz = sample_event.data_json["notiz"]
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "99", "notiz": "offline-edit-ohne-token"},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "missing-token"
        assert payload["client_expected"] is None
        assert "server_state" in payload
        assert payload["server_state"]["updated_at"] is not None
        sample_event.refresh_from_db()
        assert sample_event.data_json["notiz"] == original_notiz, (
            "Event darf bei fehlendem Token nicht veraendert werden"
        )

    def test_event_update_htmx_without_token_also_returns_missing_token(self, client, staff_user, sample_event):
        """Der ``missing-token``-Zweig gilt fuer JEDEN JSON-wollenden Request
        (Accept: application/json ODER HX-Request), nicht nur fuer Accept-Header
        -- konsistent mit dem bestehenden Konflikt-Zweig (Refs #1338, #575).
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "99", "notiz": "offline-edit-ohne-token"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 409
        assert response.json()["error"] == "missing-token"

    def test_event_update_html_post_without_token_still_succeeds(self, client, staff_user, sample_event):
        """Regressionsschutz #1338: der HTML-Formular-Pfad (kein Accept:
        application/json, kein HX-Request) erzwingt den Versions-Token
        NICHT -- ``require_version_token`` gilt nur fuer
        ``_wants_json_response``. Ein normaler Browser-Submit ohne
        ``expected_updated_at`` bleibt unveraendert ein No-Op-Check.
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "50", "notiz": "html-ohne-token"},
        )
        assert response.status_code == 302

    def test_event_update_corrupt_token_returns_409_not_500(self, client, staff_user, sample_event):
        """Refs #1338: ein nicht-ISO-parsebarer Token darf im JSON-Pfad
        keinen ungefangenen ``ValueError`` (-> 500) mehr ausloesen. Defensiv
        wird das wie ein echter Konflikt behandelt (Server-Stand zur Review).
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "99", "notiz": "offline-edit", "expected_updated_at": "nicht-iso"},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "conflict"
        assert payload["client_expected"] == "nicht-iso"

    def test_event_update_corrupt_token_html_path_redirects_not_500(self, client, staff_user, sample_event):
        """Gegenprobe: derselbe korrupte Token im klassischen HTML-Pfad
        bleibt beim bisherigen messages+redirect-Verhalten (kein 500)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "99", "notiz": "offline-edit", "expected_updated_at": "nicht-iso"},
        )
        assert response.status_code == 302

    def test_offline_bundle_token_triggers_conflict_on_stale_replay(self, client, staff_user, sample_event):
        """F-07 (Refs #1109): End-to-End — der ``updated_at``-Token, den das
        Offline-Bundle pro Event serialisiert, stellt beim Replay den
        serverseitigen Konflikt-Check scharf.

        Ablauf: Bundle wird gebaut (trägt den frischen Token) → der Server-
        Datensatz wird zwischenzeitlich verändert (Token veraltet) → der
        Offline-Replay schickt den *alten* Bundle-Token als
        ``expected_updated_at`` → Server muss 409 statt 302 liefern. Genau
        dieser Pfad war tot, solange das Bundle keinen Token mitführte.
        """
        from core.services.system import build_client_offline_bundle

        # 1) Token wie der Offline-Client ihn aus dem Bundle bekäme.
        bundle = build_client_offline_bundle(staff_user, sample_event.facility, sample_event.client)
        token = next(e for e in bundle["events"] if e["pk"] == str(sample_event.pk))["updated_at"]
        assert token, "Bundle muss einen Optimistic-Lock-Token liefern (F-07)"

        # 2) Server-seitige Konkurrenz-Änderung → Token veraltet.
        sample_event.data_json = {"dauer": 7, "notiz": "server-seitig geaendert"}
        sample_event.save()

        # 3) Offline-Replay mit dem (jetzt veralteten) Bundle-Token.
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "99", "notiz": "offline-edit", "expected_updated_at": token},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409, "Offline-Replay mit Bundle-Token muss Konflikt erkennen"
        assert response.json()["error"] == "conflict"

    def test_offline_bundle_token_allows_clean_replay(self, client, staff_user, sample_event):
        """Gegenprobe zu F-07: Ist der Bundle-Token noch aktuell (keine
        Server-Änderung zwischendurch), geht der Replay normal durch (302).
        Der Token darf den legitimen Sync nicht fälschlich blockieren.
        """
        from core.services.system import build_client_offline_bundle

        bundle = build_client_offline_bundle(staff_user, sample_event.facility, sample_event.client)
        token = next(e for e in bundle["events"] if e["pk"] == str(sample_event.pk))["updated_at"]

        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "99", "notiz": "offline-edit", "expected_updated_at": token},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302

    def test_event_update_returns_422_json_on_invalid_form(self, client, staff_user, sample_event):
        """Refs #1111: ein Offline-Replay (Accept: application/json) mit einem
        UNGUELTIGEN Formular muss 422 + Feldfehler bekommen — nicht ein 200-
        Re-Render, das der Replay faelschlich als ``synced`` werten und damit den
        Edit still verwerfen wuerde (Datenverlust).
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "nicht-numerisch"},  # Number-Feld -> Validierung schlaegt fehl
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert payload["errors"], "422-Antwort muss die Feldfehler tragen"

    def test_event_update_html_invalid_form_still_rerenders_200(self, client, staff_user, sample_event):
        """Gegenprobe: ein normaler Browser-POST (kein Accept: application/json)
        mit ungueltigem Formular behaelt das 200-Re-Render mit inline-Fehlern —
        die 422-Sonderbehandlung gilt NUR fuer den JSON/Offline-Replay-Pfad.
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "nicht-numerisch"},
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")


@pytest.mark.django_db
class TestOfflineConflictShellViews:
    """Pure HTML scaffolds for ``/offline/conflicts/`` and
    ``/offline/conflicts/<uuid>/`` — they never render PII because the
    conflict data lives in client-side IndexedDB.
    """

    def test_conflict_list_requires_login(self, client):
        response = client.get(reverse("core:offline_conflict_list"))
        assert response.status_code in (302, 403)

    def test_conflict_list_renders_scaffold(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:offline_conflict_list"))
        assert response.status_code == 200
        assert b"conflict-list-view" in response.content

    def test_conflict_review_requires_login(self, client):
        response = client.get(
            reverse(
                "core:offline_conflict_review",
                kwargs={"pk": "00000000-0000-0000-0000-000000000000"},
            )
        )
        assert response.status_code in (302, 403)

    def test_conflict_review_renders_scaffold(self, client, staff_user):
        """Scaffold renders even for unknown event PKs — the merge UI pulls
        state from IndexedDB, not from the server."""
        client.force_login(staff_user)
        response = client.get(
            reverse(
                "core:offline_conflict_review",
                kwargs={"pk": "00000000-0000-0000-0000-000000000000"},
            )
        )
        assert response.status_code == 200
        assert b"conflict-resolver-view" in response.content
