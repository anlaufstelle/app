"""Tests fuer den CSP-Violation-Report-Endpoint (Refs #684, Refs #733)."""

import json
import logging

import pytest
from django.test import override_settings
from django.urls import reverse

from core.logging import JsonFormatter, VerboseScrubbingFormatter


@pytest.fixture
def csp_url():
    return reverse("csp_report")


@pytest.mark.django_db
class TestCSPReportEndpoint:
    """Endpoint akzeptiert valide Reports und loggt sie strukturiert."""

    SAMPLE_REPORT = {
        "csp-report": {
            "document-uri": "https://anlaufstelle.app/clients/",
            "referrer": "",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
            "original-policy": "script-src 'self'; report-uri /csp-report/",
            "blocked-uri": "https://evil.example.com/payload.js",
            "status-code": 200,
            "source-file": "https://anlaufstelle.app/clients/",
            "line-number": 42,
        }
    }

    def test_csp_level_2_report_logged(self, client, csp_url, caplog):
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(self.SAMPLE_REPORT),
                content_type="application/csp-report",
            )
        assert response.status_code == 204
        warnings = [r for r in caplog.records if r.message == "csp_violation"]
        assert len(warnings) == 1
        assert warnings[0].csp_violation["blocked-uri"] == "https://evil.example.com/payload.js"

    @override_settings(TRUSTED_PROXY_HOPS=1)
    def test_remote_addr_uses_canonical_proxy_aware_ip(self, client, csp_url, caplog):
        """A5.4 (Refs #1024 / #1016): csp_report nutzt die kanonische
        ``get_client_ip`` (TRUSTED_PROXY_HOPS-bewusst) statt der naiven
        'erste X-Forwarded-For-IP'.

        Bei ``X-Forwarded-For: client, proxy`` wird die proxy-gesetzte (rechte)
        IP geloggt — die linke ist vom Client fälschbar und darf nicht als
        Herkunft im Security-Log erscheinen.
        """
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(self.SAMPLE_REPORT),
                content_type="application/csp-report",
                HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8",
            )
        assert response.status_code == 204
        warnings = [r for r in caplog.records if r.message == "csp_violation"]
        assert len(warnings) == 1
        assert warnings[0].remote_addr == "5.6.7.8"

    def test_csp_level_3_reporting_api_payload(self, client, csp_url, caplog):
        # CSP Level 3 / Reporting API: Liste mit {"type": "csp-violation", "body": {...}}.
        payload = [
            {
                "type": "csp-violation",
                "age": 0,
                "url": "https://anlaufstelle.app/clients/",
                "user_agent": "test",
                "body": {
                    "blockedURL": "https://evil.example.com/payload.js",
                    "violatedDirective": "script-src",
                    "effectiveDirective": "script-src",
                },
            }
        ]
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(payload),
                content_type="application/reports+json",
            )
        assert response.status_code == 204
        warnings = [r for r in caplog.records if r.message == "csp_violation"]
        assert len(warnings) == 1
        assert warnings[0].csp_violation["blockedURL"] == "https://evil.example.com/payload.js"

    def test_malicious_nested_report_is_sanitized(self, client, csp_url, caplog):
        """Refs #1476 (R19): Angreifer-kontrolliertes Report-JSON darf nicht
        verbatim ins ``extra`` des Loggers wandern (Log-Forging/Stored-XSS-
        Risiko im Downstream-Sink, z.B. Sentry). Nur eine Whitelist bekannter
        Felder wird uebernommen, unbekannte Keys und Verschachtelung werden
        verworfen, Strings auf 512 Zeichen gekappt.
        """
        malicious = {
            "csp-report": {
                "blocked-uri": "https://evil.example.com/payload.js",
                "document-uri": "https://anlaufstelle.app/clients/",
                "line-number": 42,
                "column-number": 10**100,  # Riesen-int, muss auf int32 gekappt werden
                "source-file": "x" * 600,  # > 512 Zeichen, muss gekappt werden
                "__proto__": {"nested": "<script>alert(1)</script>"},
                "unknown-field": "sollte gedroppt werden",
                "nested-array": [1, 2, {"a": "b"}],
            }
        }
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(malicious),
                content_type="application/csp-report",
            )
        assert response.status_code == 204
        warnings = [r for r in caplog.records if r.message == "csp_violation"]
        assert len(warnings) == 1
        logged = warnings[0].csp_violation
        assert "unknown-field" not in logged
        assert "__proto__" not in logged
        assert "nested-array" not in logged
        assert logged["blocked-uri"] == "https://evil.example.com/payload.js"
        assert logged["line-number"] == 42
        assert logged["column-number"] == 2**31 - 1
        assert logged["source-file"] == "x" * 512
        assert len(logged["source-file"]) == 512

    def test_unsupported_content_type_rejected(self, client, csp_url):
        response = client.post(
            csp_url,
            data=json.dumps(self.SAMPLE_REPORT),
            content_type="text/plain",
        )
        assert response.status_code == 400

    def test_invalid_json_rejected(self, client, csp_url):
        response = client.post(
            csp_url,
            data=b"<<not json>>",
            content_type="application/csp-report",
        )
        assert response.status_code == 400

    def test_oversized_payload_rejected(self, client, csp_url):
        # 33 KiB > 32 KiB Limit
        large = {"csp-report": {"detail": "x" * (33 * 1024)}}
        response = client.post(
            csp_url,
            data=json.dumps(large),
            content_type="application/csp-report",
        )
        assert response.status_code == 400

    def test_get_method_not_allowed(self, client, csp_url):
        response = client.get(csp_url)
        assert response.status_code == 405


