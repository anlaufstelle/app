"""Regression pins for ``core.services.compliance.cron`` (Refs #2193).

Cover 123 surviving cron mutations after #2219 changed the service. The
comparison used ``make mutation-delta BASE=50eede0a`` at ``0571fcdf``;
mutation definitions come from ``mutants/src/core/services/compliance/cron.py``.

The four last-run checks share an UNKNOWN branch for missing markers,
followed by OK/WARNING/CRITICAL branches based on age. Existing compliance
checks covered status at non-boundary ages. These pins additionally verify
exact text fields and inclusive age boundaries.

As in ``test_mutation_pins_094_retention.py``, assertions compare exact values:
substring checks would accept mutated literals of the form ``"XX<text>XX"``.
Control time through ``core.services.compliance._clock.now`` because audit
rows are immutable through the migration 0024 trigger. Each test documents
its mutation identifiers and the value that changes.

The equivalent ``x__latest__mutmut_4`` mutation is excluded: omitting
``detail`` from ``.only("timestamp", "detail")`` has no observable effect
because the cron checks only access ``entry.timestamp``."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import AuditLog
from core.services import ops_thresholds
from core.services.compliance import (
    ComplianceStatus,
    _audit_chain_checks,
    _breach_scan_checks,
    _mv_refresh_checks,
    _snapshot_checks,
)
from core.services.compliance._types import ComplianceCategory
from core.services.compliance.cron import _age_hours, _cron_action_hint
from tests._job_run_helpers import SUCCESSFUL_JOB_RUN

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _patch_compliance_now(fixed: datetime):
    """``core.services.compliance._clock.now`` -> ``fixed`` (Refs #958-M3)."""
    return patch("core.services.compliance._clock.now", return_value=fixed)


def _marker(action: str) -> AuditLog:
    """Frischer Last-Run-Marker der gegebenen Action, sonst keiner in der DB.

    Die Zeile wird nach dem Insert erneut aus der DB gelesen (wie
    ``test_mutation_pins_094_retention.py::_retention_marker``): für
    Zeit-/Formatvergleiche zählt der gespeicherte Zeitstempel, nicht das
    In-Memory-Objekt.
    """
    AuditLog.objects.filter(action=action).delete()
    entry = AuditLog.objects.create(
        detail={"job_run": SUCCESSFUL_JOB_RUN}, action=action, facility=None, target_type="CronRun"
    )
    return AuditLog.objects.get(pk=entry.pk)


def _no_marker(action: str) -> None:
    """UNKNOWN-Vorbedingung: kein einziger Marker der Action in der DB."""
    AuditLog.objects.filter(action=action).delete()


# ============================================================================
# _latest / _age_hours — geteilte Helper aller vier Last-Run-Checks
# ============================================================================


class TestLatestQueryBudget:
    """``.only()`` muss die Felder nennen, die der Check danach anfasst."""

    def test_single_query_loads_what_it_reads(self):
        """Pin: ``x__latest__mutmut_3`` — ``.only("timestamp", "detail")`` ->
        ``.only("detail")``.

        Fachlich identisch (Django lädt ein zurückgestelltes Feld lazy nach),
        aber nicht wirkungsgleich: jede der vier Last-Run-Funktionen liest
        ``entry.timestamp`` (für ``_age_hours`` UND für das ``detail``-
        f-String). Fehlt ``"timestamp"`` im Ladebudget, löst der erste
        Zugriff einen zweiten ``SELECT`` auf dieselbe AuditLog-Zeile aus —
        das Compliance-Dashboard ruft die Last-Run-Checks bei jedem Aufruf.

        Original: genau 1 Query auf ``core_auditlog``. Mutant: 2 (Basis-
        Query + Deferred-Load von ``timestamp``).
        """
        _marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        with CaptureQueriesContext(connection) as ctx:
            checks = _breach_scan_checks()

        assert checks[0].status == ComplianceStatus.OK
        audit_queries = [q for q in ctx.captured_queries if AuditLog._meta.db_table in q["sql"]]
        assert len(audit_queries) == 1


class TestAgeHoursUsesFullSeconds:
    """Die Ampelgrenzen aller vier Checks hängen an der Sekunden-Stunden-Umrechnung."""

    def test_two_hours_is_exactly_7200_seconds(self):
        """Pin: ``x__age_hours__mutmut_3`` — ``/ 3600.0`` -> ``/ 3601.0``.

        Der Mutant staucht jedes Alter um Faktor 3600/3601 (~0,028 %).
        Original: ``age_hours == 7200 / 3600.0 == 2.0`` exakt (beide
        Operanden ganzzahlig exakt darstellbar). Mutant: ``7200 / 3601.0 ==
        1.99944...`` — die ``==``-Zusicherung unterscheidet beide exakt,
        ganz ohne auf eine Ampelgrenze angewiesen zu sein.
        """
        entry = _marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=2)):
            age = _age_hours(entry)

        assert age == 2.0


