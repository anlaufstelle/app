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
from django.core.management.base import CommandError


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


@pytest.mark.django_db
class TestRlsBypassContextGuard:
    """Refs #1554 / #1016 A1.1: Der Orphan-Cleanup gleicht die ``.enc``-Dateien
    gegen die aktuell registrierten ``EventAttachment``-``storage_filename`` ab.
    Als RLS-gefilterte App-Rolle ohne Request-GUC sieht der Lauf 0 Zeilen, haelt
    also ALLE Dateien fuer Orphans und wuerde jede ``.enc``-Datei > min-age
    loeschen. Deshalb **fail-loud** (CommandError, Exit != 0), statt destruktiv
    weiterzulaufen. Der Cron MUSS als Rolle mit BYPASSRLS (Admin) laufen."""

    PATCH_TARGET = "core.management.commands.cleanup_orphan_storage_files.cleanup_orphan_storage_files"

    def test_aborts_loud_without_bypass_context(self, monkeypatch):
        from core.management.commands import cleanup_orphan_storage_files as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        with patch(self.PATCH_TARGET) as mock_cleanup:
            with pytest.raises(CommandError, match="RLS"):
                call_command("cleanup_orphan_storage_files", stdout=StringIO())
        # Kein destruktiver Loesch-Lauf, wenn der Kontext strukturell blind waere:
        mock_cleanup.assert_not_called()

    def test_runs_with_bypass_context(self, monkeypatch):
        from core.management.commands import cleanup_orphan_storage_files as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        with patch(self.PATCH_TARGET, return_value=0) as mock_cleanup:
            call_command("cleanup_orphan_storage_files", stdout=StringIO())
        mock_cleanup.assert_called_once_with(min_age_seconds=3600)
