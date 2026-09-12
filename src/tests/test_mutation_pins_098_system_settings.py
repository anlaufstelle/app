"""Pin-Tests der Welle K (0.98) — Prio-2-Testluecken in ``core.services.system.settings``.

Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Batch ``export-settings`` der Survivor-Triage (``triage-096-audit-
settings-forms.json``). Gepinnt werden drei Mutanten-Familien der
SETTINGS_CHANGE-Audit-Zeile:

* **``_jsonable``**: dreht ``value is None`` in ``value is not None`` um. Fuer
  ``None`` faellt die Funktion dann auf ``return str(value)`` — ein
  nullbares Feld, das von "nicht gesetzt" auf einen Wert wechselt, traegt im
  ``detail["changes"]``-Diff den Literalstring ``"None"`` statt eines echten
  JSON-``null``.
* **``log_settings_change`` (Django-Admin-Pfad)**: ``target_type``/
  ``target_id`` fallen weg oder tragen einen anderen Wert als
  ``"Settings"``/die Settings-PK. ``test_mutation_pins_096_settings.py``
  prueft fuer diesen Pfad nur ``facility``/``user``/``detail`` — nicht
  ``target_type``/``target_id``.
* **``update_settings`` (Fach-UI-Pfad)**: derselbe ``target_type``-Wegfall
  bzw. dieselbe Umbenennung, nur fuer den View-Pfad. ``test_mutation_pins_
  096_settings.py`` prueft hier ebenfalls nur ``entry.target_id``, nicht
  ``entry.target_type``.

``AuditLog.target_type``/``target_id`` sind ``CharField(blank=True)`` ohne
expliziten ``default`` — ein weggelassenes Argument landet als ``""``
(Django-``Field.get_default()``), kein ``NULL`` und kein Fehler.

Konventionen wie ``test_mutation_pins_096_settings.py``: Zusicherungen
vergleichen EXAKT (``==``/``is``); Fixtures kommen aus
``src/tests/conftest.py``. ``_encryption_key`` liegt modulweit an (Lehre 112):
der Mutantenbaum hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest

from core.models import AuditLog
from core.services.system.settings import log_settings_change, snapshot_settings, update_settings

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _latest_settings_change(settings_obj):
    return AuditLog.objects.filter(
        action=AuditLog.Action.SETTINGS_CHANGE,
        target_id=str(settings_obj.pk),
    ).latest("timestamp")


# ---------------------------------------------------------------------------
# _jsonable — None bleibt JSON-null, nicht der String "None"
# ---------------------------------------------------------------------------


class TestJsonableNullHandling:
    def test_null_to_value_transition_in_the_changes_diff_stays_json_null(self, settings_obj, staff_user):
        """Mutante ``x__jsonable__mutmut_2`` (``value is None`` ->
        ``value is not None``).

        ``max_storage_mb`` wechselt von "kein Limit" (``None``) auf einen
        Zahlenwert. Betroffen ist der ALTE Wert im Diff: der Mutant schreibt
        den Literalstring ``"None"`` statt echtem ``None`` — jede maschinelle
        Auswertung (z. B. eine Migration, die "kein Limit" erkennen will)
        trifft dann auf eine Zeichenkette statt auf ``None``.
        """
        assert settings_obj.max_storage_mb is None  # Sanity: Startzustand ist "kein Limit"

        update_settings(settings_obj, staff_user, max_storage_mb=512)

        entry = _latest_settings_change(settings_obj)
        assert entry.detail["changes"]["max_storage_mb"] == {"old": None, "new": 512}


# ---------------------------------------------------------------------------
# log_settings_change — Django-Admin-Pfad: target_type/target_id
# ---------------------------------------------------------------------------


class TestLogSettingsChangeTargetPointer:
    @staticmethod
    def _change_session_timeout(settings_obj, staff_user):
        snapshot = snapshot_settings(settings_obj)
        settings_obj.session_timeout_minutes = settings_obj.session_timeout_minutes + 5
        settings_obj.save()
        return log_settings_change(settings_obj, staff_user, snapshot)

    def test_audit_row_target_type_and_id_point_to_the_settings_row(self, settings_obj, staff_user):
        """Mutanten ``x_log_settings_change__mutmut_17`` (``target_type``
        entfernt), ``_18`` (``target_id`` entfernt), ``_20/_21/_22``
        (``target_type`` -> ``"XXSettingsXX"``/``"settings"``/``"SETTINGS"``),
        ``_23`` (``target_id`` -> ``str(None)``).

        Ohne den korrekten Zieltyp/-Pointer fallen die Admin-Pfad-Zeilen aus
        jeder Settings-Auswertung heraus bzw. zeigen (bei mehreren
        Einrichtungen) auf keine eindeutige Konfiguration mehr.
        """
        assert self._change_session_timeout(settings_obj, staff_user) == ["session_timeout_minutes"]

        entry = _latest_settings_change(settings_obj)
        assert entry.target_type == "Settings"
        assert entry.target_id == str(settings_obj.pk)


# ---------------------------------------------------------------------------
# update_settings — Fach-UI-Pfad: target_type
# ---------------------------------------------------------------------------


class TestUpdateSettingsTargetType:
    def test_audit_row_target_type_identifies_the_settings_model(self, settings_obj, staff_user):
        """Mutanten ``x_update_settings__mutmut_35`` (``target_type`` entfernt),
        ``_38/_39/_40`` (``target_type`` -> ``"XXSettingsXX"``/``"settings"``/
        ``"SETTINGS"``).

        Die Spalte ist ein abgefragtes Datenfeld (Audit-Filter/Export), kein
        Anzeigetext — ohne den korrekten Wert fallen die Fach-UI-Zeilen aus
        jeder Settings-Sicht heraus. ``test_mutation_pins_096_settings.py``
        prueft fuer diesen Pfad nur ``entry.target_id``.
        """
        update_settings(settings_obj, staff_user, session_timeout_minutes=42)

        entry = _latest_settings_change(settings_obj)
        assert entry.target_type == "Settings"