@pytest.mark.django_db
class TestCSPHeaderContainsReportURI:
    """CSP-Header auf jeder Response enthaelt die report-uri-Direktive."""

    def test_report_uri_in_csp_header(self, client):
        # Health-Endpoint ist anonymous-zugaenglich und liefert die globale CSP.
        response = client.get(reverse("health"))
        assert response.status_code == 200
        csp = response.headers.get("Content-Security-Policy", "")
        assert "report-uri /csp-report/" in csp, f"report-uri-Direktive fehlt im CSP-Header: {csp!r}"


# --- Refs #1943: Report-Inhalte muessen im tatsaechlich ausgegebenen
# Log-Record ankommen, der Listenpfad darf das Log nicht aufblaehen und
# Steuerzeichen duerfen die Log-Zeile nicht faelschen. -------------------

# Muss mit settings/base.py ``LOGGING["formatters"]["verbose"]["format"]``
# uebereinstimmen — LOG_FORMAT=text ist der Default in Produktion.
_VERBOSE_FORMAT = "{levelname} {asctime} {module} {message}"


def _text_line(record):
    return VerboseScrubbingFormatter(fmt=_VERBOSE_FORMAT, style="{").format(record)


def _json_entry(record):
    return json.loads(JsonFormatter().format(record))


@pytest.mark.django_db
class TestCSPReportReachesLogSink:
    """Refs #1943: Die sanitisierten Felder muessen die Formatter ueberleben.

    Die Bestandstests pruefen nur ``record.csp_violation`` — also das
    logging-``extra``. Beide konfigurierten Formatter verwarfen das
    ``extra`` aber, sodass produktiv von jedem Report nur die nackte Zeile
    ``csp_violation`` sichtbar war. Diese Tests pruefen deshalb die
    **Formatter-Ausgabe**, nicht die Record-Attribute.
    """

    REPORT = {
        "csp-report": {
            "document-uri": "https://anlaufstelle.app/clients/",
            "violated-directive": "script-src 'self'",
            "blocked-uri": "https://evil.example.com/payload.js",
            "line-number": 42,
        }
    }

    def _post(self, client, csp_url, caplog, payload, content_type="application/csp-report", **extra):
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(payload),
                content_type=content_type,
                **extra,
            )
        return response

    def _violations(self, caplog):
        return [r for r in caplog.records if r.message == "csp_violation"]

    def test_json_formatter_emits_violation_details(self, client, csp_url, caplog):
        response = self._post(
            client,
            csp_url,
            caplog,
            self.REPORT,
            HTTP_USER_AGENT="Mozilla/5.0 (probe)",
            REMOTE_ADDR="203.0.113.7",
        )
        assert response.status_code == 204
        entry = _json_entry(self._violations(caplog)[0])
        assert entry["csp_violation"]["blocked-uri"] == "https://evil.example.com/payload.js"
        assert entry["csp_violation"]["document-uri"] == "https://anlaufstelle.app/clients/"
        assert entry["csp_violation"]["violated-directive"] == "script-src 'self'"
        assert entry["remote_addr"] == "203.0.113.7"
        assert entry["user_agent"] == "Mozilla/5.0 (probe)"

    def test_text_formatter_emits_violation_details(self, client, csp_url, caplog):
        """LOG_FORMAT=text ist der Default — der Textpfad darf nicht blind bleiben."""
        response = self._post(
            client,
            csp_url,
            caplog,
            self.REPORT,
            HTTP_USER_AGENT="Mozilla/5.0 (probe)",
            REMOTE_ADDR="203.0.113.7",
        )
        assert response.status_code == 204
        line = _text_line(self._violations(caplog)[0])
        assert "https://evil.example.com/payload.js" in line
        assert "203.0.113.7" in line
        assert "Mozilla/5.0 (probe)" in line
        assert "\n" not in line

    def test_formatters_scrub_pii_from_violation_details(self, client, csp_url, caplog):
        """Die Report-URLs sind fremdbestimmt und koennen Credentials tragen —
        der PII-Scrub muss auch auf den strukturierten Feldern greifen."""
        payload = {
            "csp-report": {
                "document-uri": "https://anlaufstelle.app/login?password=hunter2",
                "blocked-uri": "https://evil.example.com/?mail=opfer@example.com",
            }
        }
        assert self._post(client, csp_url, caplog, payload).status_code == 204
        record = self._violations(caplog)[0]
        rendered = _text_line(record) + json.dumps(_json_entry(record), ensure_ascii=False)
        assert "hunter2" not in rendered
        assert "opfer@example.com" not in rendered
        assert "password=<redacted>" in rendered
        assert "<email>" in rendered


