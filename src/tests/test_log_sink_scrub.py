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

from core.logging import UNREPRESENTABLE, JsonFormatter, VerboseScrubbingFormatter
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

    def test_structured_list_values_are_masked(self):
        """Auch Listen in einem strukturierten Feld laufen durch die Maskierung."""
        record = _raw_record("csp_violation", csp_violation={"scripts": [INVITE_URL]})
        for line in _rendered_sink_lines(record):
            assert INVITE_TOKEN not in line
            assert SEARCH_TERM not in line

    def test_unresolvable_urlconf_falls_back_to_the_heuristic(self, monkeypatch):
        """Faellt die Routenaufloesung aus, greift die Segment-Heuristik.

        Der Log-Sink laeuft auch in Zustaenden, in denen der URLconf nicht
        (mehr) bereitsteht — dann darf die Maskierung weder werfen noch den
        Pfad durchreichen.
        """
        import django.urls

        def boom(*args, **kwargs):
            raise RuntimeError("kein URLconf")

        monkeypatch.setattr(django.urls, "get_resolver", boom)
        record = _raw_record("Not Found: %s", (INVITE_PATH,))
        for line in _rendered_sink_lines(record):
            assert INVITE_TOKEN not in line
            assert "[scrubbed]" in line

    def test_failing_masking_is_fail_closed(self, monkeypatch):
        """Wirft die Maskierung selbst, geht der Platzhalter in den Sink."""
        import core.logging as core_logging

        def boom(text):
            raise RuntimeError("Maskierung kaputt")

        monkeypatch.setattr(core_logging, "mask_paths_in_text", boom)
        record = _raw_record("Not Found: %s", (INVITE_PATH,))
        for line in _rendered_sink_lines(record):
            assert INVITE_TOKEN not in line
            assert "[scrubbed]" in line

    def test_unformattable_exc_info_does_not_break_the_line(self):
        """Ein kaputtes ``exc_info`` kostet die Zeile nicht.

        ``exc_info`` kommt vom Aufrufer (``log_message(..., exc_info=…)``);
        ein Tripel mit unbrauchbarem Traceback bringt ``formatException``
        zu Fall. Die Nachricht selbst bleibt erhalten und maskiert.
        """
        record = _raw_record(
            "Internal Server Error: %s",
            (INVITE_PATH,),
            exc_info=(ValueError, ValueError("kaputt"), "kein Traceback"),
        )
        for line in _rendered_sink_lines(record):
            assert isinstance(line, str)
            assert INVITE_TOKEN not in line
            assert "Internal Server Error" in line

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


class _BrokenRepr:
    """Objekt, dessen ``__repr__`` (und damit auch ``str()``) wirft.

    Ohne eigenes ``__str__`` faellt ``str()`` in der Stdlib auf ``__repr__``
    zurueck — dieses Objekt bringt also BEIDE Darstellungswege zu Fall. Die
    Ausnahme-Meldung traegt absichtlich ein Token: sie darf auf keinem Weg in
    die Zeile geraten.
    """

    def __repr__(self):
        raise RuntimeError(f"repr kaputt: {INVITE_PATH}")


class _BrokenStr:
    """``str()`` wirft, ``repr()`` traegt einen maskierungspflichtigen Pfad."""

    def __str__(self):
        raise RuntimeError(f"str kaputt: {INVITE_PATH}")

    def __repr__(self):
        return f"<Probe {INVITE_PATH}>"


class _HostileRecord:
    """Record-artiges Objekt, bei dem JEDER Attributzugriff wirft.

    Kein realistischer Django-Record, sondern die Probe auf die Zusage
    selbst: der aeusserste Fallback darf sich auf kein einziges Feld
    verlassen koennen.
    """

    def __getattr__(self, item):
        raise RuntimeError(f"Attribut {item} kaputt")


def _cyclic_field():
    """Strukturiertes Feld mit Zyklus — ein Dict, das sich selbst enthaelt."""
    inner = {"document-uri": INVITE_URL}
    inner["self"] = inner
    return inner


def _self_referencing_list():
    """Dieselbe Falle in Listenform (``_scrub_value`` laeuft auch dort)."""
    items = [INVITE_URL]
    items.append(items)
    return items


#: Name → (Formatter-Index in ``_configured_formatters()``).
FORMATTER_INDEX = {"verbose": 0, "json": 1}


