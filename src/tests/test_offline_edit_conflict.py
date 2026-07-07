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

Erweiterung (Refs #1351 Task 7): ``WorkItemUpdateView.post`` hatte bislang
GAR KEINEN JSON-Zweig — ein Versionskonflikt landete unabhängig vom
Accept-/HX-Request-Header immer im klassischen messages+redirect(302)-Pfad.
Für die generische Offline-Queue (die jedem 200/redirect als Erfolg folgt
und die Zeile dann löscht) bedeutete das: der Konflikt verschwand
ersatzlos — schlimmer als der ungefixte Event-Fall, weil hier sogar ein
gültiges Replay ohne Token still durchging. ``TestWorkItemUpdateConflict``
überträgt den Vertrag von ``TestEventUpdateConflict`` 1:1 auf WorkItems,
mit einem Unterschied: ``server_state`` trägt statt ``data_json``/
``document_type_name`` die WorkItem-Felder ``title``/``description``/
``status``/``updated_at`` direkt (WorkItems haben keine dynamischen,
sensitivitätsklassifizierten Felder wie Events, daher entfällt der
Filter-Test).

Erweiterung (Refs #1351 Task 8, #1387): die beiden Create-Views
(``EventCreateView``, ``WorkItemCreateView``) rendern bei ungueltigem
Formular bislang IMMER 200-HTML — unabhaengig vom Accept-Header. Fuer den
Offline-Replay bedeutet das denselben Datenverlust wie beim ungefixten
Update-Pfad: ein 200-Re-Render wird von der generischen Queue als Erfolg
gewertet und die lokale Aenderung geloescht, obwohl sie nie in der DB
gelandet ist. ``TestEventCreateInvalid422``/``TestWorkItemCreateInvalid422``
uebertragen den 422-Vertrag von ``TestEventUpdateConflict``/
``TestWorkItemUpdateConflict`` auf die Create-Pfade. Bei Events gibt es DREI
statt einem Invalid-Pfad (``meta_form``, ``data_form``, ``ValidationError``
aus ``create_event``); der Formular-Check bleibt bewusst an den ROHEN
``Accept: application/json``-Header gebunden (nicht ``HX-Request`` wie bei
409-Konflikten) — ein normaler HTMX-Submit behaelt sein 200-Re-Render mit
inline-Fehlern.
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
    WorkItem,
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
class TestWorkItemUpdateConflict:
    """Optimistic-concurrency contract of :class:`WorkItemUpdateView.post`.

    1:1 aus :class:`TestEventUpdateConflict` übertragen (Refs #1351 Task 7):
    ``WorkItemUpdateView.post`` fing Versionskonflikte bislang NUR klassisch
    (messages+redirect 302) ab — unabhängig vom Accept-/HX-Request-Header.
    Für die generische Offline-Queue (folgt jedem 200/redirect als Erfolg)
    bedeutete das: der Konflikt verschwindet ersatzlos, die Queue-Zeile wird
    als "synchronisiert" gelöscht. Reihenfolge und Assertions spiegeln die
    Event-Tests oben; ``server_state`` trägt WorkItem-Felder
    (``title``/``description``/``status``/``updated_at``) statt
    ``data_json``/``document_type_name`` — WorkItems haben keine
    dynamischen, sensitivitätsklassifizierten Felder, daher entfällt der
    Event-Filter-Test.

    Dieser Test ist gegen den heutigen Code (vor Task 7) ROT, sofern nicht
    anders vermerkt — siehe Docstrings der einzelnen Tests. Refs #1351.
    """

    def _payload(self, **overrides):
        payload = {"item_type": "task", "title": "Lokale Änderung", "priority": "normal"}
        payload.update(overrides)
        return payload

    def test_workitem_update_returns_409_json_on_conflict(self, client, staff_user, sample_workitem):
        """Ein JSON/HTMX-Client mit veraltetem ``expected_updated_at`` muss
        409 mit maschinenlesbarem Server-Stand bekommen — heute (vor Task 7)
        liefert dieser POST unveraendert 302 (ROT)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(expected_updated_at=_stale_timestamp()),
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "conflict"
        assert "server_state" in payload
        assert payload["client_expected"] == _stale_timestamp()

    def test_workitem_update_409_triggered_by_htmx_header(self, client, staff_user, sample_workitem):
        """HTMX setzt kein Accept: application/json, aber ``HX-Request: true``
        muss ebenfalls den JSON-Konflikt-Zweig ausloesen (ROT vor Task 7)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(expected_updated_at=_stale_timestamp()),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 409
        assert response.json()["error"] == "conflict"

    def test_workitem_update_html_fallback_remains_rerender(self, client, staff_user, sample_workitem):
        """Wächter (bereits heute grün): ein normaler Browser-POST (kein
        Accept-Header, kein HX-Request) behält den bestehenden
        redirect-mit-Flash-Fallback — *kein* 409-JSON. Task 7 darf den
        HTML-Pfad nicht veraendern."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(expected_updated_at=_stale_timestamp()),
        )
        assert response.status_code == 302

    def test_conflict_response_includes_server_state(self, client, staff_user, sample_workitem):
        """Body trägt die WorkItem-Felder, die der Konflikt-Resolver braucht:
        ``title``, ``description``, ``status``, ``updated_at`` — statt
        ``data_json``/``document_type_name`` wie beim Event-Pendant, weil
        WorkItems ihre Felder direkt als Spalten halten (ROT vor Task 7)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(expected_updated_at=_stale_timestamp()),
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        server_state = response.json()["server_state"]
        # sample_workitem: title="Test-Aufgabe", status=OPEN, description="".
        assert server_state["title"] == "Test-Aufgabe"
        assert server_state["description"] == ""
        assert server_state["status"] == "open"
        assert server_state["updated_at"] is not None

    def test_no_conflict_json_still_redirects(self, client, staff_user, sample_workitem):
        """Ein erfolgreicher JSON-Edit mit frischem (nicht veraltetem) Token
        behält den normalen 302-Redirect — der 409-Zweig gilt nur für den
        Konfliktfall. Bereits heute grün (kein neuer Zweig betroffen)."""
        client.force_login(staff_user)
        sample_workitem.refresh_from_db()
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(expected_updated_at=sample_workitem.updated_at.isoformat()),
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302

    def test_workitem_update_returns_409_missing_token_without_expected_updated_at(
        self, client, staff_user, sample_workitem
    ):
        """JSON-/Offline-Replay-Clients MUESSEN ``expected_updated_at``
        mitschicken. Ein fehlender/leerer Token ist kein stilles No-Op
        (silent Last-Write-Wins), sondern ein expliziter 409 mit eigener
        Fehlerkennung — das WorkItem bleibt dabei unveraendert (ROT vor
        Task 7: heute wuerde der Edit klaglos durchgehen)."""
        client.force_login(staff_user)
        original_title = sample_workitem.title
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(),
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "missing-token"
        assert payload["client_expected"] is None
        assert "server_state" in payload
        assert payload["server_state"]["updated_at"] is not None
        sample_workitem.refresh_from_db()
        assert sample_workitem.title == original_title, "WorkItem darf bei fehlendem Token nicht veraendert werden"

    def test_workitem_update_htmx_without_token_also_returns_missing_token(self, client, staff_user, sample_workitem):
        """Der ``missing-token``-Zweig gilt für JEDEN JSON-wollenden Request
        (Accept: application/json ODER HX-Request), nicht nur für den
        Accept-Header (ROT vor Task 7)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 409
        assert response.json()["error"] == "missing-token"

    def test_workitem_update_html_post_without_token_still_succeeds(self, client, staff_user, sample_workitem):
        """Regressionsschutz: der HTML-Formular-Pfad (kein Accept:
        application/json, kein HX-Request) erzwingt den Versions-Token
        NICHT — ``require_version_token`` gilt nur für
        ``_wants_json_response``. Ein normaler Browser-Submit ohne
        ``expected_updated_at`` bleibt ein No-Op-Check und die Aenderung
        geht durch. Bereits heute gruen."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(title="HTML ohne Token"),
        )
        assert response.status_code == 302
        sample_workitem.refresh_from_db()
        assert sample_workitem.title == "HTML ohne Token"

    def test_workitem_update_corrupt_token_returns_409_not_500(self, client, staff_user, sample_workitem):
        """Ein nicht-ISO-parsebarer Token darf im JSON-Pfad keinen
        ungefangenen ``ValueError`` (-> 500) ausloesen. Defensiv wird das wie
        ein echter Konflikt behandelt (Server-Stand zur Review). ROT vor
        Task 7 (heute: 302, kein 409)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(expected_updated_at="nicht-iso"),
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "conflict"
        assert payload["client_expected"] == "nicht-iso"

    def test_workitem_update_corrupt_token_html_path_redirects_not_500(self, client, staff_user, sample_workitem):
        """Gegenprobe: derselbe korrupte Token im klassischen HTML-Pfad
        bleibt beim bisherigen messages+redirect-Verhalten (kein 500).
        Bereits heute gruen."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            self._payload(expected_updated_at="nicht-iso"),
        )
        assert response.status_code == 302

    def test_workitem_update_returns_422_json_on_invalid_form(self, client, staff_user, sample_workitem):
        """Ein Offline-Replay (Accept: application/json) mit einem
        UNGUELTIGEN Formular muss 422 + Feldfehler bekommen — nicht ein
        200-Re-Render, das der Replay faelschlich als "synced" werten und
        damit den Edit still verwerfen wuerde (Datenverlust). ROT vor Task 7
        (heute: 200)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            {"item_type": "task", "priority": "normal"},  # title fehlt -> Form ungueltig
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert payload["errors"], "422-Antwort muss die Feldfehler tragen"

    def test_workitem_update_html_invalid_form_still_rerenders_200(self, client, staff_user, sample_workitem):
        """Gegenprobe: ein normaler Browser-POST (kein Accept:
        application/json) mit ungueltigem Formular behaelt das
        200-Re-Render mit inline-Fehlern — die 422-Sonderbehandlung gilt
        NUR für den JSON/Offline-Replay-Pfad. Bereits heute gruen."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk}),
            {"item_type": "task", "priority": "normal"},
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")


