"""Pin-Tests Welle L (release-0.99) — Vollmutation #2174/#2193, Batch **backup-b**.

Modul ``core.services.compliance.backup``, Funktion ``_backup_checks``
(Batch-Schwester ``backup-a`` deckt ``_restore_checks`` im selben Modul ab,
eigene Pin-Datei). Liste der 29 Survivors: ``/work/wellen/welle-l-0.99/
reports/m-scratch/batch-backup-b.json``. Quelle der Mutanten-Rümpfe:
Mutantenbaum ``mutants/src/core/services/compliance/backup.py``.

``_backup_checks`` hat vier Zweige (UNKNOWN/OK/WARNING/CRITICAL, nach dem
Alter des juengsten Backups gegen ``ops_thresholds.BACKUP_WARN_HOURS``/
``BACKUP_CRITICAL_HOURS`` = 36h/48h) und baut je Zweig ein
``ComplianceCheck`` mit sieben Feldern (``key``/``label``/``category``/
``status``/``message``/``detail``/``action_hint``). Die Bestandsdeckung
(``test_compliance_service.py::TestOperationsJobChecks``) prueft nur
``status`` (und im UNKNOWN-Zweig ``message``) bei genau zwei Zweigen
(UNKNOWN, OK bei 6.0h) — WARNING/CRITICAL werden von KEINEM Bestandstest mit
einem echten ``age_hours`` angefahren, und ``key``/``label``/``category``/
``detail``/``action_hint`` sind an keiner Stelle exakt zugesichert. Genau
dort liegen die 29 Survivors dieses Batches.

Konvention wie ``test_mutation_pins_098_compliance_cron.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt zu jedem
Text-Literal zusaetzlich die Variante ``"XX<text>XX"``, die ein
``in``-Vergleich ueberleben wuerde). Je Zweig EIN Test, der alle sieben
Felder exakt prueft — toetet damit alle Feld-Survivors dieses Zweigs in
einem Lauf (mehrere Mutanten je Test, wie vorgesehen).

Produktions-Konsumenten von ``ComplianceCheck.key``/``.category`` bei
**genau** dieser Funktion (Begruendung der Prio-1-Einstufung einzelner
Mutanten, s. Bericht ``m-pins-backup-b-report.md`` Abschnitt 1):

- ``core.services.compliance.__init__.operations_job_checks`` hebt NUR
  ``check.key == "backup_age" and check.status == UNKNOWN`` auf WARNING an
  (Betriebsansicht ``/system/``) — betrifft den UNKNOWN-Zweig (Batch-a-
  Territorium, hier nicht mutiert).
- ``core.views.health._check_stale_jobs`` filtert
  ``check.status == CRITICAL`` und liefert ``check.key`` an das
  ``/health/``-Monitoring — ein verstuemmelter ``key`` im CRITICAL-Zweig
  (Mutanten 55/69/70) macht einen echten Backup-Totalausfall fuer externes
  Monitoring unsichtbar.
- ``core.views.system.compliance.SystemComplianceView`` gruppiert
  ``grouped[check.category].append(check)`` und rendert ``"Backup"`` bewusst
  an Position 2 (Kommentar: "DSGVO Art. 32 Wiederherstellbarkeit zuerst") —
  ein verstuemmelter ``category`` im CRITICAL-Zweig (Mutant 57) verbannt den
  Backup-Check beim akuten Datenverlustrisiko ans Ende unter eine leere
  Ueberschrift.

Nicht gepinnt: keine (alle 29 Survivors sind Klasse ``test``, siehe
Triage-Datei ``reports/m-scratch/pins-backup-b/triage-099-backup-b.json``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.services import compliance
from core.services.compliance import ComplianceStatus
from core.services.compliance._types import ComplianceCategory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_BACKUP_PATH = "/var/backups/x.sql"


def _patch_last_backup_info(info: dict | None):
    """``core.services.system.health.last_backup_info`` -> ``info`` (wie
    ``test_compliance_service.py::TestOperationsJobChecks``)."""
    return patch("core.services.system.health.last_backup_info", return_value=info)


class TestBackupChecksUnknownBranch:
    """Kein Backup gefunden (``last_backup_info`` -> ``None``)."""

    def test_unknown_branch_fields_are_exact(self):
        """Pin: ``x__backup_checks__mutmut_4`` (``label`` -> ``None``),
        ``x__backup_checks__mutmut_8`` (``action_hint`` -> ``None``),
        ``x__backup_checks__mutmut_14`` (``action_hint``-Keyword ganz
        entfernt, gleicher Effekt wie mutmut_8 ueber den Dataclass-Default).

        ``action_hint`` ist hier die einzige Handlungsempfehlung fuer den
        laut ``operations_job_checks``-Docstring schlimmsten Fall ("wird nie
        gesichert, still und ohne Fehlermeldung") — Verlust ist fachliches
        Verhalten (Prio 2), nicht nur Kosmetik.
        """
        with _patch_last_backup_info(None):
            checks = compliance._backup_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "backup_age"
        assert check.label == "Letztes Backup"
        assert check.category == ComplianceCategory.BACKUP
        assert check.status == ComplianceStatus.UNKNOWN
        assert check.detail is None
        assert check.action_hint == "settings.BACKUP_DIR pruefen, Cron-Job 'backup.sh' aktivieren."


class TestBackupChecksOkBranch:
    """Backup jung genug (``age_hours <= BACKUP_WARN_HOURS`` = 36h)."""

    def test_ok_branch_fields_are_exact(self):
        """Pin: ``x__backup_checks__mutmut_20`` (geteilte Variable ``detail``
        -> ``None``, wirkt in OK/WARNING/CRITICAL gleichermaßen),
        ``x__backup_checks__mutmut_25`` (``label`` -> ``None``),
        ``x__backup_checks__mutmut_26`` (``category`` -> ``None``),
        ``x__backup_checks__mutmut_28`` (``message`` -> ``None``),
        ``x__backup_checks__mutmut_29`` (``detail`` -> ``None`` explizit im
        OK-Zweig), ``x__backup_checks__mutmut_35`` (``detail``-Keyword im
        OK-Zweig ganz entfernt, gleicher Effekt ueber den Default).
        """
        info = {"path": _BACKUP_PATH, "mtime": None, "age_hours": 6.0, "is_stale": False}
        with _patch_last_backup_info(info):
            checks = compliance._backup_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "backup_age"
        assert check.label == "Letztes Backup"
        assert check.category == ComplianceCategory.BACKUP
        assert check.status == ComplianceStatus.OK
        assert check.message == "Backup ist 6h alt (Schwelle: 36h)."
        assert check.detail == "/var/backups/x.sql (Alter: 6.0h)"
        assert check.action_hint is None


class TestBackupChecksWarningBranch:
    """Ein Tageslauf fehlt (``BACKUP_WARN_HOURS`` < ``age_hours`` <= ``BACKUP_CRITICAL_HOURS``)."""

    def test_warning_branch_fields_are_exact(self):
        """Pin: ``x__backup_checks__mutmut_39`` (``key`` -> ``None``),
        ``x__backup_checks__mutmut_53`` (``key`` -> ``"XXbackup_ageXX"``),
        ``x__backup_checks__mutmut_54`` (``key`` -> ``"BACKUP_AGE"``),
        ``x__backup_checks__mutmut_40`` (``label`` -> ``None``),
        ``x__backup_checks__mutmut_41`` (``category`` -> ``None``),
        ``x__backup_checks__mutmut_43`` (``message`` -> ``None``),
        ``x__backup_checks__mutmut_44`` (``detail`` -> ``None`` explizit),
        ``x__backup_checks__mutmut_51`` (``detail``-Keyword entfernt,
        gleicher Effekt ueber den Default),
        ``x__backup_checks__mutmut_45`` (``action_hint`` -> ``None``),
        ``x__backup_checks__mutmut_52`` (``action_hint``-Keyword entfernt).
        """
        info = {"path": _BACKUP_PATH, "mtime": None, "age_hours": 40.0, "is_stale": True}
        with _patch_last_backup_info(info):
            checks = compliance._backup_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "backup_age"
        assert check.label == "Letztes Backup"
        assert check.category == ComplianceCategory.BACKUP
        assert check.status == ComplianceStatus.WARNING
        assert check.message == "Backup ist 40h alt — ein Tageslauf fehlt."
        assert check.detail == "/var/backups/x.sql (Alter: 40.0h)"
        assert check.action_hint == "Backup-Cron pruefen, evtl. manuell ausloesen."


class TestBackupChecksCriticalBranch:
    """Mindestens zwei Laeufe ausgefallen (``age_hours > BACKUP_CRITICAL_HOURS``)."""

    def test_critical_branch_fields_are_exact(self):
        """Pin: ``x__backup_checks__mutmut_55`` (``key`` -> ``None``),
        ``x__backup_checks__mutmut_69`` (``key`` -> ``"XXbackup_ageXX"``),
        ``x__backup_checks__mutmut_70`` (``key`` -> ``"BACKUP_AGE"``),
        ``x__backup_checks__mutmut_56`` (``label`` -> ``None``),
        ``x__backup_checks__mutmut_57`` (``category`` -> ``None``),
        ``x__backup_checks__mutmut_59`` (``message`` -> ``None``),
        ``x__backup_checks__mutmut_60`` (``detail`` -> ``None`` explizit),
        ``x__backup_checks__mutmut_67`` (``detail``-Keyword entfernt),
        ``x__backup_checks__mutmut_61`` (``action_hint`` -> ``None``),
        ``x__backup_checks__mutmut_68`` (``action_hint``-Keyword entfernt).

        ``key`` und ``category`` sind hier Prio 1 (siehe Modul-Docstring
        oben): ``_check_stale_jobs`` (``/health/``) und
        ``SystemComplianceView`` (``/system/``) lesen genau diese beiden
        Felder nur im CRITICAL-Zweig.
        """
        info = {"path": _BACKUP_PATH, "mtime": None, "age_hours": 60.0, "is_stale": True}
        with _patch_last_backup_info(info):
            checks = compliance._backup_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "backup_age"
        assert check.label == "Letztes Backup"
        assert check.category == ComplianceCategory.BACKUP
        assert check.status == ComplianceStatus.CRITICAL
        assert check.message == "Backup ist 60h alt — mindestens zwei Laeufe ausgefallen."
        assert check.detail == "/var/backups/x.sql (Alter: 60.0h)"
        assert check.action_hint == "Backup-Cron + Disk-Speicher pruefen, Restore-Test nachholen."


class TestCheckStaleJobsSurfacesCriticalBackupKey:
    """Prio-1-Regressionsguard: das echte ``/health/``-Monitoring liest den
    ``key`` des CRITICAL-Backup-Checks ueber ``cron_job_checks()`` — nicht
    ueber eine gemockte Konstante (die wuerde die Mutation gar nicht
    durchlaufen)."""

    def test_critical_backup_key_reaches_stale_jobs_filter(self):
        """Pin (zusaetzlich zu ``TestBackupChecksCriticalBranch``):
        ``x__backup_checks__mutmut_55``/``_69``/``_70`` — bricht
        ``core.views.health._check_stale_jobs``'s
        ``[check.key for check in cron_job_checks() if check.status ==
        CRITICAL]``: ohne exakten ``key`` fehlt ``"backup_age"`` in der
        Liste, und ein echter Backup-Totalausfall bleibt fuer das externe
        Monitoring stumm.
        """
        info = {"path": _BACKUP_PATH, "mtime": None, "age_hours": 60.0, "is_stale": True}
        with _patch_last_backup_info(info):
            checks = compliance.cron_job_checks()

        critical_keys = [c.key for c in checks if c.status == ComplianceStatus.CRITICAL]
        assert "backup_age" in critical_keys
