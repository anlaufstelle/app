"""Pin-Tests Welle L (release-0.99) — Survivors in ``core.services.compliance.backup``.

Ticket-Auftrag #2174/#2193, Batch **backup-a**: 42 Survivor-Mutanten des
Vollmutations-Laufs auf ``main@f1083ef5`` (Liste: ``/work/wellen/welle-l-0.99/
reports/m-scratch/batch-backup-a.json``), alle in ``x__restore_checks``. Quelle
der Mutanten-Rümpfe: Mutantenbaum ``mutants/src/core/services/compliance/
backup.py``. Der Schwester-Batch **backup-b** (``x__backup_checks``, eigener
Worktree ``/work/anlaufstelle-l-m-backup-b``) pinnt getrennt.

``_restore_checks`` hat dieselbe Vier-Zweig-Struktur wie ``cron.py``
(UNKNOWN/OK/WARNING/CRITICAL nach Alter) — Konvention wie
``test_mutation_pins_098_compliance_cron.py``: Zusicherungen vergleichen EXAKT
(``==``), nie per Teilstring (mutmut erzeugt zu jedem Text-Literal die
Variante ``"XX<text>XX"``, die ein ``in``-Vergleich überlebt). Zeitkontrolle
über ``core.services.compliance._clock.now`` (AuditLog-Zeilen sind per
DB-Trigger, Migration 0024, immutable; das Alter wird über die Zeitquelle
gestellt, nicht über den gespeicherten Zeitstempel).

Anders als der cron-Zwilling ``_latest`` (dort ist die fehlende ``"detail"``
im Ladebudget äquivalent, weil ``cron.py`` das Feld nie liest) liest
``_restore_checks`` sowohl ``entry.timestamp`` (für ``age_days`` UND das
``detail``-f-String) als auch ``entry.detail`` (für die dokumentierte Notiz)
— beide Felder fehlen je einmal im ``.only()``-Ladebudget der Batch, beide
sind hier ein echter Query-Count-Unterschied.

Bestandsdeckung (``test_compliance_service.py::TestRestoreChecks``) prüft an
allen vier Zweigen ausschließlich ``status`` — Wortlaut (``key``/``label``/
``category``/``message``/``detail``/``action_hint``), die Notiz-Auflösung und
das Ladebudget blieben ungepinnt. Genau dort liegen die 42 Survivors dieses
Batches.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import AuditLog
from core.services.compliance import ComplianceStatus, _restore_checks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _patch_compliance_now(fixed: datetime):
    """``core.services.compliance._clock.now`` -> ``fixed`` (Refs #958-M3)."""
    return patch("core.services.compliance._clock.now", return_value=fixed)


def _restore_marker(detail: dict) -> AuditLog:
    """Frischer RESTORE_VERIFIED-Marker, sonst keiner in der DB.

    Wie ``test_mutation_pins_098_compliance_cron.py::_marker``: die Zeile wird
    nach dem Insert erneut aus der DB gelesen, damit Zeit-/Formatvergleiche
    den gespeicherten statt den In-Memory-Zeitstempel nutzen.
    """
    AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
    entry = AuditLog.objects.create(
        action=AuditLog.Action.RESTORE_VERIFIED,
        facility=None,
        target_type="RestoreVerification",
        detail=detail,
    )
    return AuditLog.objects.get(pk=entry.pk)


class TestRestoreChecksQueryBudget:
    """``.only()`` muss beide Felder nennen, die der Check danach liest."""

    def test_single_query_loads_timestamp_and_detail(self):
        """Pin: ``x__restore_checks__mutmut_4`` (``.only("timestamp",
        "detail")`` -> ``.only("detail")``), ``x__restore_checks__mutmut_5``
        (-> ``.only("timestamp", )``).

        ``_restore_checks`` liest nach dem ``.first()`` sowohl
        ``entry.timestamp`` (für ``age_days``) als auch ``entry.detail``
        (für die Notiz im ``detail``-Feld) — fehlt eines der beiden im
        Ladebudget, löst der erste Zugriff einen zweiten ``SELECT`` auf
        dieselbe AuditLog-Zeile aus (das Compliance-Dashboard ruft die
        Checks bei jedem Aufruf neu ab).

        Original: genau 1 Query auf ``core_auditlog``. Jeder Mutant: 2.
        """
        _restore_marker(detail={"note": "Testnotiz"})

        with CaptureQueriesContext(connection) as ctx:
            checks = _restore_checks()

        assert checks[0].status == ComplianceStatus.OK
        audit_queries = [q for q in ctx.captured_queries if AuditLog._meta.db_table in q["sql"]]
        assert len(audit_queries) == 1


class TestRestoreChecksUnknownBranch:
    def test_unknown_branch_carries_every_field(self):
        """Pin: ``x__restore_checks__mutmut_16`` (``label=None``),
        ``mutmut_19`` (``message=None``), ``mutmut_20``/``mutmut_26``
        (``action_hint=None``/Keyword gestrichen), ``mutmut_27``
        (``key="XXrestore_verifiedXX"``), ``mutmut_28``
        (``key="RESTORE_VERIFIED"``).

        Bestandsdeckung (``test_compliance_service.py::TestRestoreChecks::
        test_unknown_when_no_restore_entry``) prüft nur ``status`` — Key/
        Label/Message/Action-Hint blieben ungepinnt.
        """
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()

        checks = _restore_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.UNKNOWN
        assert check.key == "restore_verified"
        assert check.label == "Restore-Test"
        assert check.category == "Backup"
        assert check.message == "Noch nie ein Restore-Test dokumentiert."
        assert check.action_hint == ("manage.py mark_restore_verified --note '...' nach jedem Test ausfuehren.")


class TestRestoreChecksOkBranch:
    def test_ok_branch_carries_every_field_and_the_documented_note(self):
        """Pin: ``x__restore_checks__mutmut_39`` (``key=None``),
        ``mutmut_40`` (``label=None``), ``mutmut_41`` (``category=None``),
        ``mutmut_43`` (``message=None``), ``mutmut_44``/``mutmut_50``
        (``detail=None``/Keyword gestrichen), ``mutmut_51``
        (``key="XXrestore_verifiedXX"``), ``mutmut_52``
        (``key="RESTORE_VERIFIED"``).

        Killt zusätzlich die geteilte ``detail``-Konstruktion (vor dem
        ersten Branch berechnet, für OK/WARNING/CRITICAL gleich):
        ``mutmut_31`` (``detail=None``), ``mutmut_32`` (``.get("note") or
        "—"`` -> ``.get("note") and "—"``: bei vorhandener Notiz liefert
        ``and`` den Fallback statt der Notiz), ``mutmut_33``
        (``.get(None)``), ``mutmut_34``/``mutmut_35``
        (``.get("XXnoteXX")``/``.get("NOTE")`` — falscher Dict-Key,
        ``.get`` liefert ``None`` statt der dokumentierten Notiz).
        """
        entry = _restore_marker(detail={"note": "Testnotiz"})

        with _patch_compliance_now(entry.timestamp + timedelta(days=30)):
            checks = _restore_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.OK
        assert check.key == "restore_verified"
        assert check.label == "Restore-Test"
        assert check.category == "Backup"
        assert check.message == "Letzter Test vor 30 Tagen."
        assert check.detail == f"Letzter Restore-Test: {entry.timestamp:%Y-%m-%d}; Notiz: Testnotiz"


class TestRestoreChecksWarningBranch:
    def test_warning_branch_carries_every_field_and_the_fallback_placeholder(self):
        """Pin: ``x__restore_checks__mutmut_55`` (``key=None``),
        ``mutmut_56`` (``label=None``), ``mutmut_57`` (``category=None``),
        ``mutmut_59`` (``message=None``), ``mutmut_60``/``mutmut_67``
        (``detail=None``/Keyword gestrichen), ``mutmut_61``/``mutmut_68``
        (``action_hint=None``/Keyword gestrichen), ``mutmut_69``
        (``key="XXrestore_verifiedXX"``), ``mutmut_70``
        (``key="RESTORE_VERIFIED"``).

        Ohne dokumentierte Notiz greift der Fallback-Platzhalter — killt
        zusätzlich ``mutmut_36`` (``"—"`` -> ``"XX—XX"``): der Mutant
        taucht nur auf, wenn ``.get("note")`` tatsächlich falsy ist (der
        OK-Branch-Test oben hat immer eine echte Notiz und wertet die
        rechte Seite von ``or`` nie aus).
        """
        entry = _restore_marker(detail={})

        with _patch_compliance_now(entry.timestamp + timedelta(days=120)):
            checks = _restore_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.WARNING
        assert check.key == "restore_verified"
        assert check.label == "Restore-Test"
        assert check.category == "Backup"
        assert check.message == "Letzter Test vor 120 Tagen — Auffrischung empfohlen."
        assert check.detail == f"Letzter Restore-Test: {entry.timestamp:%Y-%m-%d}; Notiz: —"
        assert check.action_hint == ("Restore gegen frische DB testen, dann mark_restore_verified ausfuehren.")


class TestRestoreChecksCriticalBranch:
    def test_critical_branch_carries_every_field(self):
        """Pin: ``x__restore_checks__mutmut_71`` (``key=None``),
        ``mutmut_72`` (``label=None``), ``mutmut_73`` (``category=None``),
        ``mutmut_75`` (``message=None``), ``mutmut_76``/``mutmut_83``
        (``detail=None``/Keyword gestrichen), ``mutmut_77``/``mutmut_84``
        (``action_hint=None``/Keyword gestrichen), ``mutmut_85``
        (``key="XXrestore_verifiedXX"``), ``mutmut_86``
        (``key="RESTORE_VERIFIED"``).

        DSGVO-Art.-32-Wortlaut: ``mutmut_75`` (``message=None``) würde die
        einzige Stelle im Dashboard löschen, an der ein Verstoß explizit
        benannt wird ("... verletzt.").
        """
        entry = _restore_marker(detail={"note": "Testnotiz"})

        with _patch_compliance_now(entry.timestamp + timedelta(days=400)):
            checks = _restore_checks()
        check = checks[0]

        assert check.status == ComplianceStatus.CRITICAL
        assert check.key == "restore_verified"
        assert check.label == "Restore-Test"
        assert check.category == "Backup"
        assert check.message == "Letzter Test vor 400 Tagen — DSGVO Art. 32 verletzt."
        assert check.detail == f"Letzter Restore-Test: {entry.timestamp:%Y-%m-%d}; Notiz: Testnotiz"
        assert check.action_hint == "Restore-Test sofort nachholen und dokumentieren."
