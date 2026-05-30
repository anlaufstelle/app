"""Coverage-Tests fuer ``core.management.commands.refresh_statistics_view``.

Deckt die Branches:

* PostgreSQL-CONCURRENTLY-Refresh (Happy Path).
* ``--no-concurrent``-Branch (Blocking Refresh).
* Non-Postgres-Skip (Warning + Return).
* CONCURRENTLY-Exception -> Fallback zu non-concurrent + Logger-Warning.
* Non-concurrent-Exception -> ``CommandError``.

Refs #922 (Coverage-Lift).
"""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


class TestRefreshStatisticsView:
    @pytest.mark.django_db
    def test_refresh_concurrent_runs_against_postgres(self, facility, doc_type_contact):
        """Happy-Path: CONCURRENTLY-Refresh laeuft erfolgreich.

        Vor CONCURRENTLY muss die MV initial befuellt werden — sonst kann
        Postgres bei einer leeren MV ohne Snapshot ggf. mit Fehlern reagieren.
        """
        # Initial-Refresh ohne CONCURRENTLY, damit ein Snapshot existiert.
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("REFRESH MATERIALIZED VIEW core_statistics_event_flat")

        out = StringIO()
        call_command("refresh_statistics_view", stdout=out)
        assert "Refreshed materialized view" in out.getvalue()

    @pytest.mark.django_db
    def test_no_concurrent_flag_runs_blocking_refresh(self, facility, doc_type_contact):
        """``--no-concurrent``: Blocking Refresh ohne CONCURRENTLY-Branch."""
        out = StringIO()
        call_command("refresh_statistics_view", "--no-concurrent", stdout=out)
        assert "Refreshed materialized view" in out.getvalue()

    @pytest.mark.django_db
    def test_writes_marker(self, facility, doc_type_contact):
        """Refs #794: nach erfolgreichem Refresh entsteht ein MV_REFRESH_COMPLETED-Marker."""
        from core.models import AuditLog

        AuditLog.objects.filter(action=AuditLog.Action.MV_REFRESH_COMPLETED).delete()
        call_command("refresh_statistics_view", "--no-concurrent")
        markers = AuditLog.objects.filter(action=AuditLog.Action.MV_REFRESH_COMPLETED)
        assert markers.count() == 1
        assert markers.first().facility_id is None

    def test_non_postgres_backend_emits_warning_and_returns(self):
        """Lines 35-38: non-PG-Backend ueberspringt MV-Refresh mit Warning."""
        out = StringIO()
        with patch("core.management.commands.refresh_statistics_view.connection") as conn:
            conn.vendor = "sqlite"
            call_command("refresh_statistics_view", stdout=out)
        assert "nicht PostgreSQL" in out.getvalue()

    @pytest.mark.django_db
    def test_concurrently_exception_falls_back_to_blocking(self, caplog):
        """Lines 50-59: CONCURRENTLY-Refresh wirft -> Fallback ohne CONCURRENTLY.

        Der ``core``-Logger hat ``propagate=False`` (settings/base.py), deshalb
        haengen wir den ``caplog``-Handler explizit an den Modul-Logger.

        ``django_db`` noetig, seit der Command nach erfolgreichem (Fallback-)
        Refresh einen MV_REFRESH_COMPLETED-Marker schreibt (Refs #794).
        """
        import logging

        mod_logger = logging.getLogger("core.management.commands.refresh_statistics_view")

        out = StringIO()
        bad_cursor = MagicMock()
        bad_cursor.__enter__.return_value.execute.side_effect = Exception("missing unique idx")
        good_cursor = MagicMock()
        good_cursor.__enter__.return_value.execute.return_value = None

        with patch("core.management.commands.refresh_statistics_view.connection") as conn:
            conn.vendor = "postgresql"
            conn.cursor.side_effect = [bad_cursor, good_cursor]

            caplog.set_level(logging.WARNING)
            mod_logger.addHandler(caplog.handler)
            try:
                call_command("refresh_statistics_view", stdout=out)
            finally:
                mod_logger.removeHandler(caplog.handler)

        assert "Refreshed materialized view" in out.getvalue()
        assert any("CONCURRENTLY" in r.getMessage() for r in caplog.records)

    def test_no_concurrent_exception_raises_command_error(self):
        """Line 52: Bei ``--no-concurrent`` darf kein Fallback greifen ->
        Exception wird zu ``CommandError`` re-raised."""
        bad_cursor = MagicMock()
        bad_cursor.__enter__.return_value.execute.side_effect = Exception("broken")
        with patch("core.management.commands.refresh_statistics_view.connection") as conn:
            conn.vendor = "postgresql"
            conn.cursor.return_value = bad_cursor
            with pytest.raises(CommandError):
                call_command("refresh_statistics_view", "--no-concurrent")
