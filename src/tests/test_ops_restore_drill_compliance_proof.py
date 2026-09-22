"""Der Compliance-Nachweis belegt nur den vollstaendigen Drill (Refs #2341).

Seit Refs #2316 endet ein Lauf mit uebersprungenen Pflichtschritten mit
Exit-Code 2 und der Zeile „kein vollstaendiger Nachweis". Schritt 7 prueft
davon aber nichts: er sieht nur ``DRILL_FAILED``/``DRILL_UNDETERMINED`` und
ruft ``manage.py mark_restore_verified`` auch dann, wenn die Ankerpruefung
mangels Konfiguration ausgefallen ist — und laeuft ausserdem VOR dem
Exit-2-Block. Der Compliance-Check „Restore-Test" springt damit auf ``ok``,
obwohl der Lauf ausdruecklich keinen vollstaendigen Nachweis behauptet: die
Ampel im Dashboard widerspricht dem Exit-Code des Skripts.

Gedeckt wird deshalb:

- Lauf mit Luecke ⇒ Exit 2 UND **kein** ``mark_restore_verified`` im Stub-Log.
- Die Abschlussausgabe sagt ausdruecklich, dass kein Nachweis geschrieben wurde.
- Kontrollproben unveraendert: vollstaendiger Lauf ⇒ Exit 0 **mit** Nachweis,
  Lauf mit FAIL ⇒ Exit 1 **ohne** Nachweis.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from tests.test_ops_backup_hwm import AUDIT_KEY, _restore_sandbox

pytestmark = pytest.mark.architecture

_PROOF = "mark_restore_verified"
_NO_PROOF_LINE = "kein Compliance-Nachweis geschrieben"


def _valid_hwm_payload() -> str:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    return f'{{"version":1,"chains":{{}},"sig":"{signature}"}}'


def test_skipped_mandatory_step_writes_no_compliance_proof(tmp_path: Path) -> None:
    """Der Kern des Befunds: Luecke im Drill, aber gruene Compliance-Karte."""
    _, docker_log, result = _restore_sandbox(tmp_path, hwm_payload=None, hwm_configured=False)

    assert result.returncode == 2, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "SKIP - Schritt 2b" in result.stdout, "Vorbedingung: der Lauf hat eine Luecke"
    assert _PROOF not in docker_log.read_text(encoding="utf-8"), (
        "Ein Lauf, der ausdruecklich keinen vollstaendigen Nachweis behauptet, darf den "
        "Compliance-Nachweis nicht schreiben"
    )


def test_skipped_run_says_that_no_proof_was_written(tmp_path: Path) -> None:
    """Der Betreiber muss die Folge im Protokoll lesen koennen, nicht raten."""
    _, _, result = _restore_sandbox(tmp_path, hwm_payload=None, hwm_configured=False)

    assert _NO_PROOF_LINE in result.stdout, result.stdout


def test_complete_run_still_writes_the_proof(tmp_path: Path) -> None:
    """Kontrollprobe: am gruenen Pfad aendert sich nichts."""
    _, docker_log, result = _restore_sandbox(tmp_path, hwm_payload=_valid_hwm_payload())

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert _PROOF in docker_log.read_text(encoding="utf-8")
    assert _NO_PROOF_LINE not in result.stdout


def test_failed_run_still_writes_no_proof(tmp_path: Path) -> None:
    """Kontrollprobe: ein FAIL bleibt Exit 1 und ohne Nachweis."""
    _, docker_log, result = _restore_sandbox(tmp_path, hwm_payload='{"version":1,"chains":{},"sig":"manipuliert"}')

    assert result.returncode == 1, f"STDOUT:\n{result.stdout}"
    assert _PROOF not in docker_log.read_text(encoding="utf-8")


def test_proof_step_is_guarded_by_the_gap_counter() -> None:
    """Strukturpin: die Nachweis-Bedingung nennt die Luecken-Zaehlung.

    Ohne sie faellt der Zusammenhang bei der naechsten Erweiterung wieder
    auseinander — der Exit-Code kennt die Luecke, Schritt 7 nicht.
    """
    drill = (Path(__file__).resolve().parents[2] / "scripts" / "ops" / "restore-drill.sh").read_text(encoding="utf-8")
    step_seven = drill.split("# Schritt 7:", maxsplit=1)[1].split("Uebersprungen (", maxsplit=1)[0]

    assert "DRILL_SKIPPED[@]" in step_seven, (
        "Schritt 7 muss die uebersprungenen Pflichtschritte kennen, bevor er den Nachweis schreibt"
    )
    assert step_seven.index("DRILL_SKIPPED[@]") < step_seven.index(_PROOF)