# ============================================================================
# _snapshot_checks — monatlicher Cron, Schwellen 35 / 65 Tage
# ============================================================================


class TestSnapshotLabelIsVerbatim:
    def test_label_is_exact_across_every_branch(self):
        """Pin: ``x__snapshot_checks__mutmut_4/5/6`` — Label
        ``"Statistik-Snapshots"`` -> ``"XXStatistik-SnapshotsXX"`` /
        ``"statistik-snapshots"`` / ``"STATISTIK-SNAPSHOTS"``.

        ``key, label = "snapshot_last_run", "Statistik-Snapshots"`` wird am
        Funktionskopf gesetzt und von allen vier Zweigen (UNKNOWN/OK/
        WARNING/CRITICAL) geteilt — ein einziger Test im UNKNOWN-Zweig
        tötet alle drei String-Varianten.
        """
        _no_marker(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)

        checks = _snapshot_checks()

        assert checks[0].label == "Statistik-Snapshots"


class TestSnapshotUnknownBranch:
    def test_unknown_branch_carries_label_and_message(self):
        """Pin: ``x__snapshot_checks__mutmut_11`` (``label=None``),
        ``x__snapshot_checks__mutmut_14`` (``message=None``).

        Bestandsdeckung (``test_compliance_service.py::
        test_snapshot_unknown_when_no_entry``) prüft nur ``status`` —
        ``label``/``message`` blieben ungepinnt.
        """
        _no_marker(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)

        checks = _snapshot_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.UNKNOWN
        assert check.label == "Statistik-Snapshots"
        assert check.message == "Noch kein Snapshot-Lauf — Cron lief vielleicht nie."
        assert check.action_hint == _cron_action_hint()


class TestSnapshotOkBranch:
    def test_ok_branch_at_35_days_carries_every_field(self):
        """Pin: ``x__snapshot_checks__mutmut_24`` (``detail``-Variable ->
        ``None``), ``mutmut_25`` (``<= 35`` -> ``< 35``), ``mutmut_27``
        (``key=None``), ``mutmut_28`` (``label=None``), ``mutmut_29``
        (``category=None``), ``mutmut_31`` (``message=None``),
        ``mutmut_32``/``mutmut_38`` (``detail=None``/Keyword gestrichen).

        Boundary: 35 Tage ist die letzte OK-Position. Mutant 25 (``< 35``)
        würde diesen Tag schon als WARNING zeigen.
        """
        entry = _marker(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(days=35)):
            checks = _snapshot_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.OK
        assert check.key == "snapshot_last_run"
        assert check.label == "Statistik-Snapshots"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Lauf vor 35 Tag(en)."
        assert check.detail == f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"

    def test_36_days_is_already_warning(self):
        """Pin: ``x__snapshot_checks__mutmut_26`` — ``<= 35`` -> ``<= 36``.

        36 Tage überschreitet die OK-Grenze bereits um einen Tag. Der
        Mutant hält den Check bei genau diesem Alter fälschlich auf OK.
        """
        entry = _marker(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(days=36)):
            checks = _snapshot_checks()

        assert checks[0].status == ComplianceStatus.WARNING


