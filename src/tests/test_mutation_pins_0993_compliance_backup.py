"""Pins fuer die Backup-Karte bei einem Zeitstempel in der Zukunft (``core.services.compliance.backup``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 4 Mutanten in
``_backup_checks`` (Zweig ``in_future``): Status (``WARNING``, nicht
``None`` — das Dashboard zaehlt ``check.status.value``), Titel, Kategorie
und die Diagnose mit Pfad und Zeitstempel. Die Karte wird als Ganzes exakt
verglichen.
"""

from datetime import UTC, datetime

from core.services.compliance import backup
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus
from core.services.system import health


class TestBackupChecksZukunft:
    def test_zeitstempel_in_der_zukunft_ergibt_die_warnkarte(self, monkeypatch):
        """x__backup_checks__mutmut_23 (``status=None``), _21 (``label``), _22
        (``category``), _32 (``detail``)."""
        info = {
            "in_future": True,
            "path": "/backups/anlaufstelle-2031.sql.gz",
            "mtime": datetime(2031, 5, 4, 3, 2, tzinfo=UTC),
            "age_hours": -1.0,
        }
        monkeypatch.setattr(health, "last_backup_info", lambda: info)
        (karte,) = backup._backup_checks()
        assert karte == ComplianceCheck(
            key="backup_age",
            label="Letztes Backup",
            category=ComplianceCategory.BACKUP,
            status=ComplianceStatus.WARNING,
            message="Zeitstempel der letzten Sicherung liegt in der Zukunft — das Alter ist nicht messbar.",
            detail="/backups/anlaufstelle-2031.sql.gz (Zeitstempel: 2031-05-04 03:02 UTC)",
            action_hint="Systemuhr/Zeitsynchronisation des Hosts und den Dateizeitstempel der Sicherung prüfen.",
        )
        assert karte.status.value == "warning"