def _assert_line_is_deliverable(record, formatter_name):
    """Die Zeile entsteht, traegt kein Token und keinen Formatter-Traceback."""
    formatter = _configured_formatters()[FORMATTER_INDEX[formatter_name]]
    line = formatter.format(record)
    assert isinstance(line, str) and line, f"{formatter_name}: keine Zeile entstanden"
    assert INVITE_TOKEN not in line, f"{formatter_name}: Token in der Ersatzzeile: {line}"
    assert SEARCH_TERM not in line, f"{formatter_name}: Query-Suchbegriff in der Ersatzzeile: {line}"
    assert "Traceback (most recent call last)" not in line, (
        f"{formatter_name}: der Formatter hat seinen eigenen Traceback ausgegeben: {line}"
    )
    assert "RuntimeError" not in line, f"{formatter_name}: Formatter-Ausnahme in der Zeile: {line}"
    return line


@pytest.mark.parametrize("formatter_name", sorted(FORMATTER_INDEX))
class TestFormattersNeverRaiseOnHostileValues:
    """Die harte Zusage: **kein** Wert im Record bringt einen Formatter zu Fall.

    Seit #2228 laeuft die Maskierung fuer jede Zeile; ein werfender Formatter
    kostet damit nicht nur eine beliebige Zeile, sondern potenziell die
    Sicherheitszeile selbst (``logging`` faengt die Ausnahme im Handler ab,
    druckt „--- Logging error ---" und **verwirft den Record**).

    Der Ersatztext fuer einen nicht darstellbaren Teil ist fest; er darf nie
    der unmaskierte Rohwert sein — sonst waere der Fallback selbst das Leck.
    """

    def test_broken_repr_argument(self, formatter_name):
        """``%s`` auf ein Objekt, dessen Darstellung wirft."""
        record = _raw_record("Not Found: %s", (_BrokenRepr(),))
        _assert_line_is_deliverable(record, formatter_name)

    def test_broken_str_argument(self, formatter_name):
        """``__str__`` wirft, ``__repr__`` traegt einen Pfad — der wird maskiert."""
        record = _raw_record("Not Found: %s", (_BrokenStr(),))
        line = _assert_line_is_deliverable(record, formatter_name)
        assert "[scrubbed]" in line or MASKED_ROUTE in line, f"Pfad aus dem Ersatztext unmaskiert: {line}"

    def test_broken_repr_message(self, formatter_name):
        """Nicht-String-``msg``, dessen Darstellung wirft."""
        record = _raw_record(_BrokenRepr(), ())
        _assert_line_is_deliverable(record, formatter_name)

    def test_broken_percent_format_with_broken_repr(self, formatter_name):
        """Kaputtes ``%``-Format UND kaputte Darstellung des Arguments."""
        record = _raw_record("%d Treffer", (_BrokenRepr(),))
        _assert_line_is_deliverable(record, formatter_name)

    def test_cyclic_structured_field(self, formatter_name):
        """Zyklisches ``extra`` — die Rekursion darf nicht durchschlagen."""
        record = _raw_record("csp_violation", (), csp_violation=_cyclic_field())
        _assert_line_is_deliverable(record, formatter_name)

    def test_self_referencing_list_in_structured_field(self, formatter_name):
        record = _raw_record("csp_violation", (), csp_violation=_self_referencing_list())
        _assert_line_is_deliverable(record, formatter_name)

    def test_broken_repr_inside_structured_field(self, formatter_name):
        """Ein nicht darstellbarer Wert im ``extra`` kostet die Zeile nicht."""
        record = _raw_record("Not Found: %s", (INVITE_PATH,), csp_violation={"probe": _BrokenRepr()})
        _assert_line_is_deliverable(record, formatter_name)

    def test_broken_repr_as_structured_key(self, formatter_name):
        """Auch als Schluessel (``json.dumps`` wirft, ``repr`` ebenfalls)."""
        record = _raw_record("Not Found: %s", (INVITE_PATH,), csp_violation={_BrokenRepr(): "x"})
        _assert_line_is_deliverable(record, formatter_name)

    def test_path_in_structured_key_is_masked(self, formatter_name):
        """Ein Schluessel aus fremder Hand kann den Pfad tragen, nicht der Wert."""
        record = _raw_record("csp_violation", (), csp_violation={INVITE_URL: "blocked"})
        _assert_line_is_deliverable(record, formatter_name)

    def test_broken_mapping_arguments(self, formatter_name):
        """``logger.info("%(a)s", {"a": …})`` — ``args`` ist dann ein Dict."""
        record = _raw_record("%(zahl)d Treffer", ({"zahl": _BrokenRepr()},))
        _assert_line_is_deliverable(record, formatter_name)

    def test_sequence_arguments_that_are_no_tuple(self, formatter_name):
        """``args`` als Liste — weder Tupel noch Mapping, und ``repr`` wirft."""
        record = _raw_record("%s %s", [_BrokenRepr(), "x"])
        _assert_line_is_deliverable(record, formatter_name)

    def test_record_without_usable_attributes(self, formatter_name):
        """Ein Record-artiges Objekt, dessen Felder samt Level/Name werfen.

        Damit faellt auch die Ersatzzeile aus — uebrig bleibt der aeusserste
        Ring, der ohne jeden Record-Wert auskommen muss.
        """
        _assert_line_is_deliverable(_HostileRecord(), formatter_name)

    def test_outer_fallback_when_the_scrub_itself_fails(self, formatter_name, monkeypatch):
        """Der aeusserste Ring: selbst ein kaputter Scrub kostet die Zeile nicht.

        ``scrub_text`` ist der letzte gemeinsame Schritt beider Formatter;
        faellt er aus, bleibt nur noch die konstante Ersatzzeile. Sie traegt
        Level und Logger-Namen und **keinen** Rohtext — ein Fallback, der den
        ungescrubbten Wert durchreichte, waere schlimmer als die verlorene
        Zeile.
        """
        import core.logging as core_logging

        def boom(value):
            raise RuntimeError("Scrub kaputt")

        monkeypatch.setattr(core_logging, "scrub_text", boom)
        record = _raw_record("Not Found: %s", (INVITE_PATH,))
        line = _assert_line_is_deliverable(record, formatter_name)
        assert "nicht formatierbar" in line
        # Faellt der Scrubber selbst aus, laesst sich auch der Logger-Name
        # nicht mehr verbuergen — der Ring gibt fail-closed den festen
        # Platzhalter aus statt eines ungeprueften Rohwerts. Dass der Name im
        # Normalfall (Scrub intakt) erhalten bleibt, pinnt
        # ``TestFallbackValuesAreScrubbed
        # .test_the_outer_fallback_keeps_a_harmless_logger_name``.
        assert REQUEST_LOGGER not in line, f"ungepruefter Logger-Name in der Ersatzzeile: {line}"
        if formatter_name == "json":
            assert json.loads(line)["logger"] == UNREPRESENTABLE, "Die Ersatzzeile muss gueltiges JSON bleiben"
        else:
            assert f"logger={UNREPRESENTABLE}" in line, f"Fail-closed-Platzhalter fehlt: {line}"