class TestSnapshotWarningBranch:
    def test_warning_branch_at_65_days_carries_every_field(self):
        """Pin: ``x__snapshot_checks__mutmut_39`` (``<= 65`` -> ``< 65``),
        ``mutmut_41`` (``key=None``), ``mutmut_42`` (``label=None``),
        ``mutmut_43`` (``category=None``), ``mutmut_45`` (``message=None``),
        ``mutmut_46``/``mutmut_53`` (``detail=None``/Keyword gestrichen),
        ``mutmut_47``/``mutmut_54`` (``action_hint=None``/Keyword
        gestrichen).

        Boundary: 65 Tage ist die letzte WARNING-Position.
        """
        entry = _marker(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(days=65)):
            checks = _snapshot_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.WARNING
        assert check.key == "snapshot_last_run"
        assert check.label == "Statistik-Snapshots"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Lauf vor 65 Tagen — Monats-Cron prüfen."
        assert check.detail == f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == "Cron muss monatlich laufen (1. des Monats)."

    def test_66_days_is_already_critical(self):
        """Pin: ``x__snapshot_checks__mutmut_40`` — ``<= 65`` -> ``<= 66``."""
        entry = _marker(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(days=66)):
            checks = _snapshot_checks()

        assert checks[0].status == ComplianceStatus.CRITICAL


class TestSnapshotCriticalBranch:
    def test_critical_branch_carries_every_field(self):
        """Pin: ``x__snapshot_checks__mutmut_55`` (``key=None``),
        ``mutmut_56`` (``label=None``), ``mutmut_57`` (``category=None``),
        ``mutmut_59`` (``message=None``), ``mutmut_60``/``mutmut_67``
        (``detail=None``/Keyword gestrichen), ``mutmut_61``/``mutmut_68``
        (``action_hint=None``/Keyword gestrichen).
        """
        entry = _marker(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(days=100)):
            checks = _snapshot_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.CRITICAL
        assert check.key == "snapshot_last_run"
        assert check.label == "Statistik-Snapshots"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Lauf vor 100 Tagen — Cron ausgefallen?"
        assert check.detail == f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == _cron_action_hint()


# ============================================================================
# _breach_scan_checks — stündlicher Cron, Schwellen 3 / 24 Stunden
# ============================================================================


class TestBreachScanLabelIsVerbatim:
    def test_label_is_exact(self):
        """Pin: ``x__breach_scan_checks__mutmut_4`` — Label
        ``"Breach-Detection-Scan"`` -> ``"XXBreach-Detection-ScanXX"``.
        """
        _no_marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        checks = _breach_scan_checks()

        assert checks[0].label == "Breach-Detection-Scan"


class TestBreachScanUnknownBranch:
    def test_unknown_branch_message(self):
        """Pin: ``x__breach_scan_checks__mutmut_14`` — ``message=None``.

        Bestandsdeckung prüft nur ``status``.
        """
        _no_marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        checks = _breach_scan_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.UNKNOWN
        assert check.message == "Noch kein Breach-Scan — Cron lief vielleicht nie."


class TestBreachScanOkBranch:
    def test_ok_branch_at_3_hours_carries_every_field(self):
        """Pin: ``x__breach_scan_checks__mutmut_24`` (``detail``-Variable ->
        ``None``), ``mutmut_25`` (``<= 3`` -> ``< 3``), ``mutmut_27``
        (``key=None``), ``mutmut_28`` (``label=None``), ``mutmut_29``
        (``category=None``), ``mutmut_31`` (``message=None``),
        ``mutmut_32``/``mutmut_38`` (``detail=None``/Keyword gestrichen).
        """
        entry = _marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=3)):
            checks = _breach_scan_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.OK
        assert check.key == "breach_scan_last_run"
        assert check.label == "Breach-Detection-Scan"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Scan vor 3h."
        assert check.detail == f"Letzter Scan: {entry.timestamp:%Y-%m-%d %H:%M}"

    def test_three_and_a_half_hours_is_already_warning(self):
        """Pin: ``x__breach_scan_checks__mutmut_26`` — ``<= 3`` -> ``<= 4``.

        3,5 h liegt zwischen der echten (3) und der mutierten (4) Grenze:
        Original schaltet bereits auf WARNING, der Mutant hielte OK.
        """
        entry = _marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=3, minutes=30)):
            checks = _breach_scan_checks()

        assert checks[0].status == ComplianceStatus.WARNING


