"""4xx-Klartextantworten auf Swap-Verhalten geprüft (Refs #2385, Nachtrag zu #2358).

Beim Fix der HTMX-Absage für Wirkungsziele/Meilensteine (409 + ``HX-Reswap:
none``, ``htmx_absage`` in ``core/views/mixins.py``) fielen weitere
Klartext-Antworten mit 4xx-Status auf, deren Swap-Verhalten seit der globalen
4xx-Swap-Regel (Refs #2232, ``responseHandling`` in ``base.html``) nicht
geprüft war. Recherche (Bericht ``u-bericht.md``, Abschnitt 2385) ergab: von
allen Fundstellen ersetzt der eingewechselte Klartext tatsächlich einen
Arbeitsbereich bei

* ``StatisticsView.get`` — Zeitraum-Pills/Report-Karten wechseln in
  ``#statistics-content`` (das ganze Dashboard);
* ``RetentionHoldView.post`` — die Formularvalidierung (fehlende Begründung,
  ungültiges Datum) wechselt in ``#proposal-<pk>`` (die ganze Vorschlagskarte,
  ``hx-swap="outerHTML"``), noch bevor überhaupt ein Hold existiert;
* ``_BulkActionMixin.post`` — die Massenaktions-Buttons haben kein
  ``hx-target``, der Klartext wechselt also in den Button selbst (kein
  ``hx-swap`` gesetzt ⇒ ``innerHTML`` des auslösenden Elements) und bleibt dort
  über den nächsten, unabhängigen Selektionszustand hinaus stehen.

Alle drei sind jetzt auf ``htmx_absage(..., status=400)`` umgestellt: 400 +
``HX-Reswap: none`` + Klartext-Toast statt Swap.

Die 409-Antworten der „Vorschlagskarte" (``RetentionApproveView`` bei aktivem
Hold, ``RetentionHoldView`` beim Doppel-Klick-Hold) bleiben ABSICHTLICH
unverändert — dort ist das Einwechseln laut Kommentar zu #2321 gewollt (die
veraltete Karte wird durch die aktuelle Meldung ersetzt). Die beiden
Gegenproben unten sichern das gegen eine versehentliche Umstellung ab.

``TestWorkItemStatusUpdateServiceRejection`` (Refs #2385): ``WorkItemStatusUpdateView.post``
swallowed every ``ValidationError`` raised by ``update_workitem_status`` —
not just a real optimistic-locking conflict — into the same 409 JSON body
(``_workitem_conflict_response``). For the inbox row button (HTMX,
``hx-swap="outerHTML"``) this JSON text got swapped into the row itself,
because an HTMX request satisfies ``_wants_json_response`` too. The view only
honours the version token for raw-JSON clients (``wants_raw_json``, true only
for ``Accept: application/json``) — the HTMX row button does send
``expected_updated_at`` (``_workitem_row.html``, ``hx-vals``, Refs #1419), but
the view discards it on that path, so an optimistic-lock conflict can never
be raised there: every ``ValidationError`` reaching that branch (Art. 18
processing restriction, a deleted WorkItem, a changed client assignment,
...) is a business rejection. The view now branches on
``_wants_raw_json_response`` first (unchanged 409 JSON contract for raw-JSON/
offline-replay clients, including ``missing-token``), then on ``HX-Request``
(``htmx_absage`` — plain text, ``HX-Reswap: none``, row stays, toast shows the
message), then falls back to the pre-existing HTML flash+redirect.
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import Client, RetentionProposal, WorkItem
from core.services.case.workitems import WORKITEM_DELETED_MESSAGE
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE, restrict_processing

HTMX = {"HTTP_HX_REQUEST": "true"}
_MAX_PLAIN_TEXT_LENGTH = 400  # Refs #2358, static/js/htmx-errors.js


@pytest.fixture
def workitem_open(facility, client_identified, staff_user):
    # Copied from test_workitems_inbox_and_status.py: pytest does not
    # discover file-specific fixtures across test modules (Refs #1130).
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        title="Offene Aufgabe",
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.NORMAL,
    )


def _assert_absage_ohne_swap(response, status):
    assert response.status_code == status, response.status_code
    assert response.headers.get("HX-Reswap") == "none", dict(response.headers)
    text = response.content.decode()
    assert text.strip()
    assert len(text) <= _MAX_PLAIN_TEXT_LENGTH
    assert "<" not in text, "Absage muss kurzer Klartext sein (htmx-Fehler-Toast)"


def _assert_ohne_htmx_unveraendert(response, status, textausschnitt):
    """Nicht-HTMX-Anfrage: Status und Klartext wie vor der Umstellung.

    ``htmx_absage`` setzt ``HX-Reswap: none`` unbedingt — auch ohne
    ``HX-Request``-Header. Ein Nicht-HTMX-Client wertet den Header nie aus
    (htmx-eigene Response-Verarbeitung), er ist also harmlos; der Vertrag
    dieser Gegenprobe ist Status + Klartext, nicht die Abwesenheit des
    Headers.
    """
    assert response.status_code == status, response.status_code
    assert textausschnitt in response.content.decode()


@pytest.mark.django_db
class TestStatisticsViewUngueltigerZeitraum:
    """``StatisticsView.get`` Z. 319 — Zeitraum-Pills wechseln in #statistics-content."""

    _UNGUELTIG = {"zeitraum": "frei", "date_from": "2026-09-20", "date_to": "2026-09-01"}

    def test_htmx_absage_ohne_swap(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), self._UNGUELTIG, **HTMX)
        _assert_absage_ohne_swap(response, 400)

    def test_ohne_htmx_unveraendert(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), self._UNGUELTIG)
        _assert_ohne_htmx_unveraendert(response, 400, "darf nicht nach")


