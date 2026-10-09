"""Pin-Tests Welle L (release-0.99) — Survivors in
``core.services.compliance.audit_events``.

Ticket-Auftrag #2174/#2193 (Batch **compliance-4**): 34 Survivors aus
dem Vollmutations-Lauf (Prüfstand 25685845, Basis main e3c125e6). Liste:
``/work/wellen/welle-l-0.99/reports/m-scratch/batch-compliance-4.json``.
Quelle der Mutanten-Rümpfe: Mutantenbaum
``mutants/src/core/services/compliance/audit_events.py``.

``_audit_event_checks()`` hat drei Antwortpfade nach der 24h-Zählung:
OK (``count == 0``), WARNING (``1 <= count <= 5``), CRITICAL (``count > 5``).
Die Bestandsdeckung (``test_compliance_service.py::TestAuditEventChecks``)
prüft an allen drei Pfaden ausschließlich ``status`` — ``key``/``label``/
``category``/``message``/``detail``/``action_hint`` blieben komplett
ungepinnt. Genau dort liegen alle 34 Survivors dieses Batches (keine
Zähl-/Schwellwert-Logik betroffen — die Bestandsdeckung tötet solche
Mutanten bereits).

Konvention wie ``test_mutation_pins_098_compliance_cron.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt zu jedem
Text-Literal die Variante ``"XX<text>XX"``, die ein ``in``-Vergleich
übersieht). Jeder Test nennt in der Docstring die Mutanten
(``x__audit_event_checks__mutmut_<n>``), die er tötet.
"""

from __future__ import annotations

import pytest

from core.models import AuditLog
from core.services.compliance import ComplianceStatus
from core.services.compliance._types import _CRITICAL_AUDIT_ACTIONS
from core.services.compliance.audit_events import _audit_event_checks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _clear_critical_events():
    AuditLog.objects.filter(action__in=_CRITICAL_AUDIT_ACTIONS).delete()


class TestOkBranchFullWording:
    """``count == 0`` — keine kritischen Events in den letzten 24h."""

    def test_ok_check_full_wording(self):
        """Pin the full wording, including every critical action (Refs #2527).

        Also pins x__audit_event_checks__mutmut_13/17/23/24/25/27: labels,
        detail, key and join separators must retain their complete wording.
        """
        _clear_critical_events()

        checks = _audit_event_checks()

        assert checks[0].key == "critical_audit_events_24h"
        assert checks[0].label == "Kritische Audit-Events (24h)"
        assert checks[0].category == "Audit"
        assert checks[0].status == ComplianceStatus.OK
        assert checks[0].message == "Keine kritischen Events in den letzten 24h."
        assert checks[0].detail == (
            "Gemonitorte Aktionen: security_violation, mfa_failed, webauthn_failed, "
            "mfa_backup_regenerate_denied, sudo_mode_failed, user_deactivated"
        )


class TestWarningBranchFullWording:
    """``1 <= count <= 5`` — drei kritische Events in 24h."""

    def test_warning_check_full_wording(self, facility, staff_user):
        """Pins ``x__audit_event_checks__mutmut_30/31/32/35/36/42/43/44/45/
        46/47/48/49/50/51``.

        ``_30``-``_32``/``_44``/``_45`` verstümmeln ``key``/``label``/
        ``category``; ``_35``/``_42`` setzen bzw. entfernen ``detail``;
        ``_36``/``_43`` setzen bzw. entfernen ``action_hint``; ``_46``-
        ``_51`` verstümmeln je eine der beiden Detail-Textteile (``XX``-
        Präfix/lower/upper, für beide Hälften des zusammengesetzten
        Strings).
        """
        _clear_critical_events()
        for _ in range(3):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.MFA_FAILED,
                target_type="User",
                target_id=str(staff_user.pk),
            )

        checks = _audit_event_checks()

        assert checks[0].key == "critical_audit_events_24h"
        assert checks[0].label == "Kritische Audit-Events (24h)"
        assert checks[0].category == "Audit"
        assert checks[0].status == ComplianceStatus.WARNING
        assert checks[0].message == "3 kritische Event(s) in 24h."
        assert checks[0].detail == (
            "Im System-Audit-Log (/system/audit/) auf SECURITY_VIOLATION / MFA_FAILED / USER_DEACTIVATED filtern."
        )
        assert checks[0].action_hint == "Pro Eintrag den Kontext pruefen, ggf. Issue mit Label security eroeffnen."


class TestCriticalBranchFullWording:
    """``count > 5`` — sieben kritische Events in 24h."""

    def test_critical_check_full_wording(self, facility, staff_user):
        """Pins ``x__audit_event_checks__mutmut_52/53/54/56/57/58/64/65/66/
        67/68/69/70``.

        ``_52``-``_54``/``_66``/``_67`` verstümmeln ``key``/``label``/
        ``category``; ``_56`` setzt ``message=None``; ``_57``/``_64``
        setzen bzw. entfernen ``detail``; ``_58``/``_65`` setzen bzw.
        entfernen ``action_hint``; ``_68``-``_70`` verstümmeln ``detail``
        (``XX``-Präfix/lower/upper).
        """
        _clear_critical_events()
        for _ in range(7):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.SECURITY_VIOLATION,
                target_type="Attachment",
            )

        checks = _audit_event_checks()

        assert checks[0].key == "critical_audit_events_24h"
        assert checks[0].label == "Kritische Audit-Events (24h)"
        assert checks[0].category == "Audit"
        assert checks[0].status == ComplianceStatus.CRITICAL
        assert checks[0].message == "7 kritische Event(s) in 24h — moeglicher Vorfall."
        assert checks[0].detail == "Im System-Audit-Log dringend pruefen."
        assert checks[0].action_hint == ("Vorfall-Triage starten, ggf. DSGVO Art. 33 Meldung an Aufsichtsbehoerde.")