class TestBreachScanWarningBranch:
    def test_warning_branch_at_24_hours_carries_every_field(self):
        """Pin: ``x__breach_scan_checks__mutmut_39`` (``<= 24`` -> ``< 24``),
        ``mutmut_41`` (``key=None``), ``mutmut_42`` (``label=None``),
        ``mutmut_43`` (``category=None``), ``mutmut_45`` (``message=None``),
        ``mutmut_46``/``mutmut_53`` (``detail=None``/Keyword gestrichen),
        ``mutmut_47``/``mutmut_54`` (``action_hint=None``/Keyword
        gestrichen).
        """
        entry = _marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=24)):
            checks = _breach_scan_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.WARNING
        assert check.key == "breach_scan_last_run"
        assert check.label == "Breach-Detection-Scan"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Scan vor 24h — Cron prüfen."
        assert check.detail == f"Letzter Scan: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == "Cron muss stündlich laufen."

    def test_twentyfour_and_a_half_hours_is_already_critical(self):
        """Pin: ``x__breach_scan_checks__mutmut_40`` — ``<= 24`` -> ``<= 25``."""
        entry = _marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=24, minutes=30)):
            checks = _breach_scan_checks()

        assert checks[0].status == ComplianceStatus.CRITICAL


class TestBreachScanCriticalBranch:
    def test_critical_branch_carries_every_field(self):
        """Pin: ``x__breach_scan_checks__mutmut_55`` (``key=None``),
        ``mutmut_56`` (``label=None``), ``mutmut_57`` (``category=None``),
        ``mutmut_59`` (``message=None``), ``mutmut_60``/``mutmut_67``
        (``detail=None``/Keyword gestrichen), ``mutmut_61``/``mutmut_68``
        (``action_hint=None``/Keyword gestrichen).
        """
        entry = _marker(AuditLog.Action.BREACH_SCAN_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=48)):
            checks = _breach_scan_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.CRITICAL
        assert check.key == "breach_scan_last_run"
        assert check.label == "Breach-Detection-Scan"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Scan vor 48h — Cron ausgefallen?"
        assert check.detail == f"Letzter Scan: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == _cron_action_hint()


# ============================================================================
# _mv_refresh_checks — stündlicher Cron, Schwellen 2 / 6 Stunden
# ============================================================================


class TestMvRefreshLabelIsVerbatim:
    def test_label_is_exact(self):
        """Pin: ``x__mv_refresh_checks__mutmut_4`` — Label
        ``"Statistik-View-Refresh"`` -> ``"XXStatistik-View-RefreshXX"``.
        """
        _no_marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        checks = _mv_refresh_checks()

        assert checks[0].label == "Statistik-View-Refresh"


class TestMvRefreshUnknownBranch:
    def test_unknown_branch_message(self):
        """Pin: ``x__mv_refresh_checks__mutmut_14`` — ``message=None``."""
        _no_marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        checks = _mv_refresh_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.UNKNOWN
        assert check.message == "Noch kein View-Refresh — Cron lief vielleicht nie."


class TestMvRefreshOkBranch:
    def test_ok_branch_at_2_hours_carries_every_field(self):
        """Pin: ``x__mv_refresh_checks__mutmut_24`` (``detail``-Variable ->
        ``None``), ``mutmut_25`` (``<= 2`` -> ``< 2``), ``mutmut_27``
        (``key=None``), ``mutmut_28`` (``label=None``), ``mutmut_29``
        (``category=None``), ``mutmut_31`` (``message=None``),
        ``mutmut_32``/``mutmut_38`` (``detail=None``/Keyword gestrichen).
        """
        entry = _marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=2)):
            checks = _mv_refresh_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.OK
        assert check.key == "mv_refresh_last_run"
        assert check.label == "Statistik-View-Refresh"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Refresh vor 2h."
        assert check.detail == f"Letzter Refresh: {entry.timestamp:%Y-%m-%d %H:%M}"

    def test_two_and_a_half_hours_is_already_warning(self):
        """Pin: ``x__mv_refresh_checks__mutmut_26`` — ``<= 2`` -> ``<= 3``."""
        entry = _marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=2, minutes=30)):
            checks = _mv_refresh_checks()

        assert checks[0].status == ComplianceStatus.WARNING