@pytest.mark.django_db
class TestEventCreateInvalid422:
    """Create-422-Contract für :class:`EventCreateView.post` (Refs #1351 Task 8, #1387).

    Analog zum Update-Pfad (:class:`TestEventUpdateConflict`), aber mit DREI
    statt einem Invalid-Pfad — ``EventCreateView.post`` prüft das Formular in
    drei Stufen, bevor das Event angelegt wird: ``meta_form``
    (Dokumentationstyp/Zeitpunkt), ``data_form`` (dynamische Felder je
    DocumentType) und eine ``ValidationError`` aus ``create_event``
    (Geschäftsregeln wie Fall/Person-Konsistenz, erst NACH beiden
    Formular-Checks geprüft, innerhalb der ``transaction.atomic()``). Alle
    drei rendern heute bei JEDEM Client dieselbe 200-HTML-Antwort — ein
    Offline-Replay mit rohem ``Accept: application/json`` deutet ein 200
    fälschlich als "synchronisiert" und verwirft den lokalen Edit
    (Datenverlust). Muster: der bestehende 422-Zweig in
    ``EventUpdateView.post``.

    Dieser Test ist gegen den heutigen Code ROT: alle drei Pfade liefern
    aktuell 200-HTML statt 422-JSON. Refs #1351, #1387.
    """

    def test_event_create_meta_form_invalid_returns_422_json(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Pfad 1: ``meta_form`` ungültig (``occurred_at`` fehlt)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                # occurred_at fehlt -> meta_form ungueltig
                "dauer": "15",
                "notiz": "offline-create",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert "occurred_at" in payload["errors"]
        assert not Event.objects.filter(created_by=staff_user).exists()

    def test_event_create_data_form_invalid_returns_422_json(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Pfad 2: ``data_form`` ungültig (``dauer`` ist ein NUMBER-Feld,
        siehe ``doc_type_contact``-Fixture)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "nicht-numerisch",
                "notiz": "offline-create",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert "dauer" in payload["errors"]
        assert not Event.objects.filter(created_by=staff_user).exists()

    def test_event_create_validation_error_returns_422_with_all_key(
        self, client, staff_user, facility, doc_type_contact, case_open
    ):
        """Pfad 3: beide Formulare valide, aber ``create_event`` wirft eine
        ``ValidationError`` (der POST-``client`` passt nicht zur Person des
        gewählten ``case`` — dieselbe Konstellation wie
        ``test_event_create_rejects_case_of_other_client`` in
        ``test_events_crud.py``). Die View fängt das über
        ``meta_form.add_error(None, e.message)`` auf, wodurch die Meldung
        unter dem ``__all__``-Key (Django NON_FIELD_ERRORS) landet."""
        from core.models import Client as ClientModel

        other = ClientModel.objects.create(
            facility=facility,
            pseudonym="Orca",
            contact_stage=ClientModel.ContactStage.IDENTIFIED,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(other.pk),
                "case": str(case_open.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "Mismatch",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert "__all__" in payload["errors"]
        assert not Event.objects.filter(created_by=staff_user).exists()

    def test_event_create_foreign_facility_client_returns_422_json(
        self, client, staff_user, second_facility, doc_type_contact
    ):
        """N11 (#1423): Client-UUID einer fremden Facility -> 422 mit
        Feldfehler unter ``client``, kein Event angelegt (statt stillem
        Anonym-Fallback)."""
        from core.models import Client as ClientModel

        foreign_client = ClientModel.objects.create(
            facility=second_facility,
            pseudonym="Fremd-01",
            contact_stage=ClientModel.ContactStage.IDENTIFIED,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(foreign_client.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "cross-tenant",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert "client" in payload["errors"]
        assert not Event.objects.filter(created_by=staff_user).exists()

    def test_event_create_unknown_client_returns_422_json(self, client, staff_user, doc_type_contact):
        """N11 (#1423): nicht existierende/geloeschte Client-UUID -> 422
        statt stillem Anonym-Fallback."""
        import uuid

        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(uuid.uuid4()),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "geisterklient",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert "client" in payload["errors"]
        assert not Event.objects.filter(created_by=staff_user).exists()

    def test_event_create_without_client_still_creates_anonymous_event(self, client, staff_user, doc_type_contact):
        """Regression N11: kein ``client``-Wert -> Event bleibt weiterhin
        anonym (die neue ``clean_client``-Pruefung darf leere Werte nicht
        als Fehler werten)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "anonym",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302
        event = Event.objects.filter(created_by=staff_user).first()
        assert event is not None
        assert event.is_anonymous is True
        assert event.client is None

    def test_event_create_with_own_client_associates_event(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Regression N11: gueltige eigene Client-UUID -> Event wird mit dem
        Client verknuepft (``cleaned_data["client"]`` ist jetzt eine
        ``Client``-Instanz statt einer rohen UUID)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "verknuepft",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302
        event = Event.objects.filter(created_by=staff_user).first()
        assert event is not None
        assert event.client_id == client_identified.pk

    def test_event_create_valid_accept_json_still_redirects(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Wächter (c): ein valider POST mit ``Accept: application/json``
        bleibt beim normalen 302-Redirect — der 422-Zweig gilt nur für
        ungültige Formulare. Bereits heute grün, darf durch Task 8 nicht
        brechen (Regressionsschutz #1387)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "Testnotiz",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302
        assert Event.objects.filter(created_by=staff_user).exists()

    def test_event_create_html_invalid_still_renders_200(self, client, staff_user, doc_type_contact, client_identified):
        """Wächter (d)/Regressionsschutz #1387: ohne ``Accept:
        application/json`` bleibt das bestehende 200-Re-Render mit
        inline-Fehlern unverändert."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                # occurred_at fehlt -> meta_form ungueltig
                "dauer": "15",
                "notiz": "offline-create",
            },
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")

    def test_event_create_hx_request_without_accept_returns_200(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Wächter (e)/Regressionsschutz #1387: ``HX-Request: true`` OHNE
        ``Accept: application/json`` löst NICHT den 422-Zweig aus — ein
        normaler HTMX-Submit behält sein 200-HTML-Re-Render (Muster-
        Konsistenz mit dem Update-Pfad, der ebenfalls NUR auf den rohen
        Accept-Header prüft, nicht auf HX-Request)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                # occurred_at fehlt -> meta_form ungueltig
                "dauer": "15",
                "notiz": "offline-create",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")


@pytest.mark.django_db
class TestWorkItemCreateInvalid422:
    """Create-422-Contract für :class:`WorkItemCreateView.post` (Refs #1351 Task 8, #1387).

    Analog zu :class:`TestEventCreateInvalid422`, aber nur EIN Invalid-Pfad —
    ``WorkItemCreateView.post`` prüft (anders als Events) kein zweites
    Formular und fängt auch keine ``ValidationError`` aus dem Service ab.

    Dieser Test ist gegen den heutigen Code ROT: der einzige Invalid-Pfad
    liefert aktuell 200-HTML statt 422-JSON. Refs #1351, #1387.
    """

    def test_workitem_create_invalid_returns_422_json(self, client, staff_user):
        """``title`` fehlt -> Form ungültig -> 422 statt 200."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {"item_type": "task", "priority": "normal"},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert "title" in payload["errors"]
        assert not WorkItem.objects.filter(created_by=staff_user).exists()

    def test_workitem_create_valid_accept_json_still_redirects(self, client, staff_user):
        """Wächter (c): valider POST + ``Accept: application/json`` bleibt
        beim 302-Redirect. Bereits heute grün, darf durch Task 8 nicht
        brechen (Regressionsschutz #1387)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {"item_type": "task", "title": "Aufgabe-422-Guard", "priority": "normal"},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302
        assert WorkItem.objects.filter(title="Aufgabe-422-Guard").exists()

    def test_workitem_create_html_invalid_still_renders_200(self, client, staff_user):
        """Wächter (d)/Regressionsschutz #1387: ohne ``Accept:
        application/json`` bleibt das bestehende 200-Re-Render mit
        inline-Fehlern unverändert."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {"item_type": "task", "priority": "normal"},
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")

    def test_workitem_create_hx_request_without_accept_returns_200(self, client, staff_user):
        """Wächter (e)/Regressionsschutz #1387: ``HX-Request: true`` OHNE
        ``Accept: application/json`` löst NICHT den 422-Zweig aus."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {"item_type": "task", "priority": "normal"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")


@pytest.mark.django_db
class TestOfflineConflictShellViews:
    """Pure HTML scaffolds for ``/offline/conflicts/`` and
    ``/offline/conflicts/<uuid>/`` — they never render PII because the
    conflict data lives in client-side IndexedDB.
    """

    def test_conflict_list_is_public(self, client):
        """Vertragsaenderung #1396 (Option 1): die Liste ist pk-los + datenlos
        und muss via SW ``cache.addAll`` precachebar sein — ein Auth-Gate
        wuerde den Install-Fetch auf ``/login/`` redirecten. Siehe
        ``test_offline_apis.py::test_offline_conflict_list_is_public`` (E2E)
        und ``_authz_expectations.py`` (Matrix-Eintrag ``public``)."""
        response = client.get(reverse("core:offline_conflict_list"))
        assert response.status_code == 200
        assert b"conflict-list-view" in response.content

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


@pytest.mark.django_db
class TestWorkItemStatusUpdateConflict:
    """Optimistic-concurrency contract of :class:`WorkItemStatusUpdateView.post` (Refs #1419).

    Der Status-Pfad tritt dem HTTP-Replay-Contract (ADR-030) bei, damit
    Status-Übergänge offline queue- und nachspielbar werden. Anders als beim
    Edit-Pfad (:class:`TestWorkItemUpdateConflict`, Token-Pflicht via
    ``_wants_json_response`` inkl. HTMX) gilt die Token-Pflicht hier NUR für
    Raw-JSON-Clients (``_wants_raw_json_response``): HTMX ist beim
    Status-Toggle der normale ONLINE-Pfad (Inbox-Buttons), dessen bewusst
    akzeptiertes Status-gegen-Status-LWW unverändert bleibt (Issue #1419:
    „Online beschränkt sich das LWW auf Status-gegen-Status"). Der Replay der
    generischen Offline-Queue setzt immer ``Accept: application/json``
    (offline-queue.js ``_send``) und fällt damit unter die Pflicht.
    """

    def _url(self, workitem):
        return reverse("core:workitem_status_update", kwargs={"pk": workitem.pk})

    def test_status_replay_returns_409_json_on_stale_token(self, client, staff_user, sample_workitem):
        """Ein Replay (Accept: application/json) mit veraltetem
        ``expected_updated_at`` bekommt 409 mit Server-Stand — und der
        Status-Übergang wird NICHT angewendet (ROT vor #1419: heute LWW)."""
        client.force_login(staff_user)
        response = client.post(
            self._url(sample_workitem),
            {"status": "done", "expected_updated_at": _stale_timestamp()},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "conflict"
        assert payload["client_expected"] == _stale_timestamp()
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "open", "Status darf bei veraltetem Token nicht wechseln"

    def test_status_replay_conflict_body_includes_server_state(self, client, staff_user, sample_workitem):
        """Der 409-Body trägt die Felder, die die M8-Konflikt-UI zum Rendern
        des Status-Konflikts braucht (title/description/status/updated_at) —
        dieselbe Form wie beim Edit-Pfad (``_workitem_conflict_response``)."""
        client.force_login(staff_user)
        response = client.post(
            self._url(sample_workitem),
            {"status": "done", "expected_updated_at": _stale_timestamp()},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        server_state = response.json()["server_state"]
        assert server_state["title"] == "Test-Aufgabe"
        assert server_state["status"] == "open"
        assert server_state["updated_at"] is not None

    def test_status_replay_without_token_returns_409_missing_token(self, client, staff_user, sample_workitem):
        """Replay-Clients MÜSSEN das Token mitschicken — fehlt es, ist das
        kein stilles LWW, sondern ein expliziter 409 ``missing-token`` und
        das WorkItem bleibt unverändert (ROT vor #1419)."""
        client.force_login(staff_user)
        response = client.post(
            self._url(sample_workitem),
            {"status": "done"},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["error"] == "missing-token"
        assert payload["client_expected"] is None
        assert payload["server_state"]["updated_at"] is not None
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "open"

    def test_status_replay_with_fresh_token_succeeds_via_redirect(self, client, staff_user, sample_workitem):
        """Erfolgsfall des Replays eines klassischen Formular-POSTs (kein
        HX-Request-Header am Queue-Record): frisches Token → Redirect wie
        beim Original — genau die Erfolgsform, der die generische Queue folgt
        (``response.redirected``)."""
        client.force_login(staff_user)
        sample_workitem.refresh_from_db()
        response = client.post(
            self._url(sample_workitem),
            {"status": "done", "expected_updated_at": sample_workitem.updated_at.isoformat()},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "done"

    def test_status_replay_with_fresh_token_htmx_returns_partial(self, client, staff_user, sample_workitem):
        """Erfolgsfall des Replays eines HTMX-Button-Klicks: der Queue-Record
        trägt ``HX-Request`` UND der Replay ``Accept: application/json`` →
        200-Partial (Queue-Erfolgskontrakt für HX-Records)."""
        client.force_login(staff_user)
        sample_workitem.refresh_from_db()
        response = client.post(
            self._url(sample_workitem),
            {"status": "in_progress", "expected_updated_at": sample_workitem.updated_at.isoformat()},
            HTTP_ACCEPT="application/json",
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "in_progress"

    def test_status_replay_corrupt_token_returns_409_not_500(self, client, staff_user, sample_workitem):
        """Ein nicht-ISO-parsebarer Token im Replay-Pfad wird defensiv als
        Konflikt behandelt (Server-Stand zur Review) statt als 500."""
        client.force_login(staff_user)
        response = client.post(
            self._url(sample_workitem),
            {"status": "done", "expected_updated_at": "nicht-iso"},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        assert response.json()["error"] == "conflict"

    def test_status_htmx_online_path_keeps_lww_with_stale_token(self, client, staff_user, sample_workitem):
        """Wächter der Scope-Grenze: der normale ONLINE-HTMX-Klick (kein
        Accept: application/json) bleibt beim bisherigen
        Status-gegen-Status-LWW — auch wenn die Buttons künftig ein (ggf.
        veraltetes) Token mitsenden. Die Templates senden das Token nur für
        den Offline-Payload mit; ausgewertet wird es erst im Replay-Pfad."""
        client.force_login(staff_user)
        response = client.post(
            self._url(sample_workitem),
            {"status": "done", "expected_updated_at": _stale_timestamp()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "done"

    def test_status_html_form_path_unchanged_with_stale_token(self, client, staff_user, sample_workitem):
        """Wächter: auch der klassische Formular-POST (Detail-Seite) bleibt
        beim LWW-Redirect — kein 409 im HTML-Pfad."""
        client.force_login(staff_user)
        response = client.post(
            self._url(sample_workitem),
            {"status": "done", "expected_updated_at": _stale_timestamp()},
        )
        assert response.status_code == 302
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "done"

    def test_status_replay_invalid_status_stays_400(self, client, staff_user, sample_workitem):
        """Ein ungültiger Statuswert bleibt 400 — die Replay-Klassifikation
        (offline-queue.js) behandelt 400 wie 422 als dauerhaften
        Dead-Letter, korrekt für einen nie gültig werdenden Wert."""
        client.force_login(staff_user)
        response = client.post(
            self._url(sample_workitem),
            {"status": "quatsch", "expected_updated_at": _stale_timestamp()},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 400