@pytest.mark.django_db
class TestCSPReportLogAmplification:
    """Refs #1943: Ein Request darf nicht beliebig viele Log-Zeilen erzeugen."""

    def test_level_3_list_is_capped_with_aggregate_record(self, client, csp_url, caplog):
        payload = [
            {"type": "csp-violation", "body": {"blockedURL": f"https://evil.example.com/{i}.js"}} for i in range(50)
        ]
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(payload),
                content_type="application/reports+json",
                REMOTE_ADDR="203.0.113.7",
            )
        assert response.status_code == 204
        violations = [r for r in caplog.records if r.message == "csp_violation"]
        assert len(violations) == 10, "Listenpfad muss auf 10 Violations pro Request gedeckelt sein"
        truncated = [r for r in caplog.records if r.message == "csp_violation_truncated"]
        assert len(truncated) == 1, "Der verworfene Rest muss EINMAL aggregiert vermerkt werden"
        assert truncated[0].csp_violations_total == 50
        assert truncated[0].csp_violations_dropped == 40
        entry = json.loads(JsonFormatter().format(truncated[0]))
        assert entry["csp_violations_total"] == 50
        assert entry["csp_violations_dropped"] == 40
        assert entry["remote_addr"] == "203.0.113.7"
        # Der Aggregat-Record ist die einzige Spur des Verworfenen — er muss
        # auch im Default-Textpfad die Zahlen tragen, nicht nur im JSON.
        line = _text_line(truncated[0])
        assert "csp_violations_total=50" in line
        assert "csp_violations_dropped=40" in line
        assert "\n" not in line

    def test_short_list_produces_no_aggregate_record(self, client, csp_url, caplog):
        payload = [{"type": "csp-violation", "body": {"blockedURL": "https://evil.example.com/a.js"}}]
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(csp_url, data=json.dumps(payload), content_type="application/reports+json")
        assert response.status_code == 204
        assert [r for r in caplog.records if r.message == "csp_violation_truncated"] == []