class TestMvRefreshWarningBranch:
    def test_warning_branch_at_6_hours_carries_every_field(self):
        """Pin: ``x__mv_refresh_checks__mutmut_39`` (``<= 6`` -> ``< 6``),
        ``mutmut_41`` (``key=None``), ``mutmut_42`` (``label=None``),
        ``mutmut_43`` (``category=None``), ``mutmut_45`` (``message=None``),
        ``mutmut_46``/``mutmut_53`` (``detail=None``/Keyword gestrichen),
        ``mutmut_47``/``mutmut_54`` (``action_hint=None``/Keyword
        gestrichen).
        """
        entry = _marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=6)):
            checks = _mv_refresh_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.WARNING
        assert check.key == "mv_refresh_last_run"
        assert check.label == "Statistik-View-Refresh"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Refresh vor 6h — Dashboard evtl. veraltet."
        assert check.detail == f"Letzter Refresh: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == "Cron muss stündlich laufen."

    def test_six_and_a_half_hours_is_already_critical(self):
        """Pin: ``x__mv_refresh_checks__mutmut_40`` — ``<= 6`` -> ``<= 7``."""
        entry = _marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=6, minutes=30)):
            checks = _mv_refresh_checks()

        assert checks[0].status == ComplianceStatus.CRITICAL


class TestMvRefreshCriticalBranch:
    def test_critical_branch_carries_every_field(self):
        """Pin: ``x__mv_refresh_checks__mutmut_55`` (``key=None``),
        ``mutmut_56`` (``label=None``), ``mutmut_57`` (``category=None``),
        ``mutmut_59`` (``message=None``), ``mutmut_60``/``mutmut_67``
        (``detail=None``/Keyword gestrichen), ``mutmut_61``/``mutmut_68``
        (``action_hint=None``/Keyword gestrichen).
        """
        entry = _marker(AuditLog.Action.MV_REFRESH_COMPLETED)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=48)):
            checks = _mv_refresh_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.CRITICAL
        assert check.key == "mv_refresh_last_run"
        assert check.label == "Statistik-View-Refresh"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == "Letzter Refresh vor 48h — Cron ausgefallen?"
        assert check.detail == f"Letzter Refresh: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == _cron_action_hint()


# ============================================================================
# _audit_chain_checks — täglicher Cron 03:30, Schwellen aus ``ops_thresholds``
# ============================================================================


class TestAuditChainLabelIsVerbatim:
    def test_label_is_exact_across_every_branch(self):
        """Pin: ``x__audit_chain_checks__mutmut_4/5/6`` — Label
        ``"Audit-Ketten-Pruefung"`` -> ``"XXAudit-Ketten-PruefungXX"`` /
        ``"audit-ketten-pruefung"`` / ``"AUDIT-KETTEN-PRUEFUNG"``.
        """
        _no_marker(AuditLog.Action.AUDIT_CHAIN_VERIFIED)

        checks = _audit_chain_checks()

        assert checks[0].label == "Audit-Ketten-Prüfung"


class TestAuditChainUnknownBranch:
    def test_unknown_branch_carries_label_and_message(self):
        """Pin: ``x__audit_chain_checks__mutmut_11`` (``label=None``),
        ``mutmut_14`` (``message=None``).
        """
        _no_marker(AuditLog.Action.AUDIT_CHAIN_VERIFIED)

        checks = _audit_chain_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.UNKNOWN
        assert check.label == "Audit-Ketten-Prüfung"
        assert check.message == "Noch keine Ketten-Prüfung — Cron lief vielleicht nie."