@pytest.mark.django_db
class TestRetentionHoldViewLeereBegruendung:
    """``RetentionHoldView.post`` Z. 88 — wechselt in #proposal-<pk> (outerHTML)."""

    def test_htmx_absage_ohne_swap(self, client, lead_user, retention_proposal):
        client.force_login(lead_user)
        response = client.post(
            reverse("core:retention_hold", kwargs={"pk": retention_proposal.pk}),
            {"reason": "   "},  # Browser laesst ``required`` mit reinem Leerraum passieren
            **HTMX,
        )
        _assert_absage_ohne_swap(response, 400)
        retention_proposal.refresh_from_db()
        assert retention_proposal.status == RetentionProposal.Status.PENDING

    def test_ohne_htmx_unveraendert(self, client, lead_user, retention_proposal):
        client.force_login(lead_user)
        response = client.post(
            reverse("core:retention_hold", kwargs={"pk": retention_proposal.pk}),
            {"reason": ""},
        )
        _assert_ohne_htmx_unveraendert(response, 400, "Begründung ist erforderlich")


@pytest.mark.django_db
class TestRetentionHoldViewUngueltigesDatum:
    """``RetentionHoldView.post`` Z. 96 — derselbe Ziel-/Swap-Mechanismus wie oben."""

    def test_htmx_absage_ohne_swap(self, client, lead_user, retention_proposal):
        client.force_login(lead_user)
        response = client.post(
            reverse("core:retention_hold", kwargs={"pk": retention_proposal.pk}),
            {"reason": "Grund", "expires_at": "nicht-valide"},
            **HTMX,
        )
        _assert_absage_ohne_swap(response, 400)


@pytest.mark.django_db
class TestBulkActionMixinKeineAuswahl:
    """``_BulkActionMixin.post`` Z. 191 — kein ``hx-target``, Klartext wechselt in den Button."""

    def test_htmx_absage_ohne_swap(self, client, lead_user):
        client.force_login(lead_user)
        response = client.post(reverse("core:retention_bulk_approve"), {}, **HTMX)
        _assert_absage_ohne_swap(response, 400)

    def test_ohne_htmx_unveraendert(self, client, lead_user):
        client.force_login(lead_user)
        response = client.post(reverse("core:retention_bulk_approve"), {})
        _assert_ohne_htmx_unveraendert(response, 400, "Keine Vorschläge ausgewählt")


@pytest.mark.django_db
class TestBulkActionMixinUngueltigeAuswahl:
    """``_BulkActionMixin.post`` Z. 195 — Auswahl inzwischen anderweitig entschieden."""

    def test_htmx_absage_ohne_swap(self, client, lead_user, retention_proposal):
        RetentionProposal.objects.filter(pk=retention_proposal.pk).update(status=RetentionProposal.Status.APPROVED)
        client.force_login(lead_user)
        response = client.post(
            reverse("core:retention_bulk_approve"),
            {"proposal_ids": [str(retention_proposal.pk)]},
            **HTMX,
        )
        _assert_absage_ohne_swap(response, 400)


@pytest.mark.django_db
class TestWorkItemStatusUpdateInvalidStatus:
    """``WorkItemStatusUpdateView.post`` — invalid status swaps into
    ``#workitem-<pk>`` (``_workitem_row.html``, ``hx-swap="outerHTML"``)."""

    def test_htmx_rejection_without_swap(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "invalid"},
            **HTMX,
        )
        _assert_absage_ohne_swap(response, 400)
        workitem_open.refresh_from_db()
        assert workitem_open.status == "open"

    def test_without_htmx_unchanged(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "invalid"},
        )
        _assert_ohne_htmx_unveraendert(response, 400, "Ungültiger Status")


def _restrict_person(client_identified, staff_user, workitem_open):
    """Art. 18 state: processing restricted on the task's person."""
    restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
    return PROCESSING_RESTRICTED_MESSAGE


