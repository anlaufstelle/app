"""Tests for the ``mark_restore_verified`` management command (Refs #919)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from core.models import AuditLog


@pytest.mark.django_db
class TestMarkRestoreVerified:
    def test_writes_auditlog_entry(self):
        before = AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).count()
        call_command("mark_restore_verified")
        after = AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).count()
        assert after == before + 1

    def test_facility_is_null_system_event(self):
        call_command("mark_restore_verified")
        entry = AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).latest("timestamp")
        assert entry.facility is None
        assert entry.user is None
        assert entry.target_type == "RestoreVerification"

    def test_note_in_detail(self):
        call_command("mark_restore_verified", "--note", "Test-Restore vom 2026-05-16")
        entry = AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).latest("timestamp")
        assert entry.detail == {"note": "Test-Restore vom 2026-05-16"}

    def test_empty_note_omits_field(self):
        call_command("mark_restore_verified")
        entry = AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).latest("timestamp")
        assert entry.detail == {}

    def test_stdout_confirms_success(self):
        out = StringIO()
        call_command("mark_restore_verified", "--note", "Smoke", stdout=out)
        output = out.getvalue()
        assert "RESTORE_VERIFIED-Eintrag geschrieben" in output

    def test_warning_when_no_note(self):
        out = StringIO()
        err = StringIO()
        call_command("mark_restore_verified", stdout=out, stderr=err)
        output = out.getvalue()
        # Warnung wird ueber stdout/style.WARNING geschrieben (Django Standard).
        assert "Kein --note angegeben" in output
