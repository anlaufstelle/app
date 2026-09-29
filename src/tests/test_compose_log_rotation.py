import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FILES = {
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "docker-compose.prod.yml",
}
OPTIONAL_FILES = {
    "docker-compose.staging.yml",
    "docker-compose.demo.yml",
}


def _tracked_compose_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "docker-compose*.yml"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {line for line in result.stdout.splitlines() if line}


def test_required_compose_files_are_tracked() -> None:
    assert _tracked_compose_files() >= REQUIRED_FILES


@pytest.mark.parametrize("filename", sorted(REQUIRED_FILES | OPTIONAL_FILES))
def test_every_service_uses_bounded_container_logs(filename: str) -> None:
    path = REPO_ROOT / filename
    if filename == "docker-compose.staging.yml" and not path.exists():
        pytest.skip("docker-compose.staging.yml is excluded from this release tree")
    if filename == "docker-compose.demo.yml" and not path.exists():
        pytest.skip("docker-compose.demo.yml is excluded from this release tree")
    tracked_files = _tracked_compose_files()
    if filename not in tracked_files:
        pytest.skip(f"{filename} is excluded from this release tree")

    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = compose.get("services", {})
    assert services, f"{filename} must define services"
    for service_name, service in services.items():
        logging = service.get("logging", {})
        assert logging.get("driver") in {"json-file", "local"}, f"{filename}:{service_name} has no supported log driver"
        options = logging.get("options", {})
        assert re.fullmatch(r"\d+[kmg]", str(options.get("max-size", ""))), (
            f"{filename}:{service_name} must set a bounded max-size"
        )
        max_file = options.get("max-file")
        assert isinstance(max_file, str) and max_file.isdigit() and int(max_file) >= 1, (
            f"{filename}:{service_name} must store max-file as a positive integer string"
        )