class _SensitiveStr:
    """Objekt, dessen ``str()`` einen maskierungspflichtigen Wert traegt.

    ``json.dumps`` kennt den Typ nicht und faellt auf den ``default``-Hook
    zurueck — genau die Ausweichroute, die den Wert bisher roh in die Zeile
    schrieb.
    """

    def __str__(self):
        return INVITE_URL


class _HostileIdentityRecord(_HostileRecord):
    """Record-artig, aber Level und Logger-Name sind lesbar.

    Jeder andere Attributzugriff wirft — beide Formatter landen damit im
    aeussersten Schutzring, der genau diese zwei Werte ausgibt. Sie kommen
    zwar aus dem Anwendungscode, sind aber nicht per se unverdaechtig: ein
    Logger-Name kann einen Pfad tragen (``logging.getLogger(request.path)``).
    """

    def __init__(self, name, levelname):
        self.name = name
        self.levelname = levelname


@pytest.mark.parametrize("formatter_name", sorted(FORMATTER_INDEX))
class TestFallbackValuesAreScrubbed:
    """Jeder **Ausweichwert** laeuft durch den Scrub (Refs #2228).

    Nicht nur Message, Argumente und die Strings strukturierter Felder: auch
    die Darstellungen, die erst entstehen, weil ein Wert sich nicht
    serialisieren laesst (Bytes, ``str``/``repr``-Ausweichroute), und die
    Identitaetswerte des aeussersten Schutzrings. Ein Ausweichwert, der den
    Rohtext durchreicht, ist genau das Leck, gegen das der Sink steht — und
    er trifft ausgerechnet den Fall, in dem schon etwas schiefgegangen ist.
    """

    def test_bytes_structured_field(self, formatter_name):
        """Bytes sind kein ``str`` — der Scrub der Strings greift nicht."""
        record = _raw_record("csp_violation", (), csp_violation=INVITE_URL.encode())
        _assert_line_is_deliverable(record, formatter_name)

    def test_bytes_nested_in_a_structured_field(self, formatter_name):
        record = _raw_record("csp_violation", (), csp_violation={"blocked-uri": [INVITE_URL.encode()]})
        _assert_line_is_deliverable(record, formatter_name)

    def test_bytes_that_are_no_valid_utf8(self, formatter_name):
        """Kaputte Bytes werden sicher dekodiert, statt die Zeile zu kosten."""
        record = _raw_record("csp_violation", (), csp_violation=INVITE_URL.encode() + b"\xff\xfe")
        _assert_line_is_deliverable(record, formatter_name)

    def test_object_with_sensitive_str(self, formatter_name):
        """``str()`` gelingt und traegt den Wert — die Ausweichroute scrubbt."""
        record = _raw_record("csp_violation", (), csp_violation=_SensitiveStr())
        _assert_line_is_deliverable(record, formatter_name)

    def test_object_with_sensitive_repr_when_str_fails(self, formatter_name):
        """``str()`` wirft, ``repr()`` traegt den Pfad — auch das wird maskiert."""
        record = _raw_record("csp_violation", (), csp_violation={"probe": _BrokenStr()})
        _assert_line_is_deliverable(record, formatter_name)

    def test_sensitive_value_in_the_log_arguments(self, formatter_name):
        """Ein Bytes-Argument laeuft ueber ``%s`` in die Nachricht."""
        record = _raw_record("Not Found: %s", (INVITE_URL.encode(),))
        _assert_line_is_deliverable(record, formatter_name)

    def test_sensitive_identity_in_the_outer_fallback(self, formatter_name):
        """Loggername und Level des aeussersten Rings laufen durch den Scrub."""
        record = _HostileIdentityRecord(
            name=f"django.request{INVITE_PATH}",
            levelname=f"WARNING {INVITE_PATH}",
        )
        _assert_line_is_deliverable(record, formatter_name)

    def test_the_outer_fallback_keeps_a_harmless_logger_name(self, formatter_name):
        """Der Scrub kostet den Diagnosewert nicht: der Name bleibt stehen."""
        record = _HostileIdentityRecord(name=REQUEST_LOGGER, levelname="WARNING")
        line = _assert_line_is_deliverable(record, formatter_name)
        assert REQUEST_LOGGER in line, f"Logger-Bezug verloren: {line}"
        assert "WARNING" in line, f"Level verloren: {line}"


