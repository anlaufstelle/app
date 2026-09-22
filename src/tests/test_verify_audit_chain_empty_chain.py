"""``--require-anchor`` und die leere Audit-Kette einer Einrichtung (Refs #2317).

Ausgangslage: ``verify_audit_chain --require-anchor`` verlangt fuer JEDE
geprueffte Kette einen verankerten Tail. Eine Einrichtung, die angelegt, aber
noch nicht benutzt wurde, hat keine Audit-Zeile — und damit auch keinen Tail,
den ein gruener Lauf verankern koennte (:meth:`HighWaterMarkStore.record`
entfernt den Eintrag bei leerem Tail ausdruecklich). Der Schalter blieb deshalb
dauerhaft rot: genau der Zustand direkt nach der Erstinbetriebnahme.

Die Abgrenzung zu Refs #2296 ist wesentlich: **dort** fehlt die Einrichtung im
Bestand, waehrend ihr Anker existiert (Inventarabweichung, bleibt rot). **Hier**
existiert die Einrichtung, ihre Kette ist leer — es gibt nichts zu verankern.

Gedeckt wird deshalb:

- Eine Einrichtung ohne Audit-Zeilen laesst ``--require-anchor`` gruen durch,
  auch bei wiederholten Laeufen (der Zustand loest sich nicht von selbst auf).
- Die Haertung bleibt vollstaendig: eine Einrichtung MIT Zeilen und ohne Anker
  bricht weiterhin ab, ein manipulierter/abgeschnittener Bestand bleibt rot,
  und eine geleerte Kette mit noch vorhandenem Anker bleibt ein roter
  Integritaetsfehler (kein Freifahrtschein durch „leer = trivial verankert").
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

_ABORT = "verlangt einen externen Audit-Anker"


def _marker_count() -> int:
    return AuditLog.objects.filter(action=AuditLog.Action.AUDIT_CHAIN_VERIFIED).count()


def _hwm_state(path: str) -> tuple[bool, bytes]:
    if not path or not os.path.exists(path):
        return (False, b"")
    return (True, Path(path).read_bytes())


@pytest.mark.django_db
class TestEmptyChainIsTriviallyAnchored:
    """Eine Kette ohne Zeilen hat keinen Tail — und braucht deshalb keinen Anker."""

    def test_unused_facility_does_not_block_require_anchor(
        self, settings, tmp_path, facility, staff_user, second_facility
    ):
        """Frisch angelegte, noch unbenutzte Einrichtung neben einer benutzten.

        Ohne den Fix nennt der Abbruch genau die leere Kette — und tut das bei
        jedem weiteren Lauf erneut, weil sie nie einen Tail bekommt.
        """
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        # Zwei Laeufe verankern erst die Facility-Kette, dann die System-Kette
        # (die ihre erste Zeile durch den Verifiziert-Marker bekommt).
        call_command("verify_audit_chain")
        call_command("verify_audit_chain")
        assert AuditLog.objects.filter(facility=second_facility).count() == 0, (
            "Vorbedingung: die zweite Einrichtung hat keine Audit-Zeile"
        )
        store = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH)
        assert store.get(second_facility.pk) is None, "Vorbedingung: eine leere Kette wird nicht verankert"
        markers_before = _marker_count()

        call_command("verify_audit_chain", "--require-anchor")

        assert _marker_count() == markers_before + 1, "Der Lauf ist gruen und setzt den Verifiziert-Marker"

    def test_repeated_runs_stay_green_with_an_empty_chain(
        self, settings, tmp_path, facility, staff_user, second_facility
    ):
        """Wiederholung: der Zustand loest sich nicht durch weitere Laeufe auf."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        call_command("verify_audit_chain")
        call_command("verify_audit_chain")
        assert AuditLog.objects.filter(facility=second_facility).count() == 0
        markers_before = _marker_count()

        call_command("verify_audit_chain", "--require-anchor")
        call_command("verify_audit_chain", "--require-anchor")

        assert _marker_count() == markers_before + 2

    def test_fresh_install_without_any_audit_row_is_green(self, settings, tmp_path, facility):
        """Erstinbetriebnahme: Einrichtung angelegt, noch keine einzige Audit-Zeile.

        Auch die System-Kette ist hier leer — ohne den Fix bricht der Schalter
        direkt nach der Installation ab.
        """
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        HighWaterMarkStore(path=settings.AUDIT_HWM_PATH).save()
        assert AuditLog.objects.count() == 0, "Vorbedingung: leerer Audit-Bestand"

        call_command("verify_audit_chain", "--require-anchor")

        assert _marker_count() == 1


@pytest.mark.django_db
class TestRequireAnchorStaysStrict:
    """Regressionsschutz: die Anker-Pflicht wird NICHT aufgeweicht."""

    def test_facility_with_rows_and_without_anchor_still_aborts(self, settings, tmp_path, facility, staff_user):
        """Eine Kette MIT Zeilen und ohne Anker bleibt ein Abbruchgrund."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        HighWaterMarkStore(path=settings.AUDIT_HWM_PATH).save()
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)
        markers_before = _marker_count()

        with pytest.raises(CommandError, match=_ABORT) as excinfo:
            call_command("verify_audit_chain", "--require-anchor")

        assert f"#{facility.pk}" in str(excinfo.value)
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before
        assert _marker_count() == markers_before

    def test_emptied_chain_with_surviving_anchor_stays_red(self, settings, tmp_path, facility, staff_user):
        """„Leer" ist kein Freifahrtschein: ein vorhandener Anker deckt die Leerung auf.

        Genau der Angriff, den der High-Water-Mark erkennen soll — eine
        komplett geleerte Kette ist in sich konsistent und faellt nur gegen den
        extern verankerten Tail auf.
        """
        if connection.vendor != "postgresql":
            pytest.skip("Das Leeren der Kette umgeht den Immutability-Trigger (PostgreSQL).")
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        # Zwei Laeufe: der zweite verankert auch die System-Kette, die ihre
        # erste Zeile erst durch den Verifiziert-Marker des ersten bekommt.
        call_command("verify_audit_chain")
        call_command("verify_audit_chain")
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk), "Vorbedingung: Kette verankert"
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(facility=facility).delete()
        assert AuditLog.objects.filter(facility=facility).count() == 0
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)
        markers_before = _marker_count()

        with pytest.raises(SystemExit) as excinfo:
            call_command("verify_audit_chain", "--require-anchor")

        assert excinfo.value.code == 1
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before, "Ein Bruch ruckt den Anker nicht vor"
        assert _marker_count() == markers_before

    def test_missing_hwm_file_still_aborts_with_only_empty_chains(self, settings, tmp_path, facility):
        """Auch bei durchweg leeren Ketten bleibt die FEHLENDE Ankerdatei ein Abbruch.

        Sonst haette ein geloeschter Anker auf einem leergeraeumten Bestand
        keinen Widerspruch mehr.
        """
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        assert not os.path.exists(settings.AUDIT_HWM_PATH)

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")

        assert not os.path.exists(settings.AUDIT_HWM_PATH), "Abbruch darf keinen neuen Anker anlegen"

    def test_unset_hwm_path_still_aborts_with_only_empty_chains(self, settings, facility):
        settings.AUDIT_HWM_PATH = ""

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")
