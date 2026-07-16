"""Follow-Up-Tests für Mutation-Survivors in
``core.services.audit.chain`` (Refs #1388).

Die HMAC-Kette macht den append-only ``AuditLog`` tamper-evident (Refs #1070)
— sicherheitskritisch. Der Voll-Lauf 2026-07-16 liess in ``verify_chain``
echte Survivors am Zähler (``rows_checked``) und an den ``ChainResult``-
Statusfeldern stehen: Tests prüften bisher ``ok``, aber nicht die genaue Zahl
geprüfter Zeilen bzw. dass ein Ketten-Bruch ``ok=False`` (nicht ``None``)
liefert.

Jeder Test benennt die konkret gekillte Mutation.
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit.chain import verify_chain
from core.services.system import bypass_replication_triggers


def _requires_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("AuditLog-Kette/Trigger erfordert PostgreSQL")


def _chained_count(facility):
    return AuditLog.objects.filter(facility=facility).exclude(entry_hash="").count()


@pytest.mark.django_db
class TestVerifyChainRowsChecked:
    def setup_method(self):
        _requires_postgres()

    def test_reports_exact_number_of_checked_rows(self, facility, staff_user):
        """Killt drei ``verify_chain``-Survivors am Zähler/Erfolgs-Ergebnis:

        - ``mutmut_22``: ``checked += 1`` → ``checked -= 1``.
        - ``mutmut_23``: ``checked += 1`` → ``checked += 2``.
        - ``mutmut_73``: ``return ChainResult(True, checked)`` →
          ``ChainResult(True, None)``.

        ``rows_checked`` ist der forensische Beleg, WIE VIEL Kette geprüft
        wurde (Basis für den High-Water-Mark-Folgeschritt). Ein falscher oder
        ``None``-Zähler entwertet einen grünen Verify-Report.
        """
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)

        expected = _chained_count(facility)
        result = verify_chain(facility)

        assert result.ok is True
        assert result.rows_checked == expected  # mutmut_22/_23 (Zähler) + mutmut_73 (None)
        assert expected >= 3


@pytest.mark.django_db
class TestVerifyChainLinkageBreak:
    def setup_method(self):
        _requires_postgres()

    def test_middle_deletion_reports_ok_false(self, facility, staff_user):
        """Killt ``verify_chain__mutmut_59``:
        ``return ChainResult(False, checked, …, "prev_hash linkage break …")``
        → ``ChainResult(None, …)``.

        Wird eine MITTLERE Zeile am Immutability-Trigger vorbei gelöscht
        (Owner-/``replica``-Vektor), trägt der Nachfolger einen danglenden
        ``prev_hash`` → Verkettungsbruch. Das Ergebnis MUSS ``ok is False``
        sein; ein ``None`` (falsy, aber nicht ``False``) würde einen
        ``if result.ok:``-Aufrufer täuschen, aber ``assert x is False``
        härtet die exakte Semantik.
        """
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        middle = audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)

        # Mittlere Zeile hart löschen (Trigger umgehen) → Nachfolger dangling.
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=middle.pk).delete()

        result = verify_chain(facility)
        assert result.ok is False  # mutmut_59: darf nicht None sein
        assert result.first_break_id
        assert "linkage break" in result.reason
