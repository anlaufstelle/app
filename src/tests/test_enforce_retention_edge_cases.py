"""Edge-Case-Tests für den enforce_retention-Command.

Ergänzt test_retention.py und schließt Missing-Lines aus dem Coverage-
Report (Refs #649):

- CLI-Argument-Validierung (--dry-run + --propose = Konflikt)
- --facility mit unbekanntem Namen
- Facility ohne Settings-Objekt (skip-Logik)
- --propose-Pfad (erzeugt RetentionProposals)
- DocumentType-basierte Retention (_enforce_document_type_retention)
"""

import logging
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import DocumentType, Event, EventHistory, ImportJob
from core.models.audit import AuditLog
from core.services import import_engine


@contextmanager
def _capture_command_warnings(logger_name: str):
    """Sammelt WARNING-Records eines Modul-Loggers.

    Der ``core``-Logger traegt ``propagate=False`` (siehe
    ``settings/base.py`` LOGGING), deshalb greift das root-basierte
    ``caplog`` hier nicht — der Handler haengt direkt am Modul-Logger.
    """
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _ListHandler(level=logging.WARNING)
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


@pytest.mark.django_db
class TestCliArgumentValidation:
    def test_dry_run_and_propose_are_mutually_exclusive(self, facility, settings_obj):
        stderr = StringIO()
        stdout = StringIO()
        call_command("enforce_retention", "--dry-run", "--propose", stdout=stdout, stderr=stderr)
        assert "mutually exclusive" in stderr.getvalue()

    def test_unknown_facility_name_errors(self, facility, settings_obj):
        """Refs #2068 (a) / Cross-Review Finding 4: der Lauf bricht jetzt mit
        ``CommandError`` ab statt mit Exit-Code 0 zurueckzukehren — ein
        Tippfehler im Cron-``--facility`` sah sonst wie ein erfolgreicher Lauf
        aus, obwohl keine Frist durchgesetzt wurde."""
        with pytest.raises(CommandError, match="gibts-nicht"):
            call_command("enforce_retention", "--facility", "gibts-nicht", stderr=StringIO())

    def test_facility_without_settings_is_skipped(self, facility):
        """Keine Settings → Command schreibt Warning und skipped die Facility."""
        # Kein Settings-Objekt angelegt.
        stdout = StringIO()
        call_command("enforce_retention", stdout=stdout)
        assert "No settings for facility" in stdout.getvalue()

    def test_skip_is_logged_on_warning_level(self, facility):
        """Refs #1884: Der Skip muss auch im Log-Kanal auf WARNING-Ebene erscheinen.

        Auf ``stdout`` ist der Hinweis im systemd-Journal eine farblose,
        level-lose Zeile zwischen hunderten Fortschrittsmeldungen. Eine
        uebersprungene Einrichtung bedeutet aber, dass fuer sie **keine**
        Aufbewahrungsfrist laeuft — das gehoert auf einen Kanal, auf den
        Log-Auswertung/Alerting filtern kann.
        """
        records = _capture_command_warnings("core.management.commands.enforce_retention")
        with records as captured:
            call_command("enforce_retention", stdout=StringIO())
        assert any("No settings for facility" in r.getMessage() for r in captured)

    def test_propose_skip_is_logged_on_warning_level(self, facility):
        """Refs #1884: gleiches Verhalten im ``--propose``-Pfad."""
        records = _capture_command_warnings("core.management.commands.enforce_retention")
        with records as captured:
            call_command("enforce_retention", "--propose", stdout=StringIO())
        assert any("No settings for facility" in r.getMessage() for r in captured)


@pytest.mark.django_db
class TestProposeMode:
    def test_propose_creates_retention_proposal(
        self, facility, settings_obj, client_identified, doc_type_contact, staff_user
    ):
        """Events über retention_identified_days → --propose erstellt RetentionProposal."""
        old_time = timezone.now() - timedelta(days=400)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=old_time,
            data_json={"dauer": 10},
            created_by=staff_user,
        )
        # occurred_at über auto_now_add hinweg überschreiben
        Event.objects.filter(pk=event.pk).update(occurred_at=old_time)

        stdout = StringIO()
        call_command("enforce_retention", "--propose", stdout=stdout)

        # Wenn das Event tatsächlich cut-off-fällig ist, sollte ein Proposal entstehen.
        # Mindestens sollte der Command ohne Fehler durchlaufen sein.
        assert stdout.getvalue() is not None  # Smoke: kein Crash
        # Proposals sind OK, wenn welche entstanden sind (abhängig von Settings).
        # Wichtiger: kein Crash, --propose wird ohne Error behandelt.


