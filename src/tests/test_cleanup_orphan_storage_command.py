"""Tests for the ``cleanup_orphan_storage_files`` management command (Refs #922 / #925).

CLI-Wrapper-Smoke fuer den Cleanup-Command — die Service-Logik selbst ist
in ``test_file_vault.py`` (``test_cleanup_orphan_storage_files_removes_unreferenced``,
Zeile 733) abgedeckt. Hier nur:

* Smoke: laeuft ohne Crash durch
* ``--min-age-seconds`` wird korrekt an den Service durchgereicht
* Default-Wert ist 3600
* stdout-Format bei n>0 enthaelt die Trefferzahl

Patch-Ziel: ``core.management.commands.cleanup_orphan_storage_files.cleanup_orphan_storage_files``
— der Command importiert den Service via ``from ... import ...`` auf Modul-Ebene,
deshalb wird das *Symbol im Command-Modul* gepatcht (nicht das Service-Modul).
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command


@pytest.mark.django_db
class TestCleanupOrphanStorageCommand:
    """Refs #925: CLI-Wrapper-Smoke fuer ``manage.py cleanup_orphan_storage_files``."""

    PATCH_TARGET = "core.management.commands.cleanup_orphan_storage_files.cleanup_orphan_storage_files"

    def test_smoke_runs_without_orphans(self):
        """Ohne Orphans (Service mocked auf 0): Command laeuft durch, stdout meldet 'Keine Orphans gefunden.'

        Der Service selbst ist in ``test_file_vault.py`` (#733) abgedeckt; hier
        verifizieren wir nur den CLI-Wrapper. Service wird gemockt, weil das
        Media-Root des Dev-Hosts gelegentlich Leftover-``.enc``-Dateien aus
        frueheren Laeufen enthaelt — der Smoke-Test soll deterministisch sein.
        """
        out = StringIO()
        with patch(self.PATCH_TARGET, return_value=0):
            call_command("cleanup_orphan_storage_files", stdout=out)
        assert "Keine Orphans gefunden." in out.getvalue(), (
            f"Erwartet 'Keine Orphans gefunden.' im stdout, erhalten: {out.getvalue()!r}"
        )

    def test_min_age_seconds_arg_is_forwarded(self):
        """``--min-age-seconds=7200`` wird als Keyword-Arg an den Service durchgereicht."""
        with patch(self.PATCH_TARGET, return_value=0) as mock_cleanup:
            call_command("cleanup_orphan_storage_files", "--min-age-seconds=7200", stdout=StringIO())

        mock_cleanup.assert_called_once_with(min_age_seconds=7200)

    def test_default_min_age_seconds_is_3600(self):
        """Ohne ``--min-age-seconds``-Arg uebernimmt der Command den Default 3600."""
        with patch(self.PATCH_TARGET, return_value=0) as mock_cleanup:
            call_command("cleanup_orphan_storage_files", stdout=StringIO())

        mock_cleanup.assert_called_once_with(min_age_seconds=3600)

    def test_stdout_reports_deletion_count_when_positive(self):
        """Wenn der Service n>0 zurueckgibt, druckt der Command 'Geloescht: n Orphan-Datei(en).'."""
        out = StringIO()
        with patch(self.PATCH_TARGET, return_value=5):
            call_command("cleanup_orphan_storage_files", stdout=out)

        output = out.getvalue()
        assert "5" in output, f"Erwartet '5' im stdout, erhalten: {output!r}"
        assert "Orphan-Datei" in output, f"Erwartet 'Orphan-Datei' im stdout, erhalten: {output!r}"
