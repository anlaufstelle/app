"""Inventarabgleich zwischen externem Anker und DB-Bestand (Refs #2296).

Ausgangslage: Die Prueflist von ``verify_audit_chain`` entstand ausschliesslich
aus der System-Kette und den aktuell in der Datenbank vorhandenen
Einrichtungen. Ein gueltiger externer Anker fuer eine inzwischen **fehlende**
Einrichtung wurde deshalb nie angefasst — der Lauf meldete vollstaendige
Integritaet und schrieb den Erfolgsmarker, obwohl eine komplette verankerte
Kette nicht mehr zum Bestand gehoerte (unvollstaendige Wiederherstellung oder
Manipulation mit DB-Owner-Rechten).

Abgrenzung zu Refs #2317: **dort** existiert die Einrichtung und ihre Kette ist
leer (nichts zu verankern, gruen). **Hier** fehlt die Einrichtung, waehrend ihr
Anker weiterlebt (Inventarabweichung, rot).

Gedeckt wird deshalb:

- Verankerte Kette ohne zugehoerige Einrichtung ⇒ Abbruch mit und ohne
  ``--require-anchor``, kein Erfolgsmarker, keine Fortschreibung der HWM-Datei.
- Sowohl eine einzelne entfernte Einrichtung neben intakten Ketten als auch ein
  vollstaendig leerer Einrichtungsbestand.
- Bestehende Einrichtungen, Erstverankerung und legitimes Kettenwachstum
  bleiben unveraendert gruen.
- Legitime endgueltige Entfernung laeuft ueber einen ausdruecklichen,
  authentifizierten Stilllegungsnachweis in der signierten Ankerdatei —
  fehlende DB-Zeilen allein autorisieren nichts.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction

from core.management.commands.verify_audit_chain import Command as VerifyAuditChainCommand
from core.models import AuditLog, Facility
from core.services.audit import audit_event
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

pytestmark = pytest.mark.django_db

# Eindeutiger Anteil der Abbruchmeldung des Inventarabgleichs.
_INVENTORY = "im aktuellen Datenbankbestand fehlen"
_RETIRE = "Stilllegungsnachweis"


def _marker_count() -> int:
    return AuditLog.objects.filter(action=AuditLog.Action.AUDIT_CHAIN_VERIFIED).count()


def _hwm_state(path: str) -> tuple[bool, bytes]:
    if not path or not os.path.exists(path):
        return (False, b"")
    return (True, Path(path).read_bytes())


def _anchor_all_chains(path: str) -> HighWaterMarkStore:
    """Verankert jede vorhandene Kette (die System-Kette erst im zweiten Lauf)."""
    call_command("verify_audit_chain")
    call_command("verify_audit_chain")
    return HighWaterMarkStore.load(path)


def _drop_facility(facility) -> None:
    """Entfernt die Einrichtung samt ihrer Audit-Zeilen am Trigger vorbei.

    Genau der Vorbedingungszustand des Befunds: ein DB-Owner-Eingriff oder eine
    unvollstaendige Wiederherstellung hinterlaesst den Anker ohne Bestand.
    """
    with transaction.atomic(), bypass_replication_triggers():
        Facility.objects.filter(pk=facility.pk).delete()


def _require_postgres() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Das Entfernen der Einrichtung umgeht den Immutability-Trigger (PostgreSQL).")


class TestOrphanedAnchorIsAnInventoryBreak:
    """Verankerte Kette ohne Einrichtung ⇒ Abbruch statt Erfolgsmeldung."""

    @pytest.mark.parametrize("with_switch", [False, True])
    def test_removed_facility_with_surviving_anchor_aborts(self, settings, tmp_path, facility, staff_user, with_switch):
        _require_postgres()
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        store = _anchor_all_chains(settings.AUDIT_HWM_PATH)
        assert store.get(facility.pk), "Vorbedingung: die Kette der Einrichtung ist verankert"
        _drop_facility(facility)
        assert not Facility.objects.exists(), "Vorbedingung: vollstaendig fehlender Einrichtungsbestand"
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)
        markers_before = _marker_count()
        args = ["verify_audit_chain"] + (["--require-anchor"] if with_switch else [])

        with pytest.raises(CommandError, match=_INVENTORY) as excinfo:
            call_command(*args)

        assert str(facility.pk) in str(excinfo.value), "Die verwaiste Kette wird per Kennnummer benannt"
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before, "Keine Fortschreibung der Ankerdatei"
        assert _marker_count() == markers_before, "Kein Erfolgsmarker bei Inventarabweichung"

    def test_single_removed_facility_next_to_an_intact_chain(
        self, settings, tmp_path, facility, staff_user, second_facility, second_facility_user
    ):
        """Eine entfernte Einrichtung neben einer weiterhin intakten Kette."""
        _require_postgres()
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGIN, user=second_facility_user, facility=second_facility)
        store = _anchor_all_chains(settings.AUDIT_HWM_PATH)
        assert store.get(facility.pk) and store.get(second_facility.pk)
        _drop_facility(second_facility)
        assert Facility.objects.filter(pk=facility.pk).exists(), "Vorbedingung: die andere Kette bleibt"
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)
        markers_before = _marker_count()

        with pytest.raises(CommandError, match=_INVENTORY) as excinfo:
            call_command("verify_audit_chain")

        message = str(excinfo.value)
        assert str(second_facility.pk) in message
        assert str(facility.pk) not in message, "Die intakte Kette wird nicht mitgemeldet"
        assert second_facility.name not in message, "Keine Einrichtungsnamen in der Meldung"
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before
        assert _marker_count() == markers_before

    def test_message_names_the_retirement_path(self, settings, tmp_path, facility, staff_user):
        """Die Meldung nennt den vorgesehenen Weg fuer eine legitime Entfernung."""
        _require_postgres()
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        _drop_facility(facility)

        with pytest.raises(CommandError, match=_RETIRE):
            call_command("verify_audit_chain")


class TestRegularOperationStaysGreen:
    """Bestand, Erstverankerung und Kettenwachstum bleiben unveraendert."""

    def test_existing_facilities_run_through(self, settings, tmp_path, facility, staff_user, second_facility):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        markers_before = _marker_count()

        call_command("verify_audit_chain")

        assert _marker_count() == markers_before + 1
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)

    def test_chain_growth_after_anchoring_stays_green(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        tail = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        tail.refresh_from_db()
        markers_before = _marker_count()

        call_command("verify_audit_chain", "--require-anchor")

        assert _marker_count() == markers_before + 1
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)["last_entry_hash"] == tail.entry_hash

    def test_without_hwm_path_there_is_nothing_to_reconcile(self, settings, facility, staff_user):
        settings.AUDIT_HWM_PATH = ""
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        markers_before = _marker_count()

        call_command("verify_audit_chain")

        assert _marker_count() == markers_before + 1


class TestRetirementIsAnExplicitAuthenticatedProof:
    """Legitime endgueltige Entfernung: ausdruecklich, benannt, signiert."""

    def test_retire_chain_unblocks_the_run_and_is_recorded(self, settings, tmp_path, facility, staff_user):
        _require_postgres()
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        retired_pk = facility.pk
        _drop_facility(facility)
        markers_before = _marker_count()

        call_command(
            "verify_audit_chain",
            "--retire-chain",
            str(retired_pk),
            "--retire-reason",
            "Traegerwechsel, Bestand uebergeben",
        )

        assert _marker_count() == markers_before + 1, "Nach der Stilllegung laeuft der Lauf gruen durch"
        entry = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(retired_pk)
        assert entry["retired"], "Der Nachweis bleibt in der signierten Datei stehen"
        assert entry["reason"] == "Traegerwechsel, Bestand uebergeben"
        assert not entry.get("last_entry_hash"), "Eine stillgelegte Kette traegt keinen Tail-Anker mehr"

    def test_retired_chain_stays_green_on_later_runs(self, settings, tmp_path, facility, staff_user):
        _require_postgres()
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        retired_pk = facility.pk
        _drop_facility(facility)
        call_command("verify_audit_chain", "--retire-chain", str(retired_pk), "--retire-reason", "Schliessung")
        markers_before = _marker_count()

        call_command("verify_audit_chain")
        call_command("verify_audit_chain", "--require-anchor")

        assert _marker_count() == markers_before + 2
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(retired_pk)["retired"]

    def test_retiring_an_existing_facility_is_refused(self, settings, tmp_path, facility, staff_user):
        """Eine vorhandene Einrichtung laesst sich nicht wegdefinieren."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)

        with pytest.raises(CommandError, match="existiert weiterhin"):
            call_command("verify_audit_chain", "--retire-chain", str(facility.pk), "--retire-reason", "egal")

        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before

    def test_retirement_requires_a_reason(self, settings, tmp_path, facility, staff_user):
        _require_postgres()
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        retired_pk = facility.pk
        _drop_facility(facility)
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)

        with pytest.raises(CommandError, match="--retire-reason"):
            call_command("verify_audit_chain", "--retire-chain", str(retired_pk))

        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before

    def test_retiring_the_system_chain_is_refused(self, settings, tmp_path, facility, staff_user):
        """Die System-Kette gehoert zu jeder Installation — sie wird nie stillgelegt.

        Sie hat keine Einrichtung, steht also nie im DB-Inventar der
        Einrichtungen; ohne eigene Pruefung passiert sie die Existenzpruefung
        immer. Eine stillgelegte System-Kette bedeutet: kein Tail-Anker mehr
        fuer die installationsweite Kette und damit keine
        Truncation-Erkennung, ohne dass je ein Bestand entfernt wurde.
        """
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)
        markers_before = _marker_count()

        with pytest.raises(CommandError, match="System-Kette"):
            call_command("verify_audit_chain", "--retire-chain", "system", "--retire-reason", "Test")

        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before, "Der System-Anker bleibt unveraendert"
        assert _marker_count() == markers_before
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(None)["last_entry_hash"]

    def test_retiring_a_chain_that_still_has_rows_is_refused(self, settings, tmp_path, facility, staff_user):
        """Tiefenverteidigung: eine Kette MIT Zeilen wird nicht stillgelegt.

        Ueber ``handle`` ist dieser Zustand heute nicht erreichbar — faellt die
        Einrichtung weg, nimmt die Fremdschluessel-Kaskade ihre Audit-Zeilen
        mit. Die Pruefung darf sich darauf aber nicht verlassen: sie ist die
        eigentliche Zusage (stillgelegt wird nur ein Bestand, der wirklich weg
        ist). Deshalb wird die Methode hier direkt mit einer Inventarliste
        aufgerufen, die die Einrichtung nicht enthaelt.
        """
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        store = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH)
        assert store.get(facility.pk), "Vorbedingung: die Kette ist verankert und hat Zeilen"
        command = VerifyAuditChainCommand()

        with pytest.raises(CommandError, match="Audit-Zeilen"):
            command._retire_chains(settings.AUDIT_HWM_PATH, store, [None], [str(facility.pk)], "Test")

        assert store.get(facility.pk)["last_entry_hash"], "Der Anker bleibt unveraendert"

    def test_retiring_an_unanchored_chain_is_refused(self, settings, tmp_path, facility):
        """Ohne Anker gibt es nichts stillzulegen — kein stiller Freibrief."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        HighWaterMarkStore(path=settings.AUDIT_HWM_PATH).save()

        with pytest.raises(CommandError, match="keinen verankerten Tail"):
            call_command(
                "verify_audit_chain",
                "--retire-chain",
                "11111111-1111-1111-1111-111111111111",
                "--retire-reason",
                "Test",
            )

    def test_retirement_needs_an_anchor_path(self, settings, facility):
        settings.AUDIT_HWM_PATH = ""

        with pytest.raises(CommandError, match="AUDIT_HWM_PATH"):
            call_command("verify_audit_chain", "--retire-chain", str(facility.pk), "--retire-reason", "Test")


class TestAnchoredChainKeys:
    """Store-Abfrage: was gilt als extern verankerte Kette?"""

    def test_only_usable_and_active_anchors_count(self):
        store = HighWaterMarkStore(
            path="",
            chains={
                "system": {"count": 2, "last_entry_hash": "abc"},
                "a": {"count": 1, "last_entry_hash": "def"},
                "b": {"count": 0, "last_entry_hash": ""},
                "c": None,
                "d": {"count": 3, "retired": "2026-09-22T00:00:00+00:00", "reason": "Schliessung"},
            },
        )

        assert store.anchored_chain_keys() == ["system", "a"]