class TestAuditChainOkBranch:
    def test_ok_branch_at_warn_threshold_carries_every_field(self):
        """Pin: ``x__audit_chain_checks__mutmut_24`` (``detail``-Variable ->
        ``None``), ``mutmut_25`` (``<= AUDIT_CHAIN_WARN_HOURS`` ->
        ``< AUDIT_CHAIN_WARN_HOURS``), ``mutmut_26`` (``key=None``),
        ``mutmut_27`` (``label=None``), ``mutmut_28`` (``category=None``),
        ``mutmut_30`` (``message=None``), ``mutmut_31``/``mutmut_37``
        (``detail=None``/Keyword gestrichen).

        Boundary: genau ``AUDIT_CHAIN_WARN_HOURS`` (36 h) ist die letzte
        OK-Position — Mutant 25 (``<``) würde diesen Punkt schon als
        WARNING zeigen.
        """
        entry = _marker(AuditLog.Action.AUDIT_CHAIN_VERIFIED)
        warn_hours = ops_thresholds.AUDIT_CHAIN_WARN_HOURS

        with _patch_compliance_now(entry.timestamp + timedelta(hours=warn_hours)):
            checks = _audit_chain_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.OK
        assert check.key == "audit_chain_last_run"
        assert check.label == "Audit-Ketten-Prüfung"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == f"Letzte Prüfung vor {warn_hours}h."
        assert check.detail == f"Letzte Prüfung: {entry.timestamp:%Y-%m-%d %H:%M}"


class TestAuditChainWarningBranch:
    def test_warning_branch_at_critical_threshold_carries_every_field(self):
        """Pin: ``x__audit_chain_checks__mutmut_38`` (``<=
        AUDIT_CHAIN_CRITICAL_HOURS`` -> ``< AUDIT_CHAIN_CRITICAL_HOURS``),
        ``mutmut_39`` (``key=None``), ``mutmut_40`` (``label=None``),
        ``mutmut_41`` (``category=None``), ``mutmut_43`` (``message=None``),
        ``mutmut_44``/``mutmut_51`` (``detail=None``/Keyword gestrichen),
        ``mutmut_45``/``mutmut_52`` (``action_hint=None``/Keyword
        gestrichen).

        Boundary: genau ``AUDIT_CHAIN_CRITICAL_HOURS`` (48 h) ist die
        letzte WARNING-Position — Mutant 38 (``<``) würde diesen Punkt
        schon als CRITICAL zeigen.
        """
        entry = _marker(AuditLog.Action.AUDIT_CHAIN_VERIFIED)
        critical_hours = ops_thresholds.AUDIT_CHAIN_CRITICAL_HOURS

        with _patch_compliance_now(entry.timestamp + timedelta(hours=critical_hours)):
            checks = _audit_chain_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.WARNING
        assert check.key == "audit_chain_last_run"
        assert check.label == "Audit-Ketten-Prüfung"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == f"Letzte Prüfung vor {critical_hours}h — ein Tageslauf fehlt."
        assert check.detail == f"Letzte Prüfung: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == _cron_action_hint()


class TestAuditChainCriticalBranch:
    def test_critical_branch_carries_every_field(self):
        """Pin: ``x__audit_chain_checks__mutmut_53`` (``key=None``),
        ``mutmut_54`` (``label=None``), ``mutmut_55`` (``category=None``),
        ``mutmut_57`` (``message=None``), ``mutmut_58``/``mutmut_65``
        (``detail=None``/Keyword gestrichen), ``mutmut_59``/``mutmut_66``
        (``action_hint=None``/Keyword gestrichen).
        """
        entry = _marker(AuditLog.Action.AUDIT_CHAIN_VERIFIED)
        age_hours = ops_thresholds.AUDIT_CHAIN_CRITICAL_HOURS + 24

        with _patch_compliance_now(entry.timestamp + timedelta(hours=age_hours)):
            checks = _audit_chain_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.CRITICAL
        assert check.key == "audit_chain_last_run"
        assert check.label == "Audit-Ketten-Prüfung"
        assert check.category == ComplianceCategory.BACKGROUND_JOBS
        assert check.message == f"Letzte Prüfung vor {age_hours}h — Cron aus oder Lauf bricht rot ab."
        assert check.detail == f"Letzte Prüfung: {entry.timestamp:%Y-%m-%d %H:%M}"
        assert check.action_hint == _cron_action_hint()
