"""Keep GitHub runner image upgrades deliberate."""

from pathlib import Path

import pytest
import yaml


def _runner_labels(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "runs-on":
                yield from _labels(child)
            yield from _runner_labels(child)
    elif isinstance(value, list):
        for child in value:
            yield from _runner_labels(child)


def _labels(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _labels(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _labels(child)


@pytest.mark.architecture
def test_workflows_use_pinned_runner_images():
    workflows = sorted((Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.yml"))
    assert workflows, "No workflow files found"
    for path in workflows:
        workflow = yaml.safe_load(path.read_text())
        assert "ubuntu-latest" not in set(_runner_labels(workflow)), path.name
        # Matrix runner labels live outside runs-on itself.
        for job in workflow.get("jobs", {}).values():
            matrix = job.get("strategy", {}).get("matrix", {})
            assert "ubuntu-latest" not in set(_labels(matrix)), path.name


@pytest.mark.parametrize(
    "declaration",
    ["runs-on: ubuntu-latest", "runs-on: [self-hosted, ubuntu-latest]", "runs-on: {labels: [ubuntu-latest]}"],
)
def test_runner_label_reader_handles_yaml_forms(declaration):
    assert "ubuntu-latest" in set(_runner_labels(yaml.safe_load(declaration)))
