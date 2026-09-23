"""Betriebsdoku startet ``web`` mit dem vollständigen Compose-Dateisatz (Refs #2309).

Sobald ein Compose-Aufruf mindestens ein ausdrückliches ``-f`` enthält, lädt
Compose ``compose.override.yml`` **nicht** mehr automatisch. Der Admin-Guide
baut deshalb vor jedem Recreate-Befehl die Dateiliste in fester Reihenfolge auf.
``docs/ops-runbook.md`` und ``docs/disaster-recovery.md`` nannten an vielen
Stellen weiterhin nur die Basis-Datei — wer diese Blöcke kopiert, startet ``web``
ohne ``compose.legal.yml``/``compose.override.yml``, und die dort gesetzten
Variablen (Impressum/Datenschutz) fehlen stillschweigend.

Geprüft wird deshalb:

- Kein Befehlsblock startet/erzeugt/neustartet ``web`` (oder den ganzen Stack)
  noch mit ``docker compose -f docker-compose.prod.yml``.
- Wo die Dateiliste aufgebaut wird, ist es **exakt** die Reihenfolge aus dem
  Admin-Guide — programmatisch aus der committeten Datei gezogen, nicht hier
  noch einmal abgeschrieben.
- Jeder Block, der die Liste benutzt, baut sie im selben Block auf (kopierbar)
  und bleibt syntaktisch gültige Bash.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_GUIDE = REPO_ROOT / "docs" / "admin-guide.md"
OPERATIONS_DOCS = (
    REPO_ROOT / "docs" / "ops-runbook.md",
    REPO_ROOT / "docs" / "en" / "ops-runbook.md",
    REPO_ROOT / "docs" / "disaster-recovery.md",
)

_BASE_CALL = "docker compose -f docker-compose.prod.yml"
# Unterkommandos, die den ``web``-Container ERZEUGEN und deshalb die
# Legal-/Override-Datei brauchen. ``restart`` gehoert bewusst nicht dazu: es
# startet den vorhandenen Container mit seiner bestehenden Konfiguration neu.
_RECREATING = {"up", "create"}
# Dienste, die ohne Legal-/Override-Datei auskommen (eigene Umgebung).
_SELF_CONTAINED = {"db", "redis", "clamav", "cron", "maintenance"}


def _series_from_admin_guide() -> list[str]:
    """Die kanonische Dateireihe, aus der ausgelieferten Doku extrahiert."""
    lines = ADMIN_GUIDE.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "compose_files=(-f docker-compose.prod.yml)":
            return [entry.strip() for entry in lines[index : index + 3]]
    raise AssertionError("docs/admin-guide.md enthält keinen Aufbau der Compose-Dateireihe")


_SHELL_FENCES = {"```bash", "```console", "```sh"}
# Kurzreferenzen stehen als ```text-Block da. Sie werden genauso kopiert wie ein
# Shell-Block — der urspruengliche Guard uebersah sie, weil er nur
# Shell-Zaeune las.
_COMMAND_FENCES = _SHELL_FENCES | {"```text"}


def _fenced_blocks(path: Path, fences: set[str]) -> list[tuple[int, str]]:
    """Alle Blöcke mit den genannten Zaun-Sprachen als (Startzeile, Inhalt)."""
    blocks: list[tuple[int, str]] = []
    current: list[str] | None = None
    start = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if current is None:
            if line.strip() in fences:
                current = []
                start = number
        elif line.strip() == "```":
            blocks.append((start, "\n".join(current)))
            current = None
        else:
            current.append(line)
    return blocks


def _bash_blocks(path: Path) -> list[tuple[int, str]]:
    """Nur die ausfuehrbaren Shell-Blöcke (Aufbau-Pflicht, ``bash -n``)."""
    return _fenced_blocks(path, _SHELL_FENCES)


def _command_blocks(path: Path) -> list[tuple[int, str]]:
    """Alle kopierbaren Befehlsblöcke, auch die Kurzreferenzen."""
    return _fenced_blocks(path, _COMMAND_FENCES)


def _prose_commands(path: Path) -> list[tuple[int, str]]:
    """Compose-Aufrufe im Fliesstext, als Inline-Code ausgezeichnet.

    Auch ein Satz „Nach ``docker compose -f … up -d`` laufen …" wird kopiert.
    """
    hits: list[tuple[int, str]] = []
    inside = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            continue
        for span in re.findall(r"`([^`]+)`", line):
            if _BASE_CALL in span:
                hits.append((number, span))
    return hits


def _recreates_web(command: str) -> bool:
    """Erzeugt dieser Compose-Aufruf den ``web``-Container neu?"""
    tail = command.split(_BASE_CALL, maxsplit=1)[1]
    tokens = [token for token in tail.split() if token not in {"&&", "\\", "|"}]
    if not tokens or tokens[0] not in _RECREATING:
        return False
    services = [token for token in tokens[1:] if not token.startswith("-")]
    if not services:  # ganzer Stack
        return True
    return bool(set(services) - _SELF_CONTAINED)


@pytest.mark.parametrize("path", OPERATIONS_DOCS, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_no_web_recreate_uses_the_base_compose_file_alone(path: Path) -> None:
    offenders = []
    for start, block in _command_blocks(path):
        for offset, line in enumerate(block.splitlines()):
            if _BASE_CALL in line and _recreates_web(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{start + offset + 1}: {line.strip()}")
    for number, span in _prose_commands(path):
        if _recreates_web(span):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: (Fliesstext) {span}")

    assert not offenders, "Diese Befehle erzeugen `web` ohne compose.legal.yml/compose.override.yml neu:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize("path", OPERATIONS_DOCS, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_every_block_using_the_series_builds_it_itself(path: Path) -> None:
    series = _series_from_admin_guide()
    for start, block in _bash_blocks(path):
        if '"${compose_files[@]}"' not in block:
            continue
        stripped = [line.strip() for line in block.splitlines()]
        for expected in series:
            assert expected in stripped, (
                f"{path.relative_to(REPO_ROOT)}:{start}: Der Block benutzt die Dateireihe, baut sie aber "
                f"nicht selbst auf — es fehlt: {expected}"
            )
        assert stripped.index(series[0]) < min(
            index for index, line in enumerate(stripped) if '"${compose_files[@]}"' in line
        ), f"{path.relative_to(REPO_ROOT)}:{start}: Die Dateireihe muss VOR ihrer ersten Verwendung stehen"
        assert stripped.index(series[0]) < stripped.index(series[1]) < stripped.index(series[2]), (
            f"{path.relative_to(REPO_ROOT)}:{start}: Die Reihenfolge der Dateien ist festgelegt"
        )


@pytest.mark.parametrize("path", OPERATIONS_DOCS, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_blocks_with_the_series_are_valid_bash(tmp_path: Path, path: Path) -> None:
    """Der ausgelieferte Wortlaut wird geparst, nicht die Arbeitsfassung."""
    bash = shutil.which("bash")
    assert bash, "bash nicht gefunden"
    for start, block in _bash_blocks(path):
        if '"${compose_files[@]}"' not in block:
            continue
        script = tmp_path / f"block-{path.stem}-{start}.sh"
        script.write_text(block + "\n", encoding="utf-8")
        result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (
            f"{path.relative_to(REPO_ROOT)}:{start}: Block ist keine gueltige Bash-Syntax\n{result.stderr}"
        )


def test_admin_guide_series_is_the_documented_order() -> None:
    """Pin auf die Quelle der Reihe — sie darf nicht still umsortiert werden."""
    assert _series_from_admin_guide() == [
        "compose_files=(-f docker-compose.prod.yml)",
        "[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)",
        "[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)",
    ]


def test_regex_helper_only_flags_web_recreating_calls() -> None:
    """Selbsttest des Erkenners — sonst waere ein gruener Lauf nichts wert."""
    assert _recreates_web(f"{_BASE_CALL} up -d")
    assert _recreates_web(f"{_BASE_CALL} up -d web")
    assert _recreates_web(f"{_BASE_CALL} up -d db web")
    assert _recreates_web(f"{_BASE_CALL} up -d web caddy")
    assert _recreates_web(f"{_BASE_CALL} up -d --scale redis=0")
    assert not _recreates_web(f"{_BASE_CALL} up -d db")
    assert not _recreates_web(f"{_BASE_CALL} up -d db clamav")
    assert not _recreates_web(f"{_BASE_CALL} down")
    assert not _recreates_web(f"{_BASE_CALL} restart web")
    assert not _recreates_web(f"{_BASE_CALL} stop cron")
    assert not _recreates_web(f"{_BASE_CALL} exec web python manage.py check")
    assert not _recreates_web(f"{_BASE_CALL} run --rm maintenance check_db_roles")
    assert not _recreates_web(f"{_BASE_CALL} ps")
    assert not _recreates_web(f"{_BASE_CALL} logs -f web")
    assert re.search(r"compose", _BASE_CALL)


def test_scanner_covers_short_reference_and_prose(tmp_path: Path) -> None:
    """Selbsttest der Erweiterung: ```text-Block und Fliesstext werden gesehen."""
    sample = tmp_path / "sample.md"
    sample.write_text(
        "Nach `" + _BASE_CALL + " up -d` laufen die Jobs.\n\n"
        "```text\nStack starten    " + _BASE_CALL + " up -d\n```\n\n"
        "```bash\n" + _BASE_CALL + " ps\n```\n",
        encoding="utf-8",
    )

    assert [line for _, line in _command_blocks(sample)] != []
    assert any(_recreates_web(line) for _, block in _command_blocks(sample) for line in block.splitlines())
    assert any(_recreates_web(span) for _, span in _prose_commands(sample))
    # Der Befehl im ```bash-Block ist kein Treffer — sonst waere der Selbsttest
    # blind fuer falsch-positive Erweiterungen.
    assert not any(_recreates_web(line) for _, block in _bash_blocks(sample) for line in block.splitlines())