# --- Billiger Negativpfad vor der Routenaufloesung (Refs #2228) -------------
#
# Die Pfadmaskierung loeste bis hierher fuer JEDE Zeile mit Pfad die Route auf
# — auch fuer Pfade, unter denen der URLconf ueberhaupt nichts kennt. Das ist
# der von aussen ausloesbare Fall (404-Flut mit immer neuen Pfaden): jeder
# einzelne kostete einen vollstaendigen Durchlauf durch alle URL-Muster und
# verdraengte ausserdem nuetzliche Eintraege aus dem Memoisierungs-Cache.
#
# Der Vorfilter darf die Zusage nicht schwaechen: was die Fassung ohne
# Vorfilter maskiert, muss sie mit Vorfilter identisch maskieren. Genau das
# prueft die Aequivalenz unten — Referenz ist dieselbe Funktion mit
# abgeschaltetem Vorfilter.

PATH_CONVERTER_SAMPLES = {
    "uidb64": INVITE_UIDB64,
    "token": INVITE_TOKEN,
    "int": "42",
    "str": "probe",
    "slug": "eine-probe",
    "uuid": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    "path": "unter/pfad",
}


def _sample_for(part):
    """Konkreter Wert fuer einen Routen-Platzhalter (``<int:pk>``)."""
    converter, _, name = part.partition(":")
    if not name:
        converter, name = "", part
    return PATH_CONVERTER_SAMPLES.get(converter) or PATH_CONVERTER_SAMPLES.get(name) or "probe"


