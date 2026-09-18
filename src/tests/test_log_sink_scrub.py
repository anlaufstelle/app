"""Guard: der Server-Log-Sink traegt weder Login-Namen noch Pfad-Token (Refs #2228).

Geprueft wird nicht das Aufruf-Argument, sondern die **ausgelieferte Zeile
beider produktiv konfigurierter Formatter**. Die Formatter werden dafuer aus
``settings.LOGGING`` instanziiert (dieselbe ``dictConfig``-Mechanik, die der
Prozessstart benutzt) — ein Guard, der eine handgebaute Formatter-Instanz
prueft, belegt den Sink nicht, sondern nur sich selbst.

In den Sink schreiben zwei Gruppen von Zeilen:

* **eigene Zeilen** — ``core/views/errors.py`` (``csrf_failure``) bei jedem
  CSRF-403 und ``core/views/csp_report.py`` bei jedem CSP-Verstoss;
* **Framework-Zeilen** — Djangos ``log_response``/``log_message``
  (``django.security.csrf``, ``django.request``) schreibt bei CSRF-403, 403
  und 404 den **rohen** ``request.path`` und bei CSRF zusaetzlich den
  **rohen** Ablehnungsgrund, der bei Referer-/Origin-Pruefungen die
  vollstaendige abgewiesene URL samt Query enthaelt
  (``django/middleware/csrf.py``, ``django/core/handlers/*.py``).

Beide Gruppen laufen durch dieselben zwei Formatter und werden deshalb
gemeinsam geprueft: es gibt in diesem Guard **keinen** Filter, der
Framework-Zeilen ausnimmt. Der Diagnosewert (Route, Statuscode, Logger-Name,
Direktive, Konto-PK) wird mitgepinnt: ein Scrub, der die Zeile unbrauchbar
macht, ist kein Fix.
"""

import json
import logging
import logging.config
import sys

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core.logging import JsonFormatter, VerboseScrubbingFormatter
from core.models import AuditLog

CSRF_LOGGER = "django.security.csrf"
REQUEST_LOGGER = "django.request"
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
#: Fremde Herkunft — genau die Konstellation, die Django mit der
#: vollstaendigen URL im Ablehnungsgrund quittiert.
FOREIGN_URL = f"https://foreign.example/invite/{INVITE_UIDB64}/{INVITE_TOKEN}/?q={SEARCH_TERM}"
#: 404-Probe auf einer Token-Route (ein Segment mehr als die echte Route).
MISSING_TOKEN_PATH = f"/password-reset/{INVITE_UIDB64}/{INVITE_TOKEN}/nicht-da/"
#: Route-Schema, das nach der Maskierung bleiben MUSS (Diagnosewert).
MASKED_ROUTE = "/invite/<uidb64>/<token>/"

#: Was in KEINER Sink-Zeile stehen darf.
FORBIDDEN_IN_SINK = (LOGIN_NAME, INVITE_TOKEN, SEARCH_TERM)


def _configured_formatters():
    """Die beiden Formatter aus ``settings.LOGGING`` — wie im Prozessstart.

    ``dictConfigClass`` ist die Klasse, die ``logging.config.dictConfig``
    selbst benutzt; ``configure_formatter`` bekommt eine Kopie, weil die
    stdlib den Konfigurationsdict beim Aufloesen der ``()``-Factory mutiert.
    """
    configurator = logging.config.dictConfigClass(settings.LOGGING)
    return tuple(
        configurator.configure_formatter(dict(settings.LOGGING["formatters"][name])) for name in ("verbose", "json")
    )


def _rendered_sink_lines(record):
    """Die Zeile, wie sie beide produktiven Formatter in den Sink schreiben."""
    return tuple(formatter.format(record) for formatter in _configured_formatters())


def _assert_sink_is_clean(records, forbidden=FORBIDDEN_IN_SINK):
    """Keine der gerenderten Zeilen traegt ein verbotenes Fragment."""
    assert records, "Es wurde keine Log-Zeile aufgezeichnet — der Guard prueft sonst nichts"
    for record in records:
        for line in _rendered_sink_lines(record):
            for needle in forbidden:
                assert needle not in line, f"{needle!r} steht in der Sink-Zeile ({record.name}): {line}"


def test_configured_formatters_are_the_production_classes():
    """Der Guard prueft die beiden Klassen, die auch am Handler haengen."""
    verbose, as_json = _configured_formatters()
    assert isinstance(verbose, VerboseScrubbingFormatter)
    assert isinstance(as_json, JsonFormatter)
    # Genau ein Handler, und der traegt einen dieser beiden Formatter.
    handlers = settings.LOGGING["handlers"]
    assert set(handlers) == {"console"}
    assert handlers["console"]["formatter"] in {"verbose", "json"}


