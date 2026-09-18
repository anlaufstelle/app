"""Tests fuer die Anker-Pflicht von ``verify_audit_chain`` (Refs #2242).

Ausgangslage: ein fehlender externer High-Water-Mark gilt dem Kommando als
**Erstlauf** — die aktuell vorhandene Kette wird dann ohne Vergleich mit dem
gesicherten Original neu verankert. Nach einem fehlgeschlagenen HWM-Import auf
einem wiederhergestellten Host kann so ein abgeschnittener, in sich konsistenter
Audit-Bestand gruen neu verankert werden.

Gedeckt wird deshalb:

- Mit ``--require-anchor`` bricht das Kommando ab, wenn fuer *irgendeine* der
  tatsaechlich geprueften Ketten kein Anker vorliegt (Pfad nicht gesetzt /
  Datei fehlt / Datei ohne verankerten Tail / Anker nur fuer einen Teil der
  Ketten) — und zwar OHNE einen neuen Anker und OHNE eine neue Audit-Zeile.
- Jeder Abbruch laesst Ankerdatei (Existenz, Inhalt, mtime) und die
  **Gesamtzahl** der Audit-Zeilen unveraendert, nicht nur die Zahl der
  ``AUDIT_CHAIN_VERIFIED``-Marker.
- Mit ``--require-anchor`` und vollstaendigen Ankern laeuft der normale Pfad
  unveraendert; ein Anker, der *kuerzer* als die aktuelle Kette ist, ist der
  Normalfall eines gewachsenen Bestands und bleibt gruen. Ein Anker, dessen
  Tail in der Kette **fehlt** (abgeschnittener Bestand), bleibt wie bisher ein
  roter Integritaetsfehler.
- OHNE den Schalter bleibt die Erstlauf-Behandlung byte-gleich (Default-Pin) —
  der Schalter ist reine Opt-in-Haertung, kein geaenderter Default.
- Strukturell defekte, aber gueltig signierte Anker-Dateien sowie leere oder
  unlesbare Dateien enden in einem kontrollierten Integritaetsfehler statt in
  einem ungefangenen ``AttributeError``/``JSONDecodeError``/``OSError``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction

from core.management.commands.verify_audit_chain import Command as VerifyAuditChainCommand
from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

# Eindeutiger Anteil der Abbruchmeldung: trennt den erwarteten Abbruch von einem
# argparse-„unrecognized arguments"-Fehler, der ebenfalls ein CommandError waere.
_ABORT = "verlangt einen externen Audit-Anker"
# Kontrollierte Integritaetsfehler der Store-Schema-Pruefung.
_STRUCTURE = "Struktur ungueltig"
_UNREADABLE = "nicht lesbar"


def _marker_count() -> int:
    return AuditLog.objects.filter(action=AuditLog.Action.AUDIT_CHAIN_VERIFIED).count()


def _audit_state() -> tuple[int, int]:
    """(Gesamtzahl aller Audit-Zeilen, Zahl der Verifiziert-Marker).

    Die Gesamtzahl faengt auch eine Audit-Zeile *anderer* Action, die ein
    Abbruchpfad versehentlich schriebe.
    """
    return AuditLog.objects.count(), _marker_count()


def _hwm_state(path: str) -> tuple[bool, bytes, int | None]:
    """(Existenz, Inhalt, mtime) der Ankerdatei — ein Abbruch aendert nichts davon."""
    if not path or not os.path.exists(path):
        return (False, b"", None)
    return (True, Path(path).read_bytes(), os.stat(path).st_mtime_ns)


def _anchor_all_chains(path: str) -> HighWaterMarkStore:
    """Verankert jede vorhandene Kette (inkl. System-Kette) und liefert den Store.

    Der erste Lauf verankert nur die Facility-Ketten: die System-Kette ist zu
    diesem Zeitpunkt leer und bekommt ihre erste Zeile erst durch den
    Verifiziert-Marker *nach* dem Fortschreiben. Der zweite Lauf verankert sie.
    """
    call_command("verify_audit_chain")
    call_command("verify_audit_chain")
    return HighWaterMarkStore.load(path)


@pytest.mark.django_db
class TestRequireAnchorAborts:
    """Fehlender Anker + Schalter ⇒ Abbruch statt stiller Neuverankerung."""

    def test_missing_hwm_file_aborts_without_anchoring(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_before = _audit_state()
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")

        assert not os.path.exists(settings.AUDIT_HWM_PATH), "Abbruch darf keinen neuen Anker anlegen"
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before
        assert _audit_state() == audit_before, "Abbruch darf keine Audit-Zeile schreiben"

    def test_anchorless_hwm_file_aborts_and_stays_byte_identical(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        HighWaterMarkStore(path=settings.AUDIT_HWM_PATH).save()  # signiert, aber ohne Kette
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_before = _audit_state()
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")

        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before
        assert _audit_state() == audit_before

    def test_unset_hwm_path_aborts(self, settings, facility, staff_user):
        settings.AUDIT_HWM_PATH = ""
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_before = _audit_state()

        with pytest.raises(CommandError, match=_ABORT):
            call_command("verify_audit_chain", "--require-anchor")

        assert _audit_state() == audit_before

    def test_anchor_for_one_facility_only_aborts_and_names_the_uncovered_pk(
        self, settings, tmp_path, facility, staff_user, second_facility, second_facility_user
    ):
        """Ein Anker irgendwo im Store genuegt nicht: JEDE geprueffte Kette braucht einen.

        Sonst laeuft fuer die ankerlose Kette derselbe Erstlauf-Pfad wie ohne
        Schalter — der wiederhergestellte Bestand wuerde ungeprueft verankert.
        """
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGIN, user=second_facility_user, facility=second_facility)
        store = _anchor_all_chains(settings.AUDIT_HWM_PATH)
        assert store.get(second_facility.pk), "Vorbedingung: beide Ketten sind verankert"
        store.chains.pop(str(second_facility.pk))
        store.save()  # gueltig signiert, aber ohne Anker fuer die zweite Einrichtung
        audit_before = _audit_state()
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)

        with pytest.raises(CommandError, match=_ABORT) as excinfo:
            call_command("verify_audit_chain", "--require-anchor")

        message = str(excinfo.value)
        assert f"#{second_facility.pk}" in message, "Die betroffene Einrichtung wird per PK benannt"
        assert second_facility.name not in message, "Keine Einrichtungsnamen in der Meldung"
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before, "Kein neuer Anker fuer IRGENDEINE Kette"
        assert _audit_state() == audit_before

    def test_missing_system_anchor_alone_aborts(self, settings, tmp_path, facility, staff_user):
        """Auch die System-Kette (``facility=NULL``) braucht ihren eigenen Anker."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        store = _anchor_all_chains(settings.AUDIT_HWM_PATH)
        assert store.get(None), "Vorbedingung: die System-Kette ist verankert"
        store.chains.pop("system")
        store.save()
        audit_before = _audit_state()
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)

        with pytest.raises(CommandError, match=_ABORT) as excinfo:
            call_command("verify_audit_chain", "--require-anchor")

        assert "System" in str(excinfo.value)
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before
        assert _audit_state() == audit_before


