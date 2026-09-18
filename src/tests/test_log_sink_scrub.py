"""Guard: der Server-Log-Sink traegt weder Login-Namen noch Pfad-Token (Refs #2228).

Zwei Fundstellen schreiben direkt in die Server-Logdatei, ohne den
Scrub-Vertrag des Sentry-Pfades zu erfuellen:

* ``core/views/errors.py`` (``csrf_failure``) protokolliert bei JEDEM
  CSRF-403 Pfad, ``Referer``, ``Origin`` und den Login-Namen auf dem Logger
  ``django.security.csrf``. Der ``Referer`` traegt auf einer Token-Route das
  live nutzbare Token und im Query-Teil den Suchbegriff — in kleinen
  Einrichtungen ist beides personenbeziehbar bis personenbezogen.
* ``core/views/csp_report.py`` uebernimmt ``document-uri``/``blocked-uri``/
  ``source-file``/``referrer`` aus dem Browser-Report. Ein CSP-Verstoss auf
  ``/invite/<uidb64>/<token>/`` schriebe das Token in die Logdatei.

Geprueft wird deshalb nicht das Aufruf-Argument, sondern die **ausgelieferte
Zeile beider produktiv konfigurierter Formatter** (``settings/base.py``:
``verbose`` = ``VerboseScrubbingFormatter``, ``json`` = ``JsonFormatter``) —
genau das, was im Sink steht. Der Diagnosewert (welcher Pfad, welche Route,
welche Direktive, welches Konto) wird mitgepinnt: ein Scrub, der die Zeile
unbrauchbar macht, ist kein Fix.
"""

import json
import logging

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core.logging import JsonFormatter, VerboseScrubbingFormatter

CSRF_LOGGER = "django.security.csrf"
CSP_LOGGER = "security.csp"

#: Erfundene, aber formgetreue Werte. Das Token spiegelt die Form der
#: Einladungs-/Reset-Token (32 zusammenhaengende Hex-Zeichen), ``uidb64`` die
#: base64-kodierte PK. Der Suchbegriff steht fuer den Klarnamen, der ueber den
#: Query-Teil eines ``Referer`` von der Suchseite weg mitreist.
LOGIN_NAME = "a.mustermann"
INVITE_UIDB64 = "MTIz"
INVITE_TOKEN = "cx1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c"
SEARCH_TERM = "Mustermann"
INVITE_PATH = f"/invite/{INVITE_UIDB64}/{INVITE_TOKEN}/"
INVITE_URL = f"https://anlaufstelle.example{INVITE_PATH}?q={SEARCH_TERM}"
#: Route-Schema, das nach der Maskierung bleiben MUSS (Diagnosewert).
MASKED_ROUTE = "/invite/<uidb64>/<token>/"


def _rendered_sink_lines(record):
    """Die Zeile, wie sie beide produktiven Formatter in den Sink schreiben."""
    return (
        VerboseScrubbingFormatter("{levelname} {asctime} {module} {message}", style="{").format(record),
        JsonFormatter().format(record),
    )


@pytest.fixture
def csrf_caplog(caplog):
    """``caplog`` am Logger ``django.security.csrf``.

    Der Logger haengt unter ``django``, das in ``settings/base.py``
    ``propagate=False`` traegt — der Root-Handler von ``caplog`` sieht die
    Zeile also nie. Der Handler wird deshalb direkt angehaengt und im
    Teardown wieder entfernt.
    """
    logger = logging.getLogger(CSRF_LOGGER)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


def _csrf_view_records(caplog):
    """Nur die Zeilen aus ``csrf_failure`` — dedupliziert.

    Auf demselben Logger schreibt auch Djangos ``log_response``
    („Forbidden (…): …"), und der Capture-Handler haengt sowohl am Logger
    selbst als auch an ``root``, sieht denselben Record also mehrfach.
    Gefiltert wird deshalb auf die Vorlage dieser View, dedupliziert ueber
    die Record-Identitaet.
    """
    matching = [rec for rec in caplog.records if str(rec.msg).startswith("CSRF failure")]
    return list({id(rec): rec for rec in matching}.values())


@pytest.fixture
def csrf_user(facility):
    """Konto mit real-namensnahem Login — genau der Fall aus dem Ticket."""
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=LOGIN_NAME,
        role=user_model.Role.STAFF,
        facility=facility,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.mark.django_db