@pytest.fixture
def sink_caplog(caplog):
    """``caplog`` sieht ALLE Zeilen der Sink-Kette.

    ``django`` traegt in ``settings/base.py`` ``propagate=False`` — der
    Root-Handler von ``caplog`` sieht weder ``django.request`` noch
    ``django.security.csrf``. Der Capture-Handler wird deshalb genau einmal
    an ``django`` gehaengt (die Kindlogger propagieren dorthin) und im
    Teardown wieder entfernt. ``security.csp`` propagiert regulaer bis zur
    Wurzel und ist damit bereits erfasst.
    """
    django_logger = logging.getLogger("django")
    django_logger.addHandler(caplog.handler)
    caplog.set_level(logging.WARNING)
    try:
        yield caplog
    finally:
        django_logger.removeHandler(caplog.handler)


def _view_records(caplog):
    """Nur die Zeilen aus ``csrf_failure`` — dedupliziert.

    Wird ausschliesslich dort benutzt, wo die eigene Zeile der Prueffall
    ist (Konto-Feld); die Leck-Pruefungen laufen ueber ALLE Records.
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
    """CSRF-403: weder eigene noch Framework-Zeile tragen Token oder Query."""

    def _post_login(self, user=None, **extra):
        client = Client(enforce_csrf_checks=True)
        if user is not None:
            client.force_login(user)
        return client.post("/login/", {"username": "x", "password": "y"}, **extra)

    def test_referer_rejection_leaks_nothing(self, csrf_user, sink_caplog):
        """HTTPS ohne ``Origin``, abgewiesener ``Referer`` mit Query + Token.

        Django legt die vollstaendige abgewiesene URL in den Ablehnungsgrund
        (``REASON_BAD_REFERER``); der Grund steht in der eigenen Zeile UND in
        der Framework-Zeile ``Forbidden (%s): %s``.
        """
        response = self._post_login(csrf_user, secure=True, HTTP_REFERER=FOREIGN_URL)
        assert response.status_code == 403

        records = list(sink_caplog.records)
        assert len(records) >= 2, "Erwartet: eigene CSRF-Zeile UND Framework-Zeile"
        assert any("Forbidden" in str(rec.msg) for rec in records), "Framework-Zeile fehlt im Guard"
        _assert_sink_is_clean(records)

    def test_origin_rejection_leaks_nothing(self, csrf_user, sink_caplog):
        """Frei vorgebbarer ``Origin`` mit Query + Token (``REASON_BAD_ORIGIN``)."""
        response = self._post_login(csrf_user, HTTP_ORIGIN=FOREIGN_URL)
        assert response.status_code == 403

        records = list(sink_caplog.records)
        assert any("Forbidden" in str(rec.msg) for rec in records)
        _assert_sink_is_clean(records)

    def test_token_route_leaks_nothing(self, csrf_user, sink_caplog):
        """403 auf einer Token-Route: der rohe ``request.path`` der Framework-Zeile."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(csrf_user)
        response = client.post(INVITE_PATH, {"x": "1"})
        assert response.status_code == 403

        records = list(sink_caplog.records)
        assert any("Forbidden" in str(rec.msg) for rec in records)
        _assert_sink_is_clean(records)

    def test_sink_line_keeps_diagnostic_value(self, csrf_user, sink_caplog):
        """Pfad, Route des Referers, HTMX-Flag und Konto-Bezug bleiben lesbar."""
        response = self._post_login(
            csrf_user,
            HTTP_REFERER=INVITE_URL,
            HTTP_ORIGIN="https://anlaufstelle.example",
        )
        assert response.status_code == 403

        (record,) = _view_records(sink_caplog)
        # Die Konto-Zuordnung bleibt — als PK, nicht als Login-Name.
        assert record.user_pk == csrf_user.pk
        verbose_line, json_line = _rendered_sink_lines(record)
        for line in (verbose_line, json_line):
            assert "CSRF failure" in line
            assert "path=/login/" in line
            assert MASKED_ROUTE in line, f"Route des Referers verloren: {line}"
            assert "origin=https://anlaufstelle.example" in line
            assert "user_pk" in line
        assert json.loads(json_line)["logger"] == CSRF_LOGGER

    def test_framework_line_keeps_diagnostic_value(self, csrf_user, sink_caplog):
        """Die Framework-Zeile behaelt Fehlerklasse, Route, Statuscode, Logger."""
        response = self._post_login(csrf_user, secure=True, HTTP_REFERER=FOREIGN_URL)
        assert response.status_code == 403

        (record,) = [rec for rec in sink_caplog.records if "Forbidden" in str(rec.msg)]
        assert record.status_code == 403
        verbose_line, json_line = _rendered_sink_lines(record)
        for line in (verbose_line, json_line):
            assert "Forbidden" in line
            assert "Referer checking failed" in line, f"Fehlerklasse verloren: {line}"
            assert MASKED_ROUTE in line, f"Route der abgewiesenen Herkunft verloren: {line}"
            assert "/login/" in line, f"Angefragter Pfad verloren: {line}"
        assert json.loads(json_line)["logger"] == CSRF_LOGGER

    def test_anonymous_request_has_no_user_pk(self, sink_caplog):
        """Ohne Anmeldung bleibt das Konto-Feld leer statt ``anonymous`` zu raten."""
        response = self._post_login()
        assert response.status_code == 403

        (record,) = _view_records(sink_caplog)
        assert getattr(record, "user_pk", None) is None


