import re
from pathlib import Path, PurePosixPath

import pytest
import yaml

pytestmark = pytest.mark.architecture

PROD_COMPOSE = Path("docker-compose.prod.yml")
RUNBOOKS = (Path("docs/ops-runbook.md"), Path("docs/en/ops-runbook.md"))


def _maintenance_default(compose: dict[str, object]) -> str:
    value = compose["services"]["web"]["environment"]["MAINTENANCE_FLAG_FILE"]
    match = re.fullmatch(r"\$\{MAINTENANCE_FLAG_FILE:-([^}]+)}", value)
    assert match is not None, "MAINTENANCE_FLAG_FILE must have a non-empty Compose default"
    return match.group(1)


def _maintenance_section(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("### 6.5a "))
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("### ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_default_flag_is_on_a_named_web_volume() -> None:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    flag_path = PurePosixPath(_maintenance_default(compose))
    named_volumes = set(compose.get("volumes", {}))
    matching_mounts: list[str] = []

    for mount in compose["services"]["web"].get("volumes", []):
        source, target, *_options = mount.split(":")
        target_path = PurePosixPath(target)
        if source in named_volumes and (flag_path == target_path or target_path in flag_path.parents):
            matching_mounts.append(target)

    assert matching_mounts, f"{flag_path} must be stored below a named web volume"


@pytest.mark.parametrize("runbook", RUNBOOKS)
def test_runbook_uses_the_container_flag_path_from_the_host(runbook: Path) -> None:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    flag_path = _maintenance_default(compose)
    section = _maintenance_section(runbook)

    assert f"exec -T web touch {flag_path}" in section
    assert f"exec -T web rm -f {flag_path}" in section