def _concrete_routes(patterns=None, prefix="", depth=0):
    """Konkrete Pfade aus den echten URL-Mustern (Platzhalter eingesetzt).

    Damit deckt der Aequivalenztest jede Routenklasse der Anwendung ab und
    nicht nur die im Guard von Hand notierten Faelle.
    """
    import re as _re

    from django.urls import URLPattern, URLResolver, get_resolver

    if patterns is None:
        patterns = get_resolver().url_patterns
    if depth > 3:
        return []
    found = []
    for entry in patterns:
        route = getattr(getattr(entry, "pattern", None), "_route", None)
        if route is None:
            continue
        full = prefix + route
        if isinstance(entry, URLResolver):
            found.extend(_concrete_routes(entry.url_patterns, full, depth + 1))
        elif isinstance(entry, URLPattern):
            found.append("/" + _re.sub(r"<([^>]+)>", lambda m: _sample_for(m.group(1)), full))
    return found


def _generated_paths(prefix, count=30):
    """*count* Pfade derselben Klasse — jeder mit eigenem Token-Segment."""
    return [f"{prefix}/{index:04d}{INVITE_TOKEN}/weiter{index}/" for index in range(count)]


#: Pfadklassen des Aequivalenztests. Die Namen stehen als Konstante, damit die
#: Parametrisierung beim Einsammeln der Tests nicht den URLconf importiert —
#: das faende sonst zur Sammelzeit statt, also vor der Datenbank-Freigabe.
EQUIVALENCE_CLASSES = (
    "404 unter bekanntem Praefix",
    "404 unter unbekanntem Praefix",
    "Dateipfade aus Tracebacks",
    "Guard-Faelle",
    "echte Routen (alle Klassen des URLconf)",
)


def _equivalence_sample():
    """Alle Pfadklassen, ueber die der Aequivalenztest laeuft."""
    routes = _concrete_routes()
    assert len(routes) >= 30, f"Zu wenige echte Routen fuer den Aequivalenztest: {len(routes)}"
    return {
        "echte Routen (alle Klassen des URLconf)": routes,
        "404 unter bekanntem Praefix": _generated_paths("/invite")
        + _generated_paths("/login")
        + _generated_paths("/mfa"),
        "404 unter unbekanntem Praefix": _generated_paths("/gibt-es-nicht")
        + _generated_paths("/xyz")
        + _generated_paths("/static"),
        "Dateipfade aus Tracebacks": [f"/srv/app/src/core/views/datei{i}.py" for i in range(30)],
        "Guard-Faelle": [INVITE_PATH, MISSING_TOKEN_PATH, INVITE_URL, FOREIGN_URL, "/", "/login/", "/robots.txt"],
    }


