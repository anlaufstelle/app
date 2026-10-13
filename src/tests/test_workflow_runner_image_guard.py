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
    directory = Path(__file__).resolve().parents[2] / ".github/workflows"
    workflows = sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])
    assert workflows, "No workflow files found"
    for path in workflows:
        workflow = yaml.safe_load(path.read_text())
        assert not any(label.endswith("-latest") for label in _runner_labels(workflow)), path.name
        # Matrix runner labels live outside runs-on itself.
        for job in workflow.get("jobs", {}).values():
            matrix = job.get("strategy", {}).get("matrix", {})
            assert not any(label.endswith("-latest") for label in _labels(matrix)), path.name


@pytest.mark.parametrize(
    "declaration",
    ["runs-on: ubuntu-latest", "runs-on: [self-hosted, ubuntu-latest]", "runs-on: {labels: [ubuntu-latest]}"],
)
def test_runner_label_reader_handles_yaml_forms(declaration):
    assert "ubuntu-latest" in set(_runner_labels(yaml.safe_load(declaration)))


@pytest.mark.architecture
@pytest.mark.parametrize("extension", ["yml", "yaml"])
@pytest.mark.parametrize("label", ["ubuntu-latest", "windows-latest", "macos-latest"])
@pytest.mark.parametrize("matrix", [False, True], ids=["direct", "matrix"])
def test_workflow_guard_rejects_latest_runner_fixtures(tmp_path, monkeypatch, extension, label, matrix):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "pinned.yml").write_text("jobs: {test: {runs-on: ubuntu-24.04}}")
    job = {"runs-on": label}
    if matrix:
        job = {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": [label]}}}
    (workflows / f"floating.{extension}").write_text(yaml.safe_dump({"jobs": {"test": job}}))
    monkeypatch.setattr(Path, "resolve", lambda self: tmp_path / "src" / "tests" / "guard.py")
    with pytest.raises(AssertionError, match=f"floating.{extension}"):
        test_workflows_use_pinned_runner_images()