@pytest.mark.django_db
class TestCSPReportControlCharacters:
    """Refs #1943: Steuerzeichen aus Angreiferfeldern duerfen die Log-Zeile
    nicht faelschen (Log-Forging) — scharf, seit die Felder ausgegeben werden."""

    FORGED = "https://evil.example/a\nWARNING 2026-08-18 12:00:00,000 csp_report FAKE-ADMIN-LOGIN\r\n\x1b[31mred\x07"

    def test_control_characters_are_stripped(self, client, csp_url, caplog):
        payload = {"csp-report": {"blocked-uri": self.FORGED}}
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(payload),
                content_type="application/csp-report",
                HTTP_USER_AGENT="curl/8\r\nX-Injected: 1\x1b[2J",
            )
        assert response.status_code == 204
        record = [r for r in caplog.records if r.message == "csp_violation"][0]
        logged_uri = record.csp_violation["blocked-uri"]
        for char in ("\n", "\r", "\x1b", "\x07"):
            assert char not in logged_uri
            assert char not in record.user_agent
        line = _text_line(record)
        assert "\n" not in line, "Eine Violation darf nur EINE Log-Zeile erzeugen"
        assert "\x1b" not in line and "\x07" not in line
        assert "https://evil.example/a" in line

    def test_unicode_format_and_bidi_characters_are_stripped(self, client, csp_url, caplog):
        """Nicht nur ASCII-Steuerzeichen: RTL-Override (U+202E) kann eine URL
        im Log-Viewer rueckwaerts darstellen, Zero-Width-Space (U+200B) macht
        zwei verschiedene Hosts optisch gleich, U+2028 ist fuer manche Parser
        ein Zeilenumbruch. Umlaute und andere echte Schriftzeichen muessen
        dagegen ueberleben — sonst verstuemmelt der Filter reale URLs.
        """
        payload = {
            "csp-report": {
                "blocked-uri": "https://evil.example/\u202egpj.exe\u200b?x=1\u2028y=2",
                "document-uri": "https://anlaufstelle.app/klient-uebersicht/\u00e4\u00f6\u00fc\u00df",
            }
        }
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(csp_url, data=json.dumps(payload), content_type="application/csp-report")
        assert response.status_code == 204
        record = [r for r in caplog.records if r.message == "csp_violation"][0]
        blocked = record.csp_violation["blocked-uri"]
        for char in ("\u202e", "\u200b", "\u2028"):
            assert char not in blocked
        assert blocked == "https://evil.example/gpj.exe?x=1y=2"
        assert (
            record.csp_violation["document-uri"]
            == "https://anlaufstelle.app/klient-uebersicht/\u00e4\u00f6\u00fc\u00df"
        )

    def test_user_agent_is_length_capped(self, client, csp_url, caplog):
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps({"csp-report": {"blocked-uri": "https://evil.example/a.js"}}),
                content_type="application/csp-report",
                HTTP_USER_AGENT="U" * 2000,
            )
        assert response.status_code == 204
        record = [r for r in caplog.records if r.message == "csp_violation"][0]
        assert len(record.user_agent) == 512


@pytest.mark.django_db
class TestCSPReportOversizedBody:
    """Refs #1943: ``RequestDataTooBig`` darf keinen ERROR-Traceback erzeugen."""

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=256)
    def test_request_data_too_big_answered_with_plain_400(self, client, csp_url, caplog):
        payload = {"csp-report": {"blocked-uri": "https://evil.example.com/" + "x" * 500}}
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            response = client.post(
                csp_url,
                data=json.dumps(payload),
                content_type="application/csp-report",
            )
        assert response.status_code == 400
        assert response.content == b"payload too large"
        csp_records = [r for r in caplog.records if r.name == "security.csp"]
        assert len(csp_records) == 1
        assert csp_records[0].exc_info is None
        assert csp_records[0].levelno == logging.WARNING
        # Eine Zeile statt ~20 Traceback-Zeilen — und die IP muss drinstehen,
        # sonst ist der Missbrauch nicht zuzuordnen.
        line = _text_line(csp_records[0])
        assert line.count("\n") == 0
        assert "csp_report_oversized_body" in line
        assert "Traceback" not in line
        assert "remote_addr=" in line


