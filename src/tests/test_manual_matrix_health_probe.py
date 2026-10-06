"""Guards for the Health cases ENT-SYS-01/-02 in matrix B (Refs #2476).

The status values these cases expect depend on preconditions the code does
not guarantee: without ``CLAMAV_ENABLED`` the scanner reports ``disabled``
even when it runs, a runtime role with BYPASSRLS keeps the anonymous status
``degraded``, and the detail status also degrades on cache, SMTP, backup, disk
or job problems. The cases therefore name their preconditions, record a
baseline and phrase recovery as a return to it; the behaviour pins replay the
counterexamples.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client as HttpClient

from tests._matrix_text import MATRIX_B, case_section, matrix_case, non_empty_lines

# Reads files under docs/ — not available inside the mutation sandbox.
pytestmark = pytest.mark.architecture

HEALTH_LIVENESS_CASE = "### TC-ID: ENT-SYS-01 "
HEALTH_CLAMAV_CASE = "### TC-ID: ENT-SYS-02 "

HEALTH_TOKEN = "matrix-probe-health-token"  # noqa: S105 - test fixture, not a secret


class TestHealthCasesNamePreconditions:
    @pytest.mark.parametrize("needle", ["`CLAMAV_ENABLED=True`", "BYPASSRLS", "nicht geprüft"])
    def test_liveness_case_names_preconditions(self, needle):
        assert needle in matrix_case(MATRIX_B, HEALTH_LIVENESS_CASE)

    def test_clamav_case_records_a_baseline(self):
        prep = case_section(matrix_case(MATRIX_B, HEALTH_CLAMAV_CASE), "Vorbereitung")
        assert "Ausgangszustand" in prep
        assert "`CLAMAV_ENABLED=True`" in prep

    def test_recovery_returns_to_baseline_instead_of_promising_ok(self):
        expected = case_section(matrix_case(MATRIX_B, HEALTH_CLAMAV_CASE), "Erwartetes Ergebnis")
        recovery = [line for line in non_empty_lines(expected) if line.startswith("- Schritt 4")]
        assert recovery and "Ausgangszustand" in recovery[0]
        assert 'wieder `status: "ok"`' not in expected


@pytest.mark.django_db
class TestHealthPreconditionBehaviour:
    @pytest.fixture(autouse=True)
    def _isolated(self, settings, tmp_path):
        settings.HEALTH_DETAIL_TOKEN = HEALTH_TOKEN
        settings.MEDIA_ROOT = tmp_path
        cache.clear()
        yield
        cache.clear()

    def _get(self, detail: bool) -> dict:
        headers = {"HTTP_X_HEALTH_TOKEN": HEALTH_TOKEN} if detail else {}
        return HttpClient().get("/health/", **headers).json()

    def test_running_scanner_reports_disabled_without_the_switch(self, settings):
        settings.CLAMAV_ENABLED = False
        with (
            patch("core.views.health.clamav_ping", return_value=True),
            patch("core.views.health._query_runtime_role_posture", return_value=(False, False)),
        ):
            data = self._get(detail=True)
        assert (data["virus_scanner"], data["clamav"]) == ("disabled", "disabled")

    def test_rls_bypass_keeps_anonymous_status_degraded_with_scanner_up(self, settings):
        settings.CLAMAV_ENABLED = True
        with (
            patch("core.views.health.clamav_ping", return_value=True),
            patch("core.views.health._query_runtime_role_posture", return_value=(True, True)),
        ):
            assert self._get(detail=False) == {"status": "degraded"}

    def test_recovered_scanner_with_missing_backup_stays_degraded_in_detail(self, settings, tmp_path):
        settings.CLAMAV_ENABLED = True
        settings.BACKUP_DIR = tmp_path
        settings.HEALTH_BACKUP_REQUIRED = True
        with (
            patch("core.views.health.clamav_ping", return_value=True),
            patch("core.views.health._query_runtime_role_posture", return_value=(False, False)),
        ):
            anonymous = self._get(detail=False)
            detail = self._get(detail=True)
        assert anonymous == {"status": "ok"}
        assert detail["clamav"] == "ok"
        assert detail["backup"] == "missing"
        assert detail["status"] == "degraded"
