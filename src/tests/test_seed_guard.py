"""Refs #1040 (S1): Umgebungs-Guard + atomarer Flush für das seed-Command.

``manage.py seed`` legt Demo-Logins mit dokumentiertem Passwort an und
löscht mit ``--flush`` den kompletten Datenbestand — auf einem
Produktionssystem wäre beides fatal. Der Guard sperrt das Command
fail-closed über ``SEED_ALLOWED`` (nur dev/test/e2e/devlive setzen True).
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.conf import settings as dj_settings
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import DeletionRequest, Organization, User
from core.seed.flush import flush_seed_data


@pytest.mark.django_db
class TestSeedEnvironmentGuard:
    def test_seed_blocked_without_seed_allowed(self, settings):
        settings.SEED_ALLOWED = False
        with pytest.raises(CommandError, match="SEED_ALLOWED"):
            call_command("seed")

    def test_seed_blocked_when_setting_missing(self, settings):
        # Fail-closed: fehlendes Setting == gesperrt (getattr-Default False).
        del settings.SEED_ALLOWED
        with pytest.raises(CommandError, match="SEED_ALLOWED"):
            call_command("seed")

    def test_test_settings_allow_seed(self):
        # Umgebungsmatrix: test.py erbt von dev.py — Seed bleibt für
        # lokale Entwicklung, Unit- und E2E-Tests erlaubt.
        assert dj_settings.SEED_ALLOWED is True


@pytest.mark.django_db
class TestSeedOnlyIfEmpty:
    """Refs #1062: ``--only-if-empty`` traegt die Idempotenz des
    One-Command-Local-Bootstraps.

    Der Init-Service des lokalen ``docker-compose.yml`` laeuft bei JEDEM
    ``docker compose up``. Ohne diese Entscheidung im Command muesste sie in
    einem Shell-Skript stehen — ungetestet und ausserhalb der Reichweite der
    Suite. „Leer" heisst: es existiert noch kein einziges Benutzerkonto.
    ``core_user`` steht bewusst nicht unter RLS (Migration 0047), die Probe
    sieht also auch ohne Bypass-Kontext alle Zeilen.
    """

    def test_seeds_when_no_user_exists(self):
        assert not User.objects.exists()
        with patch("core.management.commands.seed.seed_organization") as seed_organization:
            seed_organization.side_effect = RuntimeError("seed-laeuft")
            with pytest.raises(RuntimeError, match="seed-laeuft"):
                call_command("seed", "--only-if-empty")

    def test_skips_when_a_user_exists(self, staff_user):
        with patch("core.management.commands.seed.seed_organization") as seed_organization:
            call_command("seed", "--only-if-empty")
        seed_organization.assert_not_called()

    def test_full_seed_still_runs_without_the_flag(self, staff_user):
        """Ohne Flag bleibt das Command unveraendert — der Schalter darf den
        bestehenden Aufrufweg (``make seed``, Demo-Reseed) nicht beruehren."""
        with patch("core.management.commands.seed.seed_organization") as seed_organization:
            seed_organization.side_effect = RuntimeError("seed-laeuft")
            with pytest.raises(RuntimeError, match="seed-laeuft"):
                call_command("seed")

    def test_flush_and_only_if_empty_are_mutually_exclusive(self):
        """``--flush`` loescht den Bestand, ``--only-if-empty`` schont ihn.
        Zusammen waere die Wirkung von der Auswertungsreihenfolge abhaengig."""
        with pytest.raises(CommandError, match="--flush"):
            call_command("seed", "--only-if-empty", "--flush")

    def test_environment_guard_wins_over_the_flag(self, settings):
        """Fail-closed bleibt fail-closed: ``--only-if-empty`` darf den
        ``SEED_ALLOWED``-Guard nicht in ein stilles „nichts tun" verwandeln."""
        settings.SEED_ALLOWED = False
        with pytest.raises(CommandError, match="SEED_ALLOWED"):
            call_command("seed", "--only-if-empty")


@pytest.mark.django_db
class TestFlushAtomicity:
    def test_flush_rolls_back_on_failure(self, facility, staff_user):
        """Schlägt ein später Schritt fehl, bleiben frühere Deletes nicht
        committed (Refs #1040 S1: vorher sequenziell ohne Transaktion)."""
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid4(),
            reason="Atomicity-Probe",
            requested_by=staff_user,
        )
        # Organization ist der letzte Delete-Schritt in flush_seed_data().
        with patch.object(Organization.objects, "all", side_effect=RuntimeError("flush-fail")):
            with pytest.raises(RuntimeError, match="flush-fail"):
                flush_seed_data()
        # Ohne Transaktion wäre der früh gelöschte DeletionRequest weg.
        assert DeletionRequest.objects.filter(pk=dr.pk).exists()
