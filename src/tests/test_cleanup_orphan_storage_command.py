"""Tests for the ``cleanup_orphan_storage_files`` management command (Refs #922 / #925).

CLI-Wrapper-Smoke fuer den Cleanup-Command — die Service-Logik selbst ist
in ``test_file_vault.py`` (``test_cleanup_orphan_storage_files_removes_unreferenced``,
Zeile 733) abgedeckt. Hier nur:

* Smoke: laeuft ohne Crash durch
* Optionen werden korrekt an den Service durchgereicht
* Default-Werte bleiben stabil
* stdout nennt Bilanz und Fortsetzung

Patch-Ziel: ``core.management.commands.cleanup_orphan_storage_files.cleanup_orphan_storage_files``
— der Command importiert den Service via ``from ... import ...`` auf Modul-Ebene,
deshalb wird das *Symbol im Command-Modul* gepatcht (nicht das Service-Modul).
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.services.file_vault import DEFAULT_ORPHAN_CLEANUP_BUDGET, OrphanCleanupResult


def _cleanup_result(
    *,
    checked_paths: tuple[str, ...] = (),
    deleted: int = 0,
    deletion_candidates: int = 0,
    skipped_referenced: int = 0,
    skipped_young: int = 0,
    errors: int = 0,
    continued_at: str | None = None,
    next_start: str | None = None,
    tree_complete: bool = True,
    state_warning: str | None = None,
    dry_run: bool = False,
) -> OrphanCleanupResult:
    return OrphanCleanupResult(
        checked_paths=checked_paths,
        deleted=deleted,
        deletion_candidates=deletion_candidates,
        skipped_referenced=skipped_referenced,
        skipped_young=skipped_young,
        errors=errors,
        continued_at=continued_at,
        next_start=next_start,
        tree_complete=tree_complete,
        state_warning=state_warning,
        dry_run=dry_run,
    )


@pytest.mark.django_db
class TestCleanupOrphanStorageCommand:
    """Refs #925: CLI-Wrapper-Smoke fuer ``manage.py cleanup_orphan_storage_files``."""

    PATCH_TARGET = "core.management.commands.cleanup_orphan_storage_files.cleanup_orphan_storage_files"

    def test_smoke_runs_without_orphans(self):
        """Ohne Orphans meldet der Command eine leere, vollstaendige Bilanz.

        Der Service selbst ist in ``test_file_vault.py`` (#733) abgedeckt; hier
        verifizieren wir nur den CLI-Wrapper. Service wird gemockt, weil das
        Media-Root des Dev-Hosts gelegentlich Leftover-``.enc``-Dateien aus
        frueheren Laeufen enthaelt — der Smoke-Test soll deterministisch sein.
        """
        out = StringIO()
        with patch(self.PATCH_TARGET, return_value=_cleanup_result()):
            call_command("cleanup_orphan_storage_files", stdout=out)
        assert "Geprüft: 0" in out.getvalue()
        assert "Baum vollständig durchlaufen" in out.getvalue()

    def test_min_age_seconds_arg_is_forwarded(self):
        """``--min-age-seconds=7200`` wird als Keyword-Arg an den Service durchgereicht."""
        with patch(self.PATCH_TARGET, return_value=_cleanup_result()) as mock_cleanup:
            call_command("cleanup_orphan_storage_files", "--min-age-seconds=7200", stdout=StringIO())

        mock_cleanup.assert_called_once_with(
            min_age_seconds=7200,
            budget=DEFAULT_ORPHAN_CLEANUP_BUDGET,
            dry_run=False,
        )

    def test_default_min_age_seconds_is_3600(self):
        """Ohne ``--min-age-seconds``-Arg uebernimmt der Command den Default 3600."""
        with patch(self.PATCH_TARGET, return_value=_cleanup_result()) as mock_cleanup:
            call_command("cleanup_orphan_storage_files", stdout=StringIO())

        mock_cleanup.assert_called_once_with(
            min_age_seconds=3600,
            budget=DEFAULT_ORPHAN_CLEANUP_BUDGET,
            dry_run=False,
        )

    def test_budget_and_dry_run_are_forwarded(self):
        """Budget und Trockenlauf erreichen den detaillierten Service-Vertrag."""
        with patch(self.PATCH_TARGET, return_value=_cleanup_result(dry_run=True)) as mock_cleanup:
            call_command("cleanup_orphan_storage_files", "--budget=23", "--dry-run", stdout=StringIO())

        mock_cleanup.assert_called_once_with(min_age_seconds=3600, budget=23, dry_run=True)

    def test_non_positive_budget_is_rejected_before_cleanup(self):
        with patch(self.PATCH_TARGET) as mock_cleanup:
            with pytest.raises(CommandError, match="mindestens 1"):
                call_command("cleanup_orphan_storage_files", "--budget=0", stdout=StringIO())

        mock_cleanup.assert_not_called()

    def test_stdout_reports_deletion_count_when_positive(self):
        """Die Ausgabe nennt Zaehler und beide Fortsetzungspfade."""
        out = StringIO()
        with patch(
            self.PATCH_TARGET,
            return_value=_cleanup_result(
                checked_paths=("a.enc", "b.enc"),
                deleted=1,
                deletion_candidates=1,
                skipped_referenced=1,
                continued_at="a.enc",
                next_start="c.enc",
                tree_complete=False,
            ),
        ):
            call_command("cleanup_orphan_storage_files", stdout=out)

        output = out.getvalue()
        assert "Geprüft: 2" in output
        assert "gelöscht: 1" in output
        assert "übersprungen (referenziert): 1" in output
        assert "Fortgesetzt bei: a.enc" in output
        assert "Nächster Start: c.enc" in output

    def test_dry_run_and_state_warning_are_reported(self):
        out = StringIO()
        with patch(
            self.PATCH_TARGET,
            return_value=_cleanup_result(
                checked_paths=("a.enc",),
                deletion_candidates=1,
                continued_at="a.enc",
                state_warning="Fortsetzungspunkt ist beschaedigt; Start am Anfang.",
                dry_run=True,
            ),
        ):
            call_command("cleanup_orphan_storage_files", "--dry-run", stdout=out)

        output = out.getvalue()
        assert "Hinweis: Fortsetzungspunkt ist beschaedigt; Start am Anfang." in output
        assert "Löschkandidaten: 1; gelöscht: 0" in output
        assert "Fortsetzungspunkt im Trockenlauf unverändert" in output
        assert "Trockenlauf: Es wurden keine Dateien gelöscht" in output


@pytest.mark.django_db
class TestRlsBypassContextGuard:
    """Refs #1554 / #1016 A1.1: Der Orphan-Cleanup gleicht die ``.enc``-Dateien
    gegen die aktuell registrierten ``Attachment``-``storage_filename`` ab.
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
        with patch(self.PATCH_TARGET, return_value=_cleanup_result()) as mock_cleanup:
            call_command("cleanup_orphan_storage_files", stdout=StringIO())
        mock_cleanup.assert_called_once_with(
            min_age_seconds=3600,
            budget=DEFAULT_ORPHAN_CLEANUP_BUDGET,
            dry_run=False,
        )