@pytest.mark.django_db
class TestFrameworkRequestLogSink:
    """``django.request``: 404 und 403 schreiben den rohen ``request.path``."""

    def test_404_on_token_path_leaks_nothing(self, csrf_user, sink_caplog):
        client = Client()
        client.force_login(csrf_user)
        response = client.get(MISSING_TOKEN_PATH)
        assert response.status_code == 404

        records = [rec for rec in sink_caplog.records if rec.name == REQUEST_LOGGER]
        assert records, "Django loggt jede 404 auf django.request"
        _assert_sink_is_clean(records)
        for record in records:
            for line in _rendered_sink_lines(record):
                assert "Not Found" in line
                assert "/password-reset/" in line, f"Diagnosewert des Pfades verloren: {line}"

    def test_403_permission_denied_leaks_nothing(self, client, staff_user, facility, sink_caplog):
        """Ein echter ``PermissionDenied`` aus dem Rollen-Gate (kein CSRF-Fall)."""
        entry = AuditLog.objects.create(facility=facility, user=staff_user, action=AuditLog.Action.LOGIN)
        client.force_login(staff_user)
        response = client.get(reverse("core:audit_detail", kwargs={"pk": entry.pk}))
        assert response.status_code == 403

        records = [rec for rec in sink_caplog.records if rec.name == REQUEST_LOGGER]
        assert records, "Django loggt jede 403 auf django.request"
        _assert_sink_is_clean(records, forbidden=(INVITE_TOKEN, SEARCH_TERM))
        for record in records:
            for line in _rendered_sink_lines(record):
                assert "Forbidden" in line


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


def _raw_record(msg, args=(), exc_info=None, **extra):
    record = logging.LogRecord(
        name="django.request",
        level=logging.WARNING,
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )
    record.module = "test"
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestFormatterRobustness:
    """Die Maskierung laeuft fuer JEDE Zeile — sie darf nie werfen.

    Ein Formatter, der wirft, kostet die Zeile: ``logging`` faengt die
    Ausnahme im Handler ab, druckt „--- Logging error ---" und verwirft den
    Record. Bei einem Sicherheits-Sink waere genau das der Ausfall.
    """

    @pytest.mark.parametrize(
        "msg,args",
        [
            ("Not Found: %s", (object(),)),  # nicht-String-Argument
            ("Forbidden (%s): %s", ("nur ein Argument",)),  # kaputtes %-Format
            ("%d Treffer", ("keine Zahl",)),  # falscher Konversionstyp
            (None, ()),  # msg = None
            (object(), ()),  # msg ohne __str__-Vertrag fuer %
        ],
    )
    def test_formatters_never_raise(self, msg, args):
        record = _raw_record(msg, args)
        for line in _rendered_sink_lines(record):
            assert isinstance(line, str)
            assert line

    def test_non_serialisable_structured_field_survives(self):
        record = _raw_record("Not Found: %s", (INVITE_PATH,), csp_violation={object(): object()})
        for line in _rendered_sink_lines(record):
            assert INVITE_TOKEN not in line

    def test_masking_is_idempotent(self):
        """Ein bereits maskierter Wert bleibt beim zweiten Durchlauf stabil.

        Beide Formatter koennen am selben Record haengen, und eine Zeile kann
        aus einer bereits maskierten Quelle stammen — die Maskierung darf den
        Wert dann nicht weiter abbauen.
        """
        raw = _raw_record("Not Found: %s", (INVITE_PATH,))
        verbose_line, json_line = _rendered_sink_lines(raw)
        assert INVITE_TOKEN not in verbose_line
        masked = json.loads(json_line)["message"].removeprefix("Not Found: ")
        assert MASKED_ROUTE in masked

        again = _raw_record("Not Found: %s", (masked,))
        verbose_again, json_again = _rendered_sink_lines(again)
        assert json.loads(json_again)["message"] == f"Not Found: {masked}"
        assert masked in verbose_again

    def test_exception_text_is_masked(self):
        """``exc_info`` traegt die Ausnahme-Argumente woertlich in die Zeile.

        Der Textformatter haengt den Traceback an die Zeile an, die er
        scrubbt — der JSON-Formatter muss dieselbe Zusage geben, sonst haengt
        der Schutz am Ausgabeformat.
        """
        try:
            raise ValueError(f"Token abgelehnt: {INVITE_PATH}")
        except ValueError:
            record = _raw_record("Internal Server Error: %s", (INVITE_PATH,), exc_info=sys.exc_info())
        for line in _rendered_sink_lines(record):
            assert INVITE_TOKEN not in line, f"Token aus dem Traceback in der Sink-Zeile: {line}"
