"""Mutation-Pins v0.96.0 — Zurechenbarkeit der SETTINGS_CHANGE-Zeile.

``test_audit_coverage.py::TestSettingsChangeAudit`` zaehlt die Zeilen des
Fach-UI-Pfades (``update_settings``) und des Django-Admin-Pfades
(``log_settings_change``, ``core/admin/system.py::SettingsAdmin.save_model``),
prueft aber weder ``facility`` noch ``user`` — und fuer den Admin-Pfad auch
den ``detail``-Payload nicht. Eine Zeile ohne Einrichtung faellt aus dem
``FacilityScopedManager`` und in die NULL-Facility-HMAC-Kette (Refs #1070),
eine Zeile ohne Benutzer beantwortet die Frage nicht mehr, fuer die sie
existiert (Art. 5 Abs. 2 DSGVO): wer hat die MFA-Pflicht abgeschaltet, wer die
Aufbewahrungsfrist verkuerzt.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import AuditLog
from core.services.system.settings import log_settings_change, snapshot_settings, update_settings

pytestmark = pytest.mark.django_db


def _latest_settings_change(settings_obj):
    return AuditLog.objects.filter(
        action=AuditLog.Action.SETTINGS_CHANGE,
        target_id=str(settings_obj.pk),
    ).latest("timestamp")


class TestUpdateSettingsAuditRow:
    """Fach-UI-Pfad ``update_settings`` (core/views/facility_settings.py)."""

    def test_audit_row_is_scoped_to_the_settings_facility(self, settings_obj, staff_user):
        """Pins x_update_settings__mutmut_26/_32: die Zeile traegt die Einrichtung
        der Settings-Zeile, nicht ``None`` und nicht das Modell-Default."""
        update_settings(settings_obj, staff_user, session_timeout_minutes=42)

        entry = _latest_settings_change(settings_obj)
        assert entry.facility_id == settings_obj.facility_id
        assert entry.facility_id is not None

    def test_audit_row_names_the_acting_user(self, settings_obj, staff_user):
        """Pins x_update_settings__mutmut_27/_33: die privilegierte Aenderung ist
        der handelnden Person zugerechnet."""
        update_settings(settings_obj, staff_user, session_timeout_minutes=42)

        entry = _latest_settings_change(settings_obj)
        assert entry.user_id == staff_user.pk
        assert entry.user_id is not None

    def test_non_audited_key_is_rejected_while_facility_stays_exempt(self, settings_obj, staff_user, facility):
        """Pins x_update_settings__mutmut_9: der Mass-Assignment-Guard laesst
        genau ``facility`` durch und weist jeden anderen nicht auditierten
        Schluessel ab — die Umkehrung des Vergleichs oeffnet das ``setattr`` fuer
        beliebige Settings-Felder, die nie im Diff erschienen."""
        with pytest.raises(ValueError, match=r"^Feld 'updated_at' darf nicht aktualisiert werden\.$"):
            update_settings(settings_obj, staff_user, updated_at=timezone.now())

        assert not AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).exists()

        # ``facility`` ist der einzige bewusst ausgenommene Schluessel
        # (``_AUDIT_EXEMPT``): der View reicht die Facility mit durch.
        update_settings(settings_obj, staff_user, facility=facility, session_timeout_minutes=42)

        settings_obj.refresh_from_db()
        assert settings_obj.session_timeout_minutes == 42
        assert _latest_settings_change(settings_obj).detail["changed_fields"] == ["session_timeout_minutes"]


class TestLogSettingsChangeAuditRow:
    """Django-Admin-Pfad ``log_settings_change`` (SettingsAdmin.save_model)."""

    @staticmethod
    def _change_session_timeout(settings_obj, staff_user):
        snapshot = snapshot_settings(settings_obj)
        settings_obj.session_timeout_minutes = settings_obj.session_timeout_minutes + 5
        settings_obj.save()
        return log_settings_change(settings_obj, staff_user, snapshot)

    def test_audit_row_is_scoped_to_the_settings_facility(self, settings_obj, staff_user):
        """Pins x_log_settings_change__mutmut_8/_14: auch der Admin-Pfad haengt die
        Zeile an die Einrichtung — sonst ist sie fuer deren Sicht unsichtbar."""
        assert self._change_session_timeout(settings_obj, staff_user) == ["session_timeout_minutes"]

        entry = _latest_settings_change(settings_obj)
        assert entry.facility_id == settings_obj.facility_id
        assert entry.facility_id is not None

    def test_audit_row_names_the_acting_user(self, settings_obj, staff_user):
        """Pins x_log_settings_change__mutmut_9/_15: auch die im Django-Admin
        vorgenommene Aenderung ist zurechenbar."""
        assert self._change_session_timeout(settings_obj, staff_user) == ["session_timeout_minutes"]

        entry = _latest_settings_change(settings_obj)
        assert entry.user_id == staff_user.pk
        assert entry.user_id is not None

    def test_audit_detail_carries_changed_fields_and_value_safe_old_new(self, settings_obj, staff_user):
        """Pins x_log_settings_change__mutmut_19: der Admin-Pfad schreibt dasselbe
        Detail-Schema wie ``update_settings`` (Security N8, Refs #1445) — ohne
        ``changed_fields``/``changes`` bliebe eine Fristenaenderung forensisch
        nicht rekonstruierbar."""
        old = settings_obj.session_timeout_minutes

        assert self._change_session_timeout(settings_obj, staff_user) == ["session_timeout_minutes"]

        entry = _latest_settings_change(settings_obj)
        assert entry.detail["changed_fields"] == ["session_timeout_minutes"]
        assert entry.detail["changes"]["session_timeout_minutes"] == {"old": old, "new": old + 5}
