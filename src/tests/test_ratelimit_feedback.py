"""Rueckmeldung bei einem Rate-Limit-Treffer (Refs #1873).

Die 429-Antwort des ``handler403`` war bis hierher **immer** leer (Refs #1354,
KISS: „die JS-Clients werten nur den Statuscode aus"). Fuer die HTMX-Flaechen
stimmte das nicht: ``htmx-errors.js`` zeigt zwar seit dem globalen Fehler-Toast
etwas an, liest aber ``xhr.status`` nirgends aus — ein Rate-Limit sah damit
exakt aus wie ein Serverfehler („Die Aktion konnte nicht ausgefuehrt werden").

Zwei Aussagen haelt dieses Modul fest:

* **Mit ``HX-Request``** traegt die 429-Antwort einen kurzen, uebersetzten
  Klartext-Hinweis — kurz genug und ohne ``<``, damit ``htmx-errors.js`` ihn
  woertlich in den Toast uebernimmt statt auf die generische Meldung zu
  fallen.
* **Ohne ``HX-Request``** bleibt die Antwort nackt. Das bewacht die
  #1354-Entscheidung fuer die Offline-JS-Clients, die den Body nie lesen
  (``test_offline_bundle_api.py``).

Der Hinweis nennt bewusst weder Treffer noch deren Abwesenheit: genau die
Verwechslung mit „keine Treffer" war der gemeldete Fehler.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.utils.translation import gettext, override
from django_ratelimit.exceptions import Ratelimited

from core.views.errors import RATELIMIT_HINWEIS, permission_denied


class TestRatelimitHinweisFuerHtmx:
    def test_mit_hx_request_traegt_die_429_einen_klartext_hinweis(self):
        request = RequestFactory().get("/search/", HTTP_HX_REQUEST="true")

        response = permission_denied(request, Ratelimited())

        assert response.status_code == 429
        assert response.content.decode() == (
            "Zu viele Anfragen in kurzer Zeit. Bitte kurz warten und es erneut versuchen."
        )
        assert response["Content-Type"] == "text/plain; charset=utf-8"

    def test_der_hinweis_passt_in_den_toast_und_nennt_keine_treffer(self):
        """``htmx-errors.js`` uebernimmt einen Body nur bis 200 Zeichen und ohne
        ``<`` woertlich; laenger oder HTML-haltig faellt er auf die generische
        Serverfehler-Meldung zurueck."""
        request = RequestFactory().get("/search/", HTTP_HX_REQUEST="true")

        text = permission_denied(request, Ratelimited()).content.decode()

        assert len(text) <= 200
        assert "<" not in text
        assert "Treffer" not in text
        assert "gefunden" not in text

    def test_ohne_hx_request_bleibt_die_antwort_nackt(self):
        """Refs #1354: die Offline-Clients werten nur den Statuscode aus."""
        request = RequestFactory().get("/offline/bundle/1/")

        response = permission_denied(request, Ratelimited())

        assert response.status_code == 429
        assert response.content == b""

    def test_ein_echter_rechteentzug_bleibt_403_ohne_hinweis(self):
        """Nur ``Ratelimited`` bekommt den Hinweis — ein echter 403 geht
        unveraendert an Djangos Standard-Handler."""
        request = RequestFactory().get("/clients/", HTTP_HX_REQUEST="true")

        response = permission_denied(request, PermissionDenied())

        assert response.status_code == 403
        assert RATELIMIT_HINWEIS not in response.content.decode()


class TestRatelimitHinweisUebersetzung:
    """ADR-027: DE ist Quellsprache, EN die einzige Uebersetzung. Der Lock
    friert das konkrete Rendering ein, damit ein spaeterer
    ``makemessages``/``msgmerge``-Lauf es nicht auf einen Fuzzy-Fallback
    zurueckdreht."""

    def test_englische_fassung(self):
        with override("en"):
            assert gettext(RATELIMIT_HINWEIS) == (
                "Too many requests in a short time. Please wait a moment and try again."
            )

    def test_deutsche_fassung(self):
        with override("de"):
            assert gettext(RATELIMIT_HINWEIS) == RATELIMIT_HINWEIS
