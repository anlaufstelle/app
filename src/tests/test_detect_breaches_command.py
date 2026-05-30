"""Tests for the ``detect_breaches`` management command (Refs #922 / #925).

CLI-Wrapper-Smoke fuer den Breach-Detection-Cron. Die Heuristiken selbst
(``detect_failed_login_burst``, ``detect_mass_export``, ``detect_mass_delete``)
sind in ``test_breach_detection.py`` abgedeckt. Hier nur:

* Smoke: laeuft ohne Findings ohne Crash durch
* ``--facility=<name>`` filtert auf die Facility und ruft ``run_all_detections``
  genau einmal mit dem Objekt auf
* Unbekannter Facility-Name: stderr-Meldung, kein Crash
* Ohne ``--facility``: Iteration ueber ALLE Facilities

Patch-Ziel: ``core.management.commands.detect_breaches.run_all_detections``
— der Command importiert den Service via ``from ... import ...`` auf Modul-Ebene,
deshalb wird das *Symbol im Command-Modul* gepatcht (nicht das Service-Modul).
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command


@pytest.mark.django_db
class TestDetectBreachesCommand:
    """Refs #925: CLI-Wrapper-Smoke fuer ``manage.py detect_breaches``."""

    PATCH_TARGET = "core.management.commands.detect_breaches.run_all_detections"

    def test_smoke_runs_without_findings(self, facility):
        """Frische Test-DB ohne Audit-Patterns: Command meldet 'Keine neuen Breach-Findings.'

        Hinweis: Sollten die Heuristiken auf der frischen DB doch ein Finding
        liefern, ist das OK — der Test verifiziert nur das Smoke-Verhalten
        und das Output-Format einer der beiden Endmeldungen.
        """
        out = StringIO()
        call_command("detect_breaches", stdout=out)
        output = out.getvalue()
        # Eine der beiden Endmeldungen muss im Output stehen.
        assert "Keine neuen Breach-Findings." in output or "Breach-Finding(s)" in output, (
            f"Erwartet Erfolgs- oder Warning-Meldung im stdout, erhalten: {output!r}"
        )

    def test_facility_filter_calls_service_with_matching_facility(self, facility):
        """``--facility=<name>`` ruft ``run_all_detections`` einmal mit dem Facility-Objekt auf."""
        with patch(self.PATCH_TARGET, return_value=[]) as mock_run:
            call_command("detect_breaches", f"--facility={facility.name}", stdout=StringIO())

        mock_run.assert_called_once()
        called_facility = mock_run.call_args.args[0]
        assert called_facility.pk == facility.pk, (
            f"Erwartet Aufruf mit facility.pk={facility.pk}, erhalten: {called_facility.pk}"
        )

    def test_unknown_facility_writes_to_stderr_without_crash(self, facility):
        """Unbekannter Facility-Name: stderr-Meldung, kein Raise, kein Service-Call.

        Der Command soll defensiv reagieren — kein Crash, dafuer eine klare
        Fehlermeldung auf stderr. Wir verifizieren zusaetzlich, dass der
        Service in diesem Zweig NICHT aufgerufen wird.
        """
        out = StringIO()
        err = StringIO()
        with patch(self.PATCH_TARGET, return_value=[]) as mock_run:
            call_command(
                "detect_breaches",
                "--facility=NichtExistent",
                stdout=out,
                stderr=err,
            )

        assert "Facility 'NichtExistent' not found." in err.getvalue(), (
            f"Erwartet 'Facility 'NichtExistent' not found.' in stderr, erhalten: {err.getvalue()!r}"
        )
        mock_run.assert_not_called()

    def test_iterates_over_all_facilities_without_filter(self, facility, second_facility):
        """Ohne ``--facility``: ``run_all_detections`` wird je Facility einmal aufgerufen."""
        with patch(self.PATCH_TARGET, return_value=[]) as mock_run:
            call_command("detect_breaches", stdout=StringIO())

        assert mock_run.call_count == 2, (
            f"Erwartet 2 Aufrufe (Teststelle + Zweite Stelle), erhalten: {mock_run.call_count}"
        )
        # Beide Facilities sollten unter den aufgerufenen Objekten sein.
        called_pks = {call.args[0].pk for call in mock_run.call_args_list}
        assert called_pks == {facility.pk, second_facility.pk}, (
            f"Erwartet Aufrufe fuer beide Facilities, erhalten pks: {called_pks}"
        )