class TestCSRFFailureLogSink:
    """``csrf_failure`` schreibt weder Login-Namen noch Token in den Sink."""

    def _trigger(self, user):
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        return client.post(
            "/login/",
            {"username": "x", "password": "y"},
            HTTP_REFERER=INVITE_URL,
            HTTP_ORIGIN="https://anlaufstelle.example",
        )

    def test_sink_line_carries_no_login_name_no_token_no_query(self, csrf_user, csrf_caplog):
        response = self._trigger(csrf_user)
        assert response.status_code == 403

        records = _csrf_view_records(csrf_caplog)
        assert len(records) == 1, "Genau eine CSRF-Zeile erwartet"
        for line in _rendered_sink_lines(records[0]):
            assert LOGIN_NAME not in line, f"Login-Name in der Server-Logzeile: {line}"
            assert INVITE_TOKEN not in line, f"Einladungs-Token in der Server-Logzeile: {line}"
            assert SEARCH_TERM not in line, f"Query-Suchbegriff in der Server-Logzeile: {line}"

    def test_sink_line_keeps_diagnostic_value(self, csrf_user, csrf_caplog):
        """Pfad, Route des Referers, HTMX-Flag und Konto-Bezug bleiben lesbar."""
        response = self._trigger(csrf_user)
        assert response.status_code == 403

        (record,) = _csrf_view_records(csrf_caplog)
        # Die Konto-Zuordnung bleibt — als PK, nicht als Login-Name.
        assert record.user_pk == csrf_user.pk
        for line in _rendered_sink_lines(record):
            assert "CSRF failure" in line
            assert "path=/login/" in line
            assert MASKED_ROUTE in line, f"Route des Referers verloren: {line}"
            assert "origin=https://anlaufstelle.example" in line
            assert "user_pk" in line

    def test_anonymous_request_has_no_user_pk(self, csrf_caplog):
        """Ohne Anmeldung bleibt das Konto-Feld leer statt ``anonymous`` zu raten."""
        client = Client(enforce_csrf_checks=True)
        response = client.post("/login/", {"username": "x", "password": "y"})
        assert response.status_code == 403

        (record,) = _csrf_view_records(csrf_caplog)
        assert getattr(record, "user_pk", None) is None


@pytest.mark.django_db
class TestCSPReportLogSink:
    """``CSPReportView`` maskiert Pfad-Token in den URL-tragenden Feldern."""

    REPORT = {
        "csp-report": {
            "document-uri": INVITE_URL,
            "referrer": INVITE_URL,
            "source-file": INVITE_URL,
            "blocked-uri": f"https://evil.example/{INVITE_TOKEN}/payload.js",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
        }
    }

    def _post(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger=CSP_LOGGER):
            response = client.post(
                reverse("csp_report"),
                data=json.dumps(self.REPORT),
                content_type="application/csp-report",
            )
        assert response.status_code == 204
        return next(rec for rec in caplog.records if rec.message == "csp_violation")

    def test_path_tokens_are_masked_in_url_fields(self, client, caplog):
        violation = self._post(client, caplog).csp_violation
        assert violation["document-uri"] == f"https://anlaufstelle.example{MASKED_ROUTE}"
        assert violation["referrer"] == f"https://anlaufstelle.example{MASKED_ROUTE}"
        assert violation["source-file"] == f"https://anlaufstelle.example{MASKED_ROUTE}"
        assert INVITE_TOKEN not in violation["blocked-uri"]

    def test_sink_line_carries_no_token_and_no_query(self, client, caplog):
        record = self._post(client, caplog)
        for line in _rendered_sink_lines(record):
            assert INVITE_TOKEN not in line, f"Token in der Server-Logzeile: {line}"
            assert SEARCH_TERM not in line, f"Query-Suchbegriff in der Server-Logzeile: {line}"

    def test_sink_line_keeps_directive_and_route(self, client, caplog):
        """Welcher Pfad, welche Direktive — der Detection-Wert bleibt."""
        record = self._post(client, caplog)
        for line in _rendered_sink_lines(record):
            assert "script-src 'self'" in line
            assert MASKED_ROUTE in line
