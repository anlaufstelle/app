"""Guard: ``.env.example`` and the changelog's operator actions name the same variables.

Forward: every environment variable an "Erforderliche Betreiber-Aktionen" section
asks the operator to handle must exist in ``.env.example`` (active or commented).
Reverse: every variable in ``.env.example`` must be mentioned in the changelog at
least once, so a newly added line cannot reach operators unannounced.

Both directions have an allowlist for justified exceptions; stale allowlist
entries fail as well, so the lists cannot silently outlive their reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPERATOR_SECTION = "Erforderliche Betreiber-Aktionen"
RELEASE_HEADING = re.compile(r"^## \[[^]]+\]")
ENV_NAME = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")
ENV_LINE = re.compile(r"^#?\s*([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)=", re.MULTILINE)

# Named in an operator action, but deliberately absent from ``.env.example``.
FORWARD_ALLOWLIST: dict[str, str] = {
    "MAINTENANCE_FLAG_FILE": "operators are told to REMOVE a host-path override; the default lives in the container",
}
# In ``.env.example`` without any changelog mention: Compose/runtime defaults that
# were never an operator action.
REVERSE_ALLOWLIST: dict[str, str] = {
    "DJANGO_SETTINGS_MODULE": "fixed by the image and the Compose files",
    "POSTGRES_HOST": "Compose-internal service name",
    "POSTGRES_PORT": "Compose-internal default",
    "GUNICORN_WORKERS": "tuning default, never required",
    "GUNICORN_TIMEOUT": "tuning default, never required",
    "DJANGO_HEALTH_BACKUP_REQUIRED": "optional health-check switch",
    "BACKUP_OFFSITE_TARGET": "optional backup target, documented in the runbook",
    "CLAMAV_HOST": "Compose-internal service name",
    "CLAMAV_PORT": "Compose-internal default",
}


def env_example_variables(text: str) -> set[str]:
    """Variable names of active and commented ``NAME=`` lines."""
    return set(ENV_LINE.findall(text))


def operator_action_variables(changelog: str) -> set[str]:
    """Backticked variable names inside any operator-actions section."""
    names: set[str] = set()
    in_section = False
    for line in changelog.splitlines():
        if RELEASE_HEADING.match(line):
            in_section = False
        elif line.startswith("### "):
            in_section = line == f"### {OPERATOR_SECTION}"
        elif in_section:
            names.update(ENV_NAME.findall(line))
    return names


def check(
    changelog: str,
    env_example: str,
    forward_allowlist: dict[str, str],
    reverse_allowlist: dict[str, str],
) -> list[str]:
    declared = env_example_variables(env_example)
    announced = operator_action_variables(changelog)
    mentioned = {name for name in declared if re.search(rf"\b{name}\b", changelog)}
    errors = [
        f"{name}: named as operator action but missing in .env.example"
        for name in sorted(announced - declared - set(forward_allowlist))
    ]
    errors += [
        f"{name}: in .env.example but never mentioned in the changelog"
        for name in sorted(declared - mentioned - set(reverse_allowlist))
    ]
    errors += [
        f"{name}: stale forward allowlist entry"
        for name in sorted(forward_allowlist)
        if name in declared or name not in announced
    ]
    errors += [
        f"{name}: stale reverse allowlist entry"
        for name in sorted(reverse_allowlist)
        if name in mentioned or name not in declared
    ]
    return errors


def test_env_example_and_operator_actions_agree() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert check(changelog, env_example, FORWARD_ALLOWLIST, REVERSE_ALLOWLIST) == []


CHANGELOG_PROBE = """## [Unreleased]

### Erforderliche Betreiber-Aktionen

- Setze `NEW_SETTING_PATH` in der `.env`.

## [1.0.0] - 2099-01-01

### Added

- Eintrag mit `NOT_AN_ACTION_NAME`.
"""


def test_operator_variable_missing_in_env_example_is_reported() -> None:
    errors = check(CHANGELOG_PROBE, "OTHER_VALUE=1\n", {}, {"OTHER_VALUE": "probe"})
    assert errors == ["NEW_SETTING_PATH: named as operator action but missing in .env.example"]


def test_variable_outside_operator_section_is_not_an_action() -> None:
    assert "NOT_AN_ACTION_NAME" not in operator_action_variables(CHANGELOG_PROBE)


def test_undocumented_env_example_variable_is_reported() -> None:
    errors = check(CHANGELOG_PROBE, "NEW_SETTING_PATH=1\nUNANNOUNCED_VALUE=2\n# COMMENTED_VALUE=3\n", {}, {})
    assert errors == [
        "COMMENTED_VALUE: in .env.example but never mentioned in the changelog",
        "UNANNOUNCED_VALUE: in .env.example but never mentioned in the changelog",
    ]


def test_allowlists_cover_exceptions_and_flag_stale_entries() -> None:
    env_example = "KNOWN_ONLY_HERE=1\n"
    assert check(CHANGELOG_PROBE, env_example, {"NEW_SETTING_PATH": "removed"}, {"KNOWN_ONLY_HERE": "default"}) == []
    stale = check(CHANGELOG_PROBE, "NEW_SETTING_PATH=1\n", {"NEW_SETTING_PATH": "now declared"}, {"GONE_VALUE": "x"})
    assert "NEW_SETTING_PATH: stale forward allowlist entry" in stale
    assert "GONE_VALUE: stale reverse allowlist entry" in stale


def test_check_runs_on_files_in_tmp_path(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG_PROBE, encoding="utf-8")
    (tmp_path / ".env.example").write_text("NEW_SETTING_PATH='x'\n", encoding="utf-8")
    errors = check(
        (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8"),
        (tmp_path / ".env.example").read_text(encoding="utf-8"),
        {},
        {},
    )
    assert errors == []