@pytest.mark.django_db
class TestHwmStoreSchemaIsFailClosed:
    """Gueltig signierte, aber strukturell defekte Stores enden kontrolliert.

    Alle Faelle betreffen den Default-Pfad OHNE Schalter: dort endeten sie
    bisher in einem ungefangenen ``AttributeError``/``JSONDecodeError``/
    ``OSError`` statt in einem sprechenden Integritaetsfehler.
    """

    @pytest.mark.parametrize(
        ("chains", "label"),
        [
            ({"system": "abc"}, "Skalar statt Eintrags-Dict"),
            ({"system": {"count": 1, "last_entry_hash": 5}}, "Nicht-String-Tail"),
            ("kaputt", "Skalar statt Ketten-Dict"),
        ],
    )
    def test_malformed_signed_store_is_command_error(self, settings, tmp_path, facility, staff_user, chains, label):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        HighWaterMarkStore(path=settings.AUDIT_HWM_PATH, chains=chains).save()
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_before = _audit_state()

        with pytest.raises(CommandError, match=_STRUCTURE):
            call_command("verify_audit_chain")

        assert _audit_state() == audit_before, label

    def test_empty_hwm_file_is_command_error(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        Path(settings.AUDIT_HWM_PATH).write_bytes(b"")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_before = _audit_state()

        with pytest.raises(CommandError, match=_UNREADABLE):
            call_command("verify_audit_chain")

        assert _audit_state() == audit_before

    def test_unreadable_hwm_path_is_command_error(self, settings, tmp_path, facility, staff_user):
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm-dir")
        os.mkdir(settings.AUDIT_HWM_PATH)
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_before = _audit_state()

        with pytest.raises(CommandError, match=_UNREADABLE):
            call_command("verify_audit_chain")

        assert _audit_state() == audit_before

    def test_signature_error_still_wins_over_structure(self, settings, tmp_path):
        """Reihenfolge-Pin: Manipulationsverdacht schlaegt den Strukturbefund."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        Path(settings.AUDIT_HWM_PATH).write_text(
            json.dumps({"version": 1, "chains": {"system": "abc"}, "sig": "00"}), encoding="utf-8"
        )

        with pytest.raises(CommandError, match="Signatur ungueltig"):
            call_command("verify_audit_chain")


@pytest.mark.django_db
class TestRequireAnchorGreenPaths:
    """Mit vollstaendigen Ankern bzw. ohne Schalter bleibt alles beim Alten."""

    def test_anchor_shorter_than_chain_runs_through_and_advances(self, settings, tmp_path, facility, staff_user):
        """Gewachsener Bestand: der verankerte Tail liegt hinter dem aktuellen Ende."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        store = _anchor_all_chains(settings.AUDIT_HWM_PATH)
        anchored_count = store.get(facility.pk)["count"]

        tail = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        tail.refresh_from_db()
        markers_before = _marker_count()
        assert AuditLog.objects.filter(facility=facility).count() > anchored_count

        call_command("verify_audit_chain", "--require-anchor")

        recorded = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)
        assert recorded["last_entry_hash"] == tail.entry_hash
        assert _marker_count() == markers_before + 1

    def test_anchor_ahead_of_chain_stays_a_red_integrity_error(self, settings, tmp_path, facility, staff_user):
        """Bestandsverhalten: fehlt der verankerte Tail, ist der Lauf rot — mit Schalter wie ohne."""
        if connection.vendor != "postgresql":
            pytest.skip("Der Tail-Schnitt umgeht den Immutability-Trigger (PostgreSQL).")
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _anchor_all_chains(settings.AUDIT_HWM_PATH)
        tail = AuditLog.objects.filter(facility=facility).order_by("timestamp", "id").last()
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=tail.pk).delete()
        markers_before = _marker_count()
        hwm_before = _hwm_state(settings.AUDIT_HWM_PATH)

        with pytest.raises(SystemExit) as excinfo:
            call_command("verify_audit_chain", "--require-anchor")

        assert excinfo.value.code == 1
        assert _hwm_state(settings.AUDIT_HWM_PATH) == hwm_before, "Ein Bruch ruckt den Anker nicht vor"
        assert _marker_count() == markers_before

    def test_default_first_run_still_anchors_silently(self, settings, tmp_path, facility, staff_user):
        """Default-Pin: ohne Schalter bleibt der Erstlauf-Pfad unveraendert."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        tail = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        tail.refresh_from_db()
        markers_before = _marker_count()

        call_command("verify_audit_chain")

        recorded = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)
        assert recorded["last_entry_hash"] == tail.entry_hash
        assert _marker_count() == markers_before + 1

    def test_default_ignores_incomplete_anchors(self, settings, tmp_path, facility, staff_user, second_facility):
        """Default-Pin: eine nur teilweise verankerte Datei ist ohne Schalter kein Abbruchgrund."""
        settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        store = _anchor_all_chains(settings.AUDIT_HWM_PATH)
        store.chains.pop("system")
        store.save()
        markers_before = _marker_count()

        call_command("verify_audit_chain")

        assert _marker_count() == markers_before + 1
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(None)

    def test_default_without_hwm_path_stays_green(self, settings, facility, staff_user):
        """Default-Pin: ohne Anker-Feature laeuft das Kommando wie bisher durch."""
        settings.AUDIT_HWM_PATH = ""
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        markers_before = _marker_count()

        call_command("verify_audit_chain")

        assert _marker_count() == markers_before + 1


class TestMissingAnchors:
    """Einheitliche Semantik der Store-Abfrage — auch fuer defekte Eintraege."""

    def test_reports_every_chain_without_usable_tail(self):
        store = HighWaterMarkStore(
            path="",
            chains={
                "system": {"count": 2, "last_entry_hash": "abc"},
                "7": {"count": 0, "last_entry_hash": ""},
                "9": "kaputt",
            },
        )

        assert store.missing_anchors([None, 7, 9, 11]) == [7, 9, 11]
        assert store.missing_anchors([None]) == []


class TestRequireAnchorOption:
    def test_option_is_opt_in(self):
        """Default-Pin auf der Kommandozeile: ohne Angabe bleibt der Schalter aus."""
        parser = VerifyAuditChainCommand().create_parser("manage.py", "verify_audit_chain")

        assert parser.parse_args([]).require_anchor is False
        assert parser.parse_args(["--require-anchor"]).require_anchor is True