@pytest.mark.django_db
class TestCSPReportRateLimit:
    """Refs #1949: Das Rate-Limit ``10/m`` pro IP war nirgends abgesichert.

    Nach dem Violation-Cap aus #1943 ist es die verbleibende
    *Mengen*-Begrenzung der unauthentifizierten Flaeche: der Cap deckelt die
    Zeilen pro Request, das Limit die Requests pro Minute und IP. Die
    Verdrahtung ist mehrteilig (``ratelimit``-Decorator, der IP-Resolver
    ``client_ip_for_ratelimit`` aus ``RATELIMIT_IP_META_KEY``, und
    ``handler403``, der ``Ratelimited`` auf 429 mappt) — faellt ein Teil aus,
    antwortet der Endpoint still weiter mit 204.

    Test- und E2E-Settings setzen ``RATELIMIT_ENABLE = False``; die Fixture
    ``ratelimit_enabled`` (conftest) schaltet es an, leert den Cache und
    nagelt das Zaehlfenster fest — ohne das koennte die Minutengrenze mitten
    in die Schleife fallen (Refs #1900).
    """

    REPORT = {"csp-report": {"blocked-uri": "https://evil.example.com/x.js"}}

    def _post(self, client, csp_url):
        return client.post(
            csp_url,
            data=json.dumps(self.REPORT),
            content_type="application/csp-report",
        )

    def test_elfter_report_pro_minute_wird_mit_429_abgewiesen(self, client, csp_url, ratelimit_enabled, caplog):
        with caplog.at_level(logging.WARNING, logger="security.csp"):
            for _ in range(10):
                assert self._post(client, csp_url).status_code == 204
            assert self._post(client, csp_url).status_code == 429
        # Der abgewiesene Request darf auch keine Log-Zeile mehr erzeugen —
        # sonst waere das Limit als Flooding-Bremse wirkungslos.
        assert len([r for r in caplog.records if r.message == "csp_violation"]) == 10

    def test_neues_zaehlfenster_laesst_wieder_durch(self, client, csp_url, ratelimit_enabled):
        """Gegenprobe: Das Limit sperrt zeitlich begrenzt, nicht dauerhaft."""
        for _ in range(10):
            assert self._post(client, csp_url).status_code == 204
        assert self._post(client, csp_url).status_code == 429
        ratelimit_enabled.advance(60)
        assert self._post(client, csp_url).status_code == 204

    @override_settings(TRUSTED_PROXY_HOPS=1)
    def test_limit_zaehlt_pro_client_ip_nicht_global(self, client, csp_url, ratelimit_enabled):
        """``RATELIMIT_IP_META_KEY`` → ``client_ip_for_ratelimit``: hinter dem
        Reverse-Proxy traegt jeder Request dieselbe ``REMOTE_ADDR``. Ohne den
        Resolver waere das EIN gemeinsamer Eimer fuers ganze Internet — eine
        einzelne Quelle koennte alle anderen Melder aussperren.
        """
        for _ in range(10):
            assert self._post_forwarded(client, csp_url, "203.0.113.10").status_code == 204
        assert self._post_forwarded(client, csp_url, "203.0.113.10").status_code == 429
        assert self._post_forwarded(client, csp_url, "203.0.113.99").status_code == 204

    def _post_forwarded(self, client, csp_url, ip):
        return client.post(
            csp_url,
            data=json.dumps(self.REPORT),
            content_type="application/csp-report",
            HTTP_X_FORWARDED_FOR=f"198.51.100.1, {ip}",
        )
