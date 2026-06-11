"""Helfer für den Live-AuthZ-Audit (Refs #1055): Logins, ID-Harvesting, Report."""

import requests

PASSWORD = "anlaufstelle2026"


def login(base_url, username):
    """Echter Form-Login; Rückgabe ``(session, login_response)``.

    Aufrufer müssen die Session schließen (``with session:`` bzw.
    ``session.close()``); schlägt der Login fehl, wird sie HIER geschlossen
    (kein Session-Leak bei Assertion-Fail). Zusätzlich ``Connection: close``:
    Der gunicorn-gthread-Worker blockiert nach einem POST über eine
    Keep-Alive-Verbindung den graceful Shutdown um volle 30s
    (graceful_timeout) — auch wenn der Client die Verbindung längst
    geschlossen hat. Das ließ den Server-Teardown der base_url-Fixture
    (proc.wait(timeout=10)) reproduzierbar in TimeoutExpired laufen.
    """
    session = requests.Session()
    session.headers["Connection"] = "close"
    try:
        login_page = session.get(f"{base_url}/login/", timeout=10)
        assert login_page.status_code == 200, f"Login-Seite antwortete {login_page.status_code}"
        token = session.cookies.get("csrftoken")
        response = session.post(
            f"{base_url}/login/",
            data={"username": username, "password": PASSWORD, "csrfmiddlewaretoken": token},
            headers={"Referer": f"{base_url}/login/"},
            timeout=10,
            allow_redirects=False,
        )
        assert response.status_code == 302, f"Login {username} fehlgeschlagen: {response.status_code}"
    except BaseException:
        session.close()
        raise
    return session, response


def session_cookie_header(login_response):
    """Roher ``Set-Cookie``-Header des Session-Cookies — oder ``""``.

    ``response.headers["Set-Cookie"]`` fasst mehrere Set-Cookie-Header
    kommasepariert zusammen; ``raw.headers.getlist`` liefert sie einzeln.
    Das macht das Parsing unabhängig von der Header-Reihenfolge
    (csrftoken vs. sessionid) und verhindert False-Positives durch
    Attribute des jeweils anderen Cookies.
    """
    matches = [h for h in login_response.raw.headers.getlist("Set-Cookie") if h.startswith("sessionid=")]
    return matches[0] if matches else ""
