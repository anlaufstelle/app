"""Tests für den dedizierten CSRF-Token-Endpoint (Refs #1408).

Der Offline-/Replay-Refresh-Pfad holt den frischen CSRF-Token seit #1408 aus
diesem günstigen JSON-Endpoint statt per Regex aus gescraptem /login/-HTML
(fehleranfällige Bugquelle in #1330/#1332). Der Endpoint ist bewusst public
(wie ``offline_client_shell``): er trägt keinerlei PII, erhält exakt die
heutige Refresh-Semantik (bisherige Quelle /login/ ist ebenfalls public und
liefert immer einen frischen Token) und darf nie zwischengespeichert werden.
"""

import pytest
from django.conf import settings
from django.middleware.csrf import _unmask_cipher_token
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
class TestOfflineCsrfTokenView:
    """HTTP-Verhalten von GET /api/v1/offline/csrf/."""

    def _cookie_name(self):
        return settings.CSRF_COOKIE_NAME

    def test_get_returns_csrftoken_json(self, client):
        response = client.get(reverse("core:offline_csrf"))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        body = response.json()
        assert set(body.keys()) == {"csrftoken"}
        assert isinstance(body["csrftoken"], str)
        assert body["csrftoken"]

    def test_response_is_not_stored(self, client):
        """Der Token darf nie aus einem HTTP-Cache kommen — sonst liefe ein
        Refresh nach Session-Wechsel gegen einen veralteten Token (403-Kaskade,
        genau das Problem, das der Endpoint beheben soll)."""
        response = client.get(reverse("core:offline_csrf"))
        assert "no-store" in response.headers.get("Cache-Control", "")

    def test_sets_csrf_cookie(self, client):
        """``@ensure_csrf_cookie``: der Endpoint setzt das CSRF-Cookie, damit
        Cookie und ausgelieferter Token zueinander passen."""
        response = client.get(reverse("core:offline_csrf"))
        assert self._cookie_name() in response.cookies

    def test_json_token_is_the_live_session_token(self, client):
        """Das ausgelieferte Token ist kein Zufallswert, sondern der maskierte
        CSRF-Token DIESER Session — es unmaskt exakt auf das gesetzte
        Cookie-Secret. Nur so akzeptiert der Server den Replay-POST, der das
        Token via X-CSRFToken zurückschickt."""
        response = client.get(reverse("core:offline_csrf"))
        masked = response.json()["csrftoken"]
        cookie_secret = response.cookies[self._cookie_name()].value
        assert _unmask_cipher_token(masked) == cookie_secret

    def test_independent_sessions_get_different_tokens(self):
        """Rotation: zwei unabhängige Sessions bekommen unterschiedliche
        CSRF-Secrets — ein Refresh nach Session-Wechsel liefert also ein
        anderes Token, wodurch ein 403 NACH frischem Token sauber als echter
        Rechteentzug ("revoked") klassifizierbar bleibt."""
        name = self._cookie_name()
        s1 = Client().get(reverse("core:offline_csrf")).cookies[name].value
        s2 = Client().get(reverse("core:offline_csrf")).cookies[name].value
        assert s1 != s2

    def test_post_not_allowed(self, client):
        response = client.post(reverse("core:offline_csrf"))
        assert response.status_code == 405

    def test_public_anonymous_access(self, client):
        """Public wie offline_client_shell — kein Auth-Gate (PII-frei)."""
        response = client.get(reverse("core:offline_csrf"))
        assert response.status_code == 200
