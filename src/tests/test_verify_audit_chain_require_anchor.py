"""Tests fuer die Anker-Pflicht von ``verify_audit_chain`` (Refs #2242).

Ausgangslage: ein fehlender externer High-Water-Mark gilt dem Kommando als
**Erstlauf** — die aktuell vorhandene Kette wird dann ohne Vergleich mit dem
gesicherten Original neu verankert. Nach einem fehlgeschlagenen HWM-Import auf
einem wiederhergestellten Host kann so ein abgeschnittener, in sich konsistenter
Audit-Bestand gruen neu verankert werden.

Gedeckt wird deshalb:

- Mit ``--require-anchor`` bricht das Kommando ab, wenn kein Anker vorliegt
  (Pfad nicht gesetzt / Datei fehlt / Datei ohne verankerten Tail) — und zwar
  OHNE einen neuen Anker und OHNE den ``AUDIT_CHAIN_VERIFIED``-Marker zu
  schreiben.
- Mit ``--require-anchor`` und vorhandenem Anker laeuft der normale Pfad
  unveraendert (Marker + monoton vorgeruckter Anker).
- OHNE den Schalter bleibt die Erstlauf-Behandlung byte-gleich (Default-Pin) —
  der Schalter ist reine Opt-in-Haertung, kein geaenderter Default.
- Ein Anker fuer nur *eine* Kette genuegt: eine neue Facility ohne eigenen
  Anker ist kein Abbruchgrund (kein False Positive fuer legitimes Wachstum).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.management.commands.verify_audit_chain import Command as VerifyAuditChainCommand
from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit.hwm import HighWaterMarkStore

# Eindeutiger Anteil der Abbruchmeldung: trennt den erwarteten Abbruch von einem
# argparse-„unrecognized arguments"-Fehler, der ebenfalls ein CommandError waere.
_ABORT = "verlangt einen externen Audit-Anker"


def _marker_count() -> int:
    return AuditLog.objects.filter(action=AuditLog.Action.AUDIT_CHAIN_VERIFIED).count()


@pytest.mark.django_db
class TestRequireAnchorAborts:
    """Fehlender Anker + Schalter ⇒ Abbruch statt stiller Neuverankerung."""

    def test_missing_hwm_file_aborts_without_anchoring(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        markers_before = _marker_count()

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")

        assert not os.path.exists(settings.AUDIT_HWM_PATH), "Abbruch darf keinen neuen Anker anlegen"
        assert _marker_count() == markers_before, "Abbruch darf keinen AUDIT_CHAIN_VERIFIED-Marker schreiben"

    def test_anchorless_hwm_file_aborts_and_stays_byte_identical(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        HighWaterMarkStore(path=settings.AUDIT_HWM_PATH).save()  # signiert, aber ohne Kette
        before = Path(settings.AUDIT_HWM_PATH).read_bytes()
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        markers_before = _marker_count()

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")

        assert Path(settings.AUDIT_HWM_PATH).read_bytes() == before
        assert _marker_count() == markers_before

    def test_unset_hwm_path_aborts(self, settings, facility, staff_user):
        settings.AUDIT_HWM_PATH = ""
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        markers_before = _marker_count()

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")

        assert _marker_count() == markers_before


@pytest.mark.django_db
class TestRequireAnchorGreenPaths:
    """Mit Anker bzw. ohne Schalter bleibt alles beim Alten."""

    def test_existing_anchor_runs_through_and_advances(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        call_command("verify_audit_chain")  # verankert (Erstlauf)
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)

        tail = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        tail.refresh_from_db()
        markers_before = _marker_count()

        call_command("verify_audit_chain", "--require-anchor")

        recorded = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)
        assert recorded["last_entry_hash"] == tail.entry_hash
        assert _marker_count() == markers_before + 1

    def test_new_chain_without_own_anchor_is_no_abort(
        self, settings, tmp_path, facility, staff_user, second_facility, second_facility_user
    ):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        call_command("verify_audit_chain")  # verankert nur die bestehenden Ketten

        fresh = audit_event(AuditLog.Action.LOGIN, user=second_facility_user, facility=second_facility)
        fresh.refresh_from_db()

        call_command("verify_audit_chain", "--require-anchor")

        recorded = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(second_facility.pk)
        assert recorded["last_entry_hash"] == fresh.entry_hash

    def test_default_first_run_still_anchors_silently(self, settings, tmp_path, facility, staff_user):
        """Default-Pin: ohne Schalter bleibt der Erstlauf-Pfad unveraendert."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        tail = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        tail.refresh_from_db()
        markers_before = _marker_count()

        call_command("verify_audit_chain")

        recorded = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)
        assert recorded["last_entry_hash"] == tail.entry_hash
        assert _marker_count() == markers_before + 1

    def test_default_without_hwm_path_stays_green(self, settings, facility, staff_user):
        """Default-Pin: ohne Anker-Feature laeuft das Kommando wie bisher durch."""
        settings.AUDIT_HWM_PATH = ""
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        markers_before = _marker_count()

        call_command("verify_audit_chain")

        assert _marker_count() == markers_before + 1


class TestRequireAnchorOption:
    def test_option_is_opt_in(self):
        """Default-Pin auf der Kommandozeile: ohne Angabe bleibt der Schalter aus."""
        parser = VerifyAuditChainCommand().create_parser("manage.py", "verify_audit_chain")

        assert parser.parse_args([]).require_anchor is False
        assert parser.parse_args(["--require-anchor"]).require_anchor is True
