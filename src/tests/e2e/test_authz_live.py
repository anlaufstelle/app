"""Live-Only-AuthZ-Checks am laufenden E2E-Server (Refs #1055).

Dauerhafte, kleine Ergänzung zur Test-Client-Matrix: prüft ausschließlich
Eigenschaften, die prinzipiell nur über echtes HTTP sichtbar sind —
Session-Cookie-Flags, Security-Header und je eine vertikale Stichprobe.
Die flächige Matrix läuft als Test-Client-Tests (test_authz_matrix.py),
der volle Live-Sweep als manueller Audit (test_authz_audit.py).
"""

import uuid

import pytest
import requests

pytestmark = pytest.mark.e2e

PASSWORD = "anlaufstelle2026"


def _login(base_url, username):
    """Per echtem HTTP einloggen; gibt (Session, Login-Response) zurück.

    Aufrufer müssen die Session schließen (``with session:`` bzw.
    ``session.close()``). Zusätzlich ``Connection: close``: Der
    gunicorn-gthread-Worker blockiert nach einem POST über eine
    Keep-Alive-Verbindung den graceful Shutdown um volle 30s
    (graceful_timeout) — auch wenn der Client die Verbindung längst
    geschlossen hat. Das ließ den Server-Teardown der base_url-Fixture
    (proc.wait(timeout=10)) reproduzierbar in TimeoutExpired laufen.
    """
    session = requests.Session()
    session.headers["Connection"] = "close"
    login_page = session.get(f"{base_url}/login/", timeout=10)
    assert login_page.status_code == 200
    token = session.cookies.get("csrftoken")
    response = session.post(
        f"{base_url}/login/",
        data={"username": username, "password": PASSWORD, "csrfmiddlewaretoken": token},
        headers={"Referer": f"{base_url}/login/"},
        timeout=10,
        allow_redirects=False,
    )
    assert response.status_code == 302, f"Login {username} fehlgeschlagen: {response.status_code}"
    return session, response


class TestSessionCookieFlags:
    """Cookie-Attribute, die nur im echten Set-Cookie-Header sichtbar sind."""

    def test_session_cookie_is_httponly_and_samesite(self, base_url):
        """Session-Cookie trägt HttpOnly und SameSite=Lax (curl-verifiziert)."""
        session, login_response = _login(base_url, "miriam")
        session.close()
        # requests fasst mehrere Set-Cookie-Header (csrftoken + sessionid)
        # kommasepariert zusammen — daher gezielt den sessionid-Teil prüfen,
        # damit Attribute des csrftoken-Cookies kein False-Positive liefern.
        set_cookie = login_response.headers.get("Set-Cookie", "")
        assert "sessionid=" in set_cookie
        session_part = set_cookie.split("sessionid=", 1)[1]
        assert "HttpOnly" in session_part
        assert "SameSite=Lax" in session_part
        # Secure-Flag ist prod-only (settings/prod.py SESSION_COOKIE_SECURE=True,
        # TLS terminiert Caddy) — am HTTP-E2E-Server nicht beobachtbar.


class TestSecurityHeaders:
    """Security-Header der echten HTTP-Responses (Middleware-Kette)."""

    def test_headers_on_login_page(self, base_url):
        """Login-Seite liefert nosniff, Referrer-Policy und enforced CSP."""
        response = requests.get(f"{base_url}/login/", timeout=10)
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("Referrer-Policy") == "same-origin"
        # Beobachtet: enforced CSP (kein Report-Only) — bewusst strikt,
        # damit ein Downgrade auf Report-Only als Regression auffällt.
        assert "Content-Security-Policy" in response.headers

    def test_headers_when_authenticated(self, base_url):
        """Auch eingeloggte Seiten (/start/) tragen die Security-Header."""
        session, _ = _login(base_url, "miriam")
        with session:
            response = session.get(f"{base_url}/start/", timeout=10)
        assert response.status_code == 200
        assert response.headers.get("X-Content-Type-Options") == "nosniff"


class TestVerticalEscalationSample:
    """Vertikale Eskalations-Stichprobe über echtes HTTP (Rolle assistant)."""

    def test_assistant_cannot_reach_system_area(self, base_url):
        """Assistant erhält auf /system/ kein 200 (beobachtet: 403)."""
        session, _ = _login(base_url, "lena")
        with session:
            response = session.get(f"{base_url}/system/", timeout=10, allow_redirects=False)
        assert response.status_code in (403, 404), response.status_code

    def test_assistant_cannot_reach_django_admin(self, base_url):
        """Assistant wird vom Django-Admin abgewiesen.

        Beobachtet: 302 → /admin-mgmt/login/?next=/admin-mgmt/.
        """
        session, _ = _login(base_url, "lena")
        with session:
            response = session.get(f"{base_url}/admin-mgmt/", timeout=10, allow_redirects=False)
        assert response.status_code in (302, 403, 404)
        if response.status_code == 302:
            assert "/login" in response.headers.get("Location", "")

    def test_anonymous_is_redirected_to_login(self, base_url):
        """Anonym landet auf /login/ (beobachtet: /login/?next=/clients/)."""
        response = requests.get(f"{base_url}/clients/", timeout=10, allow_redirects=False)
        assert response.status_code == 302
        assert response.headers.get("Location", "").startswith("/login/")


class TestObjectScopeSample:
    """Objekt-Scope-Stichprobe über echtes HTTP."""

    def test_unknown_client_uuid_is_404_not_500(self, base_url):
        """Stichprobe Objekt-Scope über echtes HTTP. Die echte
        Cross-Facility-Probe läuft im Audit (medium-Seed, 2 Facilities)."""
        session, _ = _login(base_url, "miriam")
        with session:
            response = session.get(f"{base_url}/clients/{uuid.uuid4()}/", timeout=10)
        assert response.status_code == 404