def _soft_delete_workitem(client_identified, staff_user, workitem_open):
    """Deleted-task state, same direct-field pattern as
    ``test_workitem_state_contract_guard.py`` (no dedicated public
    soft-delete endpoint exists for a single WorkItem)."""
    WorkItem.objects.filter(pk=workitem_open.pk).update(is_deleted=True, deleted_at=timezone.now())
    return WORKITEM_DELETED_MESSAGE


@pytest.mark.django_db
class TestWorkItemStatusUpdateServiceRejection:
    """``WorkItemStatusUpdateView.post`` — a service-level ``ValidationError``
    (Art. 18 processing restriction, deleted WorkItem, changed client
    assignment, ...) swaps into ``#workitem-<pk>`` the same way the invalid-
    status case above does (Refs #2385).

    Two of the service's rejection states are not reachable through this
    view's public surface, so they are left out here rather than mocked:
    the foreign-facility ``ValidationError`` (``workitems.py:71``, via
    ``_assert_actor_in_facility``) can never fire because the view's own
    ``get_scoped_object`` call already 404s on a facility mismatch before
    the service runs (``scoping.py``); the changed-client-assignment
    ``ValidationError`` (``workitems.py:957-958``) only fires when the
    row's client changes between the service's unlocked pre-read and its
    locked re-read inside the same transaction — a genuine concurrent-write
    race that a single synchronous test client cannot produce without a
    second real connection.
    """

    @pytest.mark.parametrize(
        "arrange",
        [_restrict_person, _soft_delete_workitem],
        ids=["processing_restricted", "workitem_deleted"],
    )
    def test_htmx_rejection_without_swap(self, client, staff_user, workitem_open, client_identified, arrange):
        expected_message = arrange(client_identified, staff_user, workitem_open)
        client.force_login(staff_user)

        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "in_progress"},
            **HTMX,
        )

        _assert_absage_ohne_swap(response, 409)
        assert "application/json" not in response.headers.get("Content-Type", "")
        assert str(expected_message) in response.content.decode()
        workitem_open.refresh_from_db()
        assert workitem_open.status == "open"

    def test_raw_json_version_conflict_unchanged(self, client, staff_user, workitem_open):
        client.force_login(staff_user)

        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "in_progress", "expected_updated_at": "2000-01-01T00:00:00+00:00"},
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 409, response.status_code
        body = response.json()
        assert body["error"] == "conflict"
        assert "server_state" in body

    def test_raw_json_missing_token_unchanged(self, client, staff_user, workitem_open):
        client.force_login(staff_user)

        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "in_progress"},
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 409, response.status_code
        body = response.json()
        assert body["error"] == "missing-token"

    def test_form_post_without_htmx_keeps_flash_redirect(self, client, staff_user, workitem_open, client_identified):
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        client.force_login(staff_user)

        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "in_progress"},
        )

        assert response.status_code == 302, response.status_code
        assert response.url == reverse("core:workitem_inbox")
        flash_messages = [str(msg) for msg in get_messages(response.wsgi_request)]
        assert flash_messages == [str(PROCESSING_RESTRICTED_MESSAGE)]
        workitem_open.refresh_from_db()
        assert workitem_open.status == "open"


@pytest.mark.django_db
class TestGegenprobeVorschlagskarteBleibtEingewechselt:
    """409 der Vorschlagskarte (Refs #2321) bleiben UNVERÄNDERT — kein ``HX-Reswap``."""

    def test_retention_hold_doppelklick_409_ohne_reswap_none(self, client, lead_user, retention_proposal):
        client.force_login(lead_user)
        url = reverse("core:retention_hold", kwargs={"pk": retention_proposal.pk})
        erster = client.post(url, {"reason": "Erster Hold"}, **HTMX)
        assert erster.status_code == 200, erster.status_code

        zweiter = client.post(url, {"reason": "Doppel-Klick"}, **HTMX)
        assert zweiter.status_code == 409, zweiter.status_code
        assert "HX-Reswap" not in zweiter.headers, dict(zweiter.headers)
        assert "aktiver Hold" in zweiter.content.decode()

    def test_retention_approve_unter_hold_409_ohne_reswap_none(self, client, lead_user, retention_proposal):
        client.force_login(lead_user)
        hold_response = client.post(
            reverse("core:retention_hold", kwargs={"pk": retention_proposal.pk}),
            {"reason": "Gerichtsverfahren"},
            **HTMX,
        )
        assert hold_response.status_code == 200, hold_response.status_code

        response = client.post(
            reverse("core:retention_approve", kwargs={"pk": retention_proposal.pk}),
            **HTMX,
        )
        assert response.status_code == 409, response.status_code
        assert "HX-Reswap" not in response.headers, dict(response.headers)
