"""Bound explicit and Playwright-managed apt invocations in GitHub workflows."""

import re
from pathlib import Path

import pytest
import yaml


@pytest.mark.architecture
def test_apt_steps_have_deadlines_retries_and_network_timeouts():
    workflows = sorted((Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.yml"))
    assert workflows
    invocations = 0
    for path in workflows:
        workflow = yaml.safe_load(path.read_text())
        for job in workflow.get("jobs", {}).values():
            for step in job.get("steps", []):
                run = step.get("run", "")
                playwright_apt = re.search(r"\bplaywright\s+(?:install-deps\b|install\b[^\n;&|]*--with-deps\b)", run)
                if "apt-get" not in run and not playwright_apt:
                    continue
                assert 0 < step.get("timeout-minutes", 0) <= 10, (path.name, step)
                commands = re.findall(r"\bapt-get\b([^\n;&|]*)", run)
                assert commands or playwright_apt
                for command in commands:
                    invocations += 1
                    for option in ("Acquire::Retries=3", "Acquire::http::Timeout=30", "Acquire::https::Timeout=30"):
                        assert f"-o {option}" in command, (path.name, command, option)
    assert invocations
