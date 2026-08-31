"""CI-Guard gegen ungepinnte Python-Tool-Installationen (Refs #2063)."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

WORKFLOW_ROOT = Path(".github/workflows")
LOCK_FILES = {"requirements.txt", "requirements-dev.txt"}
DEV_LOCK_TOOLS = {
    "click",
    "django-stubs",
    "mypy",
    "pip-audit",
    "pip-licenses",
    "pip-tools",
}
_RUN_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:\s*(?P<body>.*)$")
_INSTALL = re.compile(r"\b(?:(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?pip|pipx|uv\s+pip)\s+install\b")
_SHELL_VARIABLE = re.compile(r"^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$")
_PACKAGE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^]]+\])?"
    r"(?P<specifier>===|==|~=|!=|<=|>=|<|>)?.*$"
)


def _run_blocks(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Liefere Shell-Text und nummerierte Zeilen jedes ``run:``-Blocks."""
    lines = text.splitlines()
    blocks: list[tuple[str, list[tuple[int, str]]]] = []
    index = 0
    while index < len(lines):
        match = _RUN_KEY.match(lines[index])
        if match is None:
            index += 1
            continue

        body = match.group("body")
        numbered_lines: list[tuple[int, str]] = []
        if body not in {"|", "|-", ">", ">-"}:
            numbered_lines.append((index + 1, body.strip()))
            index += 1
        else:
            block_indent = len(match.group("indent"))
            index += 1
            while index < len(lines):
                line = lines[index]
                if line.strip() and len(line) - len(line.lstrip()) <= block_indent:
                    break
                numbered_lines.append((index + 1, line.strip()))
                index += 1

        blocks.append(("\n".join(line for _, line in numbered_lines), numbered_lines))
    return blocks


def _install_arguments(line: str) -> tuple[str, list[str]] | None:
    match = _INSTALL.search(line)
    if match is None:
        return None

    command = line[match.start() :].strip()
    try:
        arguments = shlex.split(line[match.end() :], comments=True, posix=True)
    except ValueError:
        arguments = line[match.end() :].split()
    for position, argument in enumerate(arguments):
        if argument in {"&&", "||", ";", "|"}:
            arguments = arguments[:position]
            break
    return command, arguments


def ungepinnte_installs(text: str) -> list[str]:
    """Finde ``pip install``-Varianten ohne Lock- oder exakte Versionsbindung."""
    findings: list[str] = []
    for block_text, numbered_lines in _run_blocks(text):
        for line_number, line in numbered_lines:
            installation = _install_arguments(line)
            if installation is None:
                continue
            command, arguments = installation

            lock_install = len(arguments) == 2 and arguments[0] == "-r" and arguments[1] in LOCK_FILES
            package_arguments = [argument for argument in arguments if not argument.startswith("-")]
            exact_pins = bool(package_arguments) and all("==" in argument for argument in package_arguments)
            lock_variables = (
                "requirements-dev.txt" in block_text
                and bool(package_arguments)
                and all(_SHELL_VARIABLE.fullmatch(argument) for argument in package_arguments)
            )
            if not (lock_install or exact_pins or lock_variables):
                findings.append(f"Zeile {line_number}: {command}")
    return findings


def lock_bindungsfehler(text: str) -> list[str]:
    """Finde Dev-Werkzeuge mit hartkodierter Version oder ohne Dev-Lock-Bezug."""
    findings: list[str] = []
    for block_text, numbered_lines in _run_blocks(text):
        for line_number, line in numbered_lines:
            installation = _install_arguments(line)
            if installation is None:
                continue
            _, arguments = installation
            for argument in arguments:
                package = _PACKAGE.fullmatch(argument)
                if package is None or package.group("name").lower() not in DEV_LOCK_TOOLS:
                    continue
                name = package.group("name").lower()
                if package.group("specifier") is not None:
                    findings.append(f"Zeile {line_number}: {name} hat eine hartkodierte Versionsangabe")
                if "requirements-dev.txt" not in block_text:
                    findings.append(f"Zeile {line_number}: {name} ist nicht an den Dev-Lock gebunden")
    return findings


def test_ungepinnte_installs_erlaubt_alle_lock_und_pin_formen() -> None:
    text = """\
      - run: pip install -r requirements.txt
      - run: pip install -r requirements-dev.txt
      - run: pip install ruff==0.15.22 pytest==9.1.1
      - name: Tool aus Dev-Lock installieren
        run: |
          PT=$(grep -oE '^pip-tools==[0-9.]+' requirements-dev.txt)
          pip install "$PT"
    """

    assert ungepinnte_installs(text) == []
    assert lock_bindungsfehler(text) == []


def test_ungepinnte_installs_und_lock_drift_werden_gemeldet() -> None:
    text = """\
      - run: pip install pip-audit
      - run: pip install mypy==2.3.0
    """

    assert ungepinnte_installs(text) == ["Zeile 1: pip install pip-audit"]
    assert lock_bindungsfehler(text) == [
        "Zeile 1: pip-audit ist nicht an den Dev-Lock gebunden",
        "Zeile 2: mypy hat eine hartkodierte Versionsangabe",
        "Zeile 2: mypy ist nicht an den Dev-Lock gebunden",
    ]


def test_workflows_installieren_ci_tools_nur_aus_pins() -> None:
    workflows = sorted(WORKFLOW_ROOT.glob("*.yml"))
    if not workflows:
        pytest.skip("Kein Workflow-Verzeichnis oder keine Workflow-Dateien vorhanden")

    findings: list[str] = []
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        findings.extend(f"{workflow}:{finding}" for finding in ungepinnte_installs(text))
        findings.extend(f"{workflow}:{finding}" for finding in lock_bindungsfehler(text))

    assert not findings, "Ungepinnte oder driftende CI-Tool-Installation (Refs #2063):\n" + "\n".join(findings)
