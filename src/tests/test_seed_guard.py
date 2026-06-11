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

from core.models import DeletionRequest, Organization
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
