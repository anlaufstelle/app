"""Tests fuer den CSP-Violation-Report-Endpoint (Refs #684, Refs #733)."""

import json
import logging

import pytest
from django.urls import reverse


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