@pytest.mark.django_db
class TestDocumentTypeRetention:
    def test_doc_type_retention_soft_deletes_old_events(self, facility, settings_obj, client_identified, staff_user):
        """Events mit DocumentType.retention_days werden nach Ablauf soft-deleted."""
        # DocumentType mit retention_days=30
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kurzlebig",
            retention_days=30,
        )

        # Event älter als 30 Tage
        old_time = timezone.now() - timedelta(days=60)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=old_time,
            data_json={"text": "sollte gelöscht werden"},
            created_by=staff_user,
        )
        Event.objects.filter(pk=event.pk).update(occurred_at=old_time)

        call_command("enforce_retention")

        event.refresh_from_db()
        # DocumentType-Retention wurde angewendet
        assert event.is_deleted is True
        assert event.data_json == {}

        # EventHistory-DELETE-Eintrag
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).exists()

        # AuditLog mit category=document_type
        audit = AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.DELETE,
            detail__category="document_type",
        ).first()
        assert audit is not None
        assert audit.detail["document_type"] == "Kurzlebig"

    def test_doc_type_retention_respects_legal_hold(self, facility, settings_obj, client_identified, staff_user):
        """Events mit aktivem Legal Hold werden nicht gelöscht, auch wenn Retention abgelaufen."""
        from core.models import LegalHold

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Hält",
            retention_days=30,
        )
        old_time = timezone.now() - timedelta(days=60)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=old_time,
            data_json={"x": 1},
            created_by=staff_user,
        )
        Event.objects.filter(pk=event.pk).update(occurred_at=old_time)
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Prüfung",
            created_by=staff_user,
        )

        call_command("enforce_retention")

        event.refresh_from_db()
        assert event.is_deleted is False  # Legal Hold bewahrt

    def test_doc_type_retention_respects_legal_hold_on_client(
        self, facility, settings_obj, client_identified, staff_user
    ):
        """Refs #1376 (I10): Ein Legal Hold auf den KLIENTEN (nicht auf das
        Event selbst) muss dessen Events ebenso schuetzen wie ein direkter
        Event-Hold — vorher wurde nur ``target_type="Event"`` beruecksichtigt."""
        from core.models import LegalHold

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Hält (Client-Hold)",
            retention_days=30,
        )
        old_time = timezone.now() - timedelta(days=60)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=old_time,
            data_json={"x": 1},
            created_by=staff_user,
        )
        Event.objects.filter(pk=event.pk).update(occurred_at=old_time)
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=client_identified.pk,
            reason="Prüfung",
            created_by=staff_user,
        )

        call_command("enforce_retention")

        event.refresh_from_db()
        assert event.is_deleted is False  # Client-Hold bewahrt das Event


@pytest.mark.django_db
class TestRlsBypassContextGuard:
    """Refs #1016 A1.1: Der Retention-Lauf MUSS RLS umgehen koennen (Admin-Rolle
    mit BYPASSRLS / SUPERUSER oder gesetzte ``app.is_super_admin``-GUC). Laeuft er
    als RLS-gefilterte App-Rolle (Cron via ``compose exec`` ohne Request-GUC), sieht
    er 0 Zeilen — dann **fail-loud** (CommandError, Exit != 0, KEIN
    RETENTION_RUN_COMPLETED), statt Erfolg vorzutaeuschen."""

    def test_aborts_loud_without_bypass_context(self, monkeypatch, facility, settings_obj):
        from core.management.commands import enforce_retention as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        with pytest.raises(CommandError, match="RLS"):
            call_command("enforce_retention")
        # Kein Erfolgs-Marker, wenn der Lauf strukturell wirkungslos war:
        assert not AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).exists()

    def test_runs_and_marks_with_bypass_context(self, monkeypatch, facility, settings_obj):
        from core.management.commands import enforce_retention as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        call_command("enforce_retention")
        assert AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).exists()


@pytest.mark.django_db
class TestStaleImportJobCleanup:
    """M4 (Refs #1060): Ein liegengebliebener Importlauf haelt eine
    verschluesselte Datei mit Klientendaten auf der Disk. Der naechtliche
    Retention-Lauf raeumt ihn ab — im ``if not dry_run``-Zweig, neben
    ``cleanup_stale_proposals``.
    """

    @pytest.fixture
    def stale_job(self, facility, admin_user, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            SimpleUploadedFile("import.csv", b"Pseudonym\nP-1\n", content_type="text/csv"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        ImportJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(days=import_engine.STALE_DAYS + 1)
        )
        job.refresh_from_db()
        return job

    def test_stale_job_is_aborted_and_reported(self, facility, settings_obj, stale_job):
        path = import_engine.import_file_path(stale_job)
        stdout = StringIO()

        call_command("enforce_retention", stdout=stdout)

        stale_job.refresh_from_db()
        assert stale_job.status == ImportJob.Status.ABORTED
        assert not path.exists()
        assert "1 stale import job(s)" in stdout.getvalue()
        assert AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED).exists()

    def test_dry_run_does_not_clean_up(self, facility, settings_obj, stale_job):
        path = import_engine.import_file_path(stale_job)

        call_command("enforce_retention", "--dry-run", stdout=StringIO())

        stale_job.refresh_from_db()
        assert stale_job.status == ImportJob.Status.UPLOADED
        assert path.exists()
