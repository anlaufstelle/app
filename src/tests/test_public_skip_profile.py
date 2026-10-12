"""Exercise the public skip policy through pytest's command line."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def run_profile(tmp_path, allowed, profile="public", collection_skip=False, source=None, show_skips=True):
    test_file = tmp_path / "test_sample.py"
    source = source or 'import pytest\ndef test_sample(): pytest.skip("Absent dev docs")\n'
    if collection_skip:
        source = 'import pytest\npytest.skip("Absent dev docs", allow_module_level=True)\n'
    test_file.write_text(source)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(allowed))
    inventory = tmp_path / "inventory.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            str(test_file),
            "--rootdir",
            str(tmp_path),
            "-p",
            "tests._skip_profile",
            "--test-profile",
            profile,
            "--skip-allowlist",
            str(policy),
            "--skip-inventory",
            str(inventory),
            "-q",
            "-rs" if show_skips else "-rN",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    return result, inventory


def entry(nodeid="test_sample.py::test_sample", profile="public"):
    return {
        "nodeid": nodeid,
        "profile": profile,
        "reason": "Absent dev docs",
        "exclusion": "docs/dev",
        "rationale": "Maintainer documentation is stripped",
    }


def test_unknown_skip_fails_and_writes_inventory(tmp_path):
    result, inventory = run_profile(tmp_path, [])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Unapproved skip" in result.stdout
    assert json.loads(inventory.read_text())[0]["reason"] == "Absent dev docs"


def test_exact_public_skip_is_allowed_without_dev_documentation(tmp_path):
    result, _ = run_profile(tmp_path, [entry()])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("allowed", [[entry("::other_test")], [entry(profile="dev")]])
def test_reason_alone_cannot_authorize_another_node_or_profile(tmp_path, allowed):
    result, _ = run_profile(tmp_path, allowed)
    assert result.returncode == 1, result.stdout + result.stderr


def test_collection_skip_also_requires_approval(tmp_path):
    result, inventory = run_profile(tmp_path, [], collection_skip=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Unapproved skip" in result.stdout
    assert json.loads(inventory.read_text())[0]["reason"] == "Absent dev docs"


def test_incomplete_exclusion_evidence_fails_closed(tmp_path):
    allowed = entry()
    del allowed["exclusion"]
    result, _ = run_profile(tmp_path, [allowed])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Public skip policy unavailable" in result.stdout


def test_export_defaults_to_public_and_reports_skip_reasons(tmp_path):
    (tmp_path / "docker-compose.prod.yml").write_text("services: {}\n")
    plugin = tmp_path / "src/tests/export_profile.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text((ROOT / "src/tests/_skip_profile.py").read_text())
    plugin.with_name("public_skip_allowlist.json").write_text("[]\n")
    test_file = tmp_path / "test_export.py"
    test_file.write_text('import pytest\ndef test_export(): pytest.skip("Absent dev docs")\n')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            str(test_file),
            "-p",
            "export_profile",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(plugin.parent), str(ROOT / "src")))},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Unapproved skip [public]" in result.stdout
    assert "SKIPPED [1]" in result.stdout


def test_teardown_skip_cannot_hide_an_unapproved_call_skip(tmp_path):
    allowed = entry()
    allowed["reason"] = "Allowed teardown"
    source = (
        "import pytest\n@pytest.fixture\ndef cleanup():\n    yield\n"
        '    pytest.skip("Allowed teardown")\ndef test_sample(cleanup):\n'
        '    pytest.skip("Unapproved call")\n'
    )
    result, inventory = run_profile(tmp_path, [allowed], source=source)
    assert result.returncode == 1, result.stdout + result.stderr
    assert len(json.loads(inventory.read_text())) == 2


def test_worker_does_not_write_the_shared_inventory(tmp_path):
    from types import SimpleNamespace

    from tests._skip_profile import SkipInventory

    inventory = tmp_path / "inventory.json"
    config = SimpleNamespace(
        workerinput={"workerid": "gw0"},
        getoption=lambda name: inventory if name == "skip_inventory" else "dev",
    )
    SkipInventory(config).pytest_sessionfinish(SimpleNamespace(config=config))
    assert not inventory.exists()


def test_present_exclusion_cannot_authorize_a_skip(tmp_path):
    (tmp_path / "docs/dev").mkdir(parents=True)
    result, _ = run_profile(tmp_path, [entry()])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Skip prerequisite is present" in result.stdout


@pytest.fixture
def env_entry(monkeypatch):
    monkeypatch.delenv("SKIP_PROFILE_OPT_IN", raising=False)
    allowed = entry()
    del allowed["exclusion"]
    allowed["requires_env"] = "SKIP_PROFILE_OPT_IN=1"
    allowed["rationale"] = "This test requires the SKIP_PROFILE_OPT_IN=1 opt-in gate."
    return allowed


@pytest.mark.parametrize("value", [None, "0"])
def test_env_gated_skip_is_allowed_without_the_required_value(tmp_path, monkeypatch, env_entry, value):
    if value is not None:
        monkeypatch.setenv("SKIP_PROFILE_OPT_IN", value)
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 0, result.stdout + result.stderr


def test_present_env_prerequisite_cannot_authorize_a_skip(tmp_path, monkeypatch, env_entry):
    monkeypatch.setenv("SKIP_PROFILE_OPT_IN", "1")
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Skip prerequisite is present: requires_env SKIP_PROFILE_OPT_IN=1" in result.stdout
    assert "Unapproved skip [public]" in result.stdout
    assert "Public skip policy unavailable" not in result.stdout


def test_both_skip_prerequisites_make_the_policy_unavailable(tmp_path, env_entry):
    env_entry["exclusion"] = "docs/dev"
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Public skip policy unavailable" in result.stdout


def test_neither_skip_prerequisite_makes_the_policy_unavailable(tmp_path, env_entry):
    # Prove valid environment evidence is accepted before testing its absence.
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 0, result.stdout + result.stderr
    del env_entry["requires_env"]
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Public skip policy unavailable" in result.stdout


@pytest.mark.parametrize("requires_env", ["SKIP_PROFILE_OPT_IN", "=1", "SKIP_PROFILE_OPT_IN=", "", None, 1])
def test_malformed_env_prerequisite_makes_the_policy_unavailable(tmp_path, env_entry, requires_env):
    # A blanket rejection of environment entries must not satisfy this test.
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 0, result.stdout + result.stderr
    env_entry["requires_env"] = requires_env
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Public skip policy unavailable" in result.stdout


@pytest.mark.parametrize("key, value", [("nodeid", "::other_test"), ("profile", "dev"), ("reason", "Other reason")])
def test_env_prerequisite_cannot_authorize_another_node_profile_or_reason(tmp_path, env_entry, key, value):
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 0, result.stdout + result.stderr
    env_entry[key] = value
    result, _ = run_profile(tmp_path, [env_entry])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Unapproved skip [public]" in result.stdout
    assert "Public skip policy unavailable" not in result.stdout


def test_bare_test_copy_keeps_the_dev_profile(tmp_path):
    plugin = tmp_path / "src/tests/export_profile.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text((ROOT / "src/tests/_skip_profile.py").read_text())
    test_file = tmp_path / "test_bare.py"
    test_file.write_text('import pytest\ndef test_bare(): pytest.skip("Diagnostic prerequisite")\n')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            str(test_file),
            "-p",
            "export_profile",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(plugin.parent), str(ROOT / "src")))},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public skip policy unavailable" not in result.stdout


@pytest.mark.parametrize("profile", ["dev", "public"])
@pytest.mark.parametrize("kind", ["strict_xfail", "xfail", "runtime_xfail", "xpass", "strict_xpass"])
def test_expected_failures_are_not_skip_inventory_entries(tmp_path, profile, kind):
    marks = {
        "strict_xfail": '@pytest.mark.xfail(strict=True, reason="Known failure")',
        "xfail": '@pytest.mark.xfail(strict=False, reason="Known failure")',
        "runtime_xfail": "",
        "xpass": '@pytest.mark.xfail(strict=False, reason="Known failure")',
        "strict_xpass": '@pytest.mark.xfail(strict=True, reason="Known failure")',
    }
    body = 'pytest.xfail("Known failure")' if kind == "runtime_xfail" else f"assert {kind.endswith('xpass')}"
    source = f"import pytest\n{marks[kind]}\ndef test_sample():\n    {body}\n"
    result, inventory = run_profile(tmp_path, [], profile=profile, source=source)
    assert result.returncode == (1 if kind == "strict_xpass" else 0), result.stdout + result.stderr
    assert "INTERNALERROR" not in result.stdout + result.stderr
    assert json.loads(inventory.read_text()) == []


@pytest.mark.parametrize("profile", ["dev", "public"])
def test_skip_report_without_location_is_reported_loudly(tmp_path, profile):
    """A skipped report whose ``longrepr`` is not the usual location tuple must not vanish.

    The inventory reads reason and location from that tuple; before this
    guard such a report was dropped silently, so the skip was neither listed
    nor approved.
    """
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n"
        "    report = outcome.get_result()\n"
        "    if report.skipped and call.when == 'call':\n"
        "        report.longrepr = 'skip without location tuple'\n"
    )
    # Without ``-rs``: pytest's own skip summary asserts the tuple and would
    # abort before the policy plugin reports.
    result, _ = run_profile(tmp_path, [], profile=profile, show_skips=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Unreadable skip report" in result.stdout
    assert "test_sample.py::test_sample" in result.stdout