class TestCheapNegativePathFilter:
    """Der Vorfilter spart die Routenaufloesung — ohne die Zusage zu aendern."""

    def test_unknown_top_level_segment_skips_route_resolution(self, monkeypatch):
        """Kein registriertes erstes Segment ⇒ gar keine Aufloesung.

        Das ist der extern ausloesbare Worst Case: fortlaufend neue 404-Pfade.
        Maskiert wird trotzdem — ueber die Segment-Heuristik.
        """
        import core.logging as core_logging

        calls = []
        original = core_logging._resolved_route

        def spy(resolver, path):
            calls.append(path)
            return original(resolver, path)

        monkeypatch.setattr(core_logging, "_resolved_route", spy)
        masked = core_logging.mask_url(f"/gibt-es-nicht/{INVITE_TOKEN}/weiter/")
        assert INVITE_TOKEN not in masked
        assert "[scrubbed]" in masked
        assert calls == [], f"Routenaufloesung trotz unbekanntem Praefix: {calls}"

    def test_known_top_level_segment_still_resolves(self, monkeypatch):
        """Ein registriertes Praefix laeuft weiter durch die Aufloesung."""
        import core.logging as core_logging

        calls = []
        original = core_logging._resolved_route

        def spy(resolver, path):
            calls.append(path)
            return original(resolver, path)

        monkeypatch.setattr(core_logging, "_resolved_route", spy)
        assert core_logging.mask_url(INVITE_PATH) == MASKED_ROUTE
        assert calls, "Der Vorfilter darf eine echte Route nicht wegfiltern"

    @pytest.mark.parametrize("klasse", EQUIVALENCE_CLASSES)
    def test_masking_is_equivalent_to_the_unfiltered_version(self, klasse, monkeypatch):
        """Jeder Pfad wird mit Vorfilter genau so maskiert wie ohne.

        Referenz ist dieselbe Implementierung mit abgeschaltetem Vorfilter
        (``_top_level_segments`` liefert ``None`` = „nicht bestimmbar").
        """
        import core.logging as core_logging

        sample = _equivalence_sample()
        assert set(sample) == set(EQUIVALENCE_CLASSES), "Pfadklassen und Parametrisierung auseinandergelaufen"
        paths = sample[klasse]
        with_filter = [core_logging.mask_url(path) for path in paths]

        monkeypatch.setattr(core_logging, "_top_level_segments", lambda resolver: None)
        core_logging._resolved_route.cache_clear()
        without_filter = [core_logging.mask_url(path) for path in paths]

        abweichungen = [
            (path, mit, ohne) for path, mit, ohne in zip(paths, with_filter, without_filter, strict=True) if mit != ohne
        ]
        assert not abweichungen, f"Vorfilter aendert die Maskierung ({klasse}): {abweichungen[:5]}"

    def test_filter_is_disabled_when_the_urlconf_is_not_analysable(self, monkeypatch):
        """Laesst sich das erste Segment nicht bestimmen, faellt der Filter aus.

        Fail-open ist hier richtig herum: der Filter darf nur Arbeit sparen,
        nie eine Maskierung verhindern.
        """
        import core.logging as core_logging

        monkeypatch.setattr(core_logging, "_top_level_segments", lambda resolver: None)
        core_logging._resolved_route.cache_clear()
        assert core_logging.mask_url(INVITE_PATH) == MASKED_ROUTE


class _FakePattern:
    """Minimales ``URLPattern.pattern``-Double (nur ``_route``)."""

    def __init__(self, route=None):
        if route is not None:
            self._route = route


class _FakeEntry:
    """Muster-Double: Blatt (ohne ``url_patterns``) oder ``include()``."""

    def __init__(self, route=None, children=None):
        self.pattern = _FakePattern(route)
        if children is not None:
            self.url_patterns = children


class _FakeResolver:
    def __init__(self, patterns):
        self.url_patterns = patterns


class _ExplodingResolver:
    @property
    def url_patterns(self):
        raise RuntimeError("URLconf kaputt")


class TestTopLevelSegmentAnalysis:
    """Wann der Vorfilter greift — und wann er sich selbst abschaltet.

    Fail-open ist Absicht: ein URLconf, dessen erstes Segment nicht
    feststeht, darf keine Maskierung verhindern.
    """

    def _segments(self, patterns):
        import core.logging as core_logging

        return core_logging._top_level_segments(_FakeResolver(tuple(patterns)))

    def test_static_heads_and_nested_include_without_prefix(self):
        segments = self._segments(
            [
                _FakeEntry("robots.txt"),
                _FakeEntry("invite/<uidb64>/<token>/"),
                _FakeEntry("mfa/", children=(_FakeEntry("setup/"),)),
                _FakeEntry("", children=(_FakeEntry("start/"), _FakeEntry(""))),
            ]
        )
        assert segments == frozenset({"robots.txt", "invite", "mfa", "start", ""})

    def test_pattern_without_route_disables_the_filter(self):
        """``re_path``/Sprachpraefix — kein ``_route``, also keine Aussage."""
        assert self._segments([_FakeEntry("start/"), _FakeEntry(None)]) is None

    def test_placeholder_in_the_first_segment_disables_the_filter(self):
        assert self._segments([_FakeEntry("<lang>/hilfe/")]) is None

    def test_include_without_separating_slash_disables_the_filter(self):
        """``path("api", include(...))`` — die Kinder setzen das Segment fort."""
        assert self._segments([_FakeEntry("api", children=(_FakeEntry("v1/"),))]) is None

    def test_deep_nesting_disables_the_filter(self):
        entry = _FakeEntry("start/")
        for _ in range(5):
            entry = _FakeEntry("", children=(entry,))
        assert self._segments([entry]) is None

    def test_unreadable_urlconf_disables_the_filter(self):
        import core.logging as core_logging

        assert core_logging._top_level_segments(_ExplodingResolver()) is None
