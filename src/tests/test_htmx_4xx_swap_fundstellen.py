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
"""

import pytest
from django.urls import reverse

from core.models import RetentionProposal

HTMX = {"HTTP_HX_REQUEST": "true"}
_MAX_PLAIN_TEXT_LENGTH = 400  # Refs #2358, static/js/htmx-errors.js


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
