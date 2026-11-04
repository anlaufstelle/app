"""Verify the demo gate without starting a browser or server."""

import ast
import json
import shlex
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
E2E_DIR = ROOT / "src/tests/e2e"
ALLOWLIST = ROOT / "src/tests/public_skip_allowlist.json"
DEMO_ENV = "E2E_DEMO_MODE=1"


def demo_gate_selectors(makefile_dir=ROOT) -> list[str]:
    """Spec selectors of the pytest call in ``make -n test-e2e-demo``."""
    result = subprocess.run(
        ["make", "-n", "test-e2e-demo"], cwd=makefile_dir, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    pytest_lines = [line for line in result.stdout.splitlines() if DEMO_ENV in line and "pytest" in line]
    assert len(pytest_lines) == 1, result.stdout
    words = shlex.split(pytest_lines[0])
    return [word for word in words[words.index("pytest") + 1 :] if word.startswith("src/tests/e2e/")]


def covered(nodeid: str, selectors: list[str]) -> bool:
    """A selector covers a nodeid when it names the file, the class or the test itself."""
    plain = nodeid.split("[", 1)[0]
    return any(plain == selector or plain.startswith(selector + "::") for selector in selectors)


def require_demo_call_sites() -> dict[str, str]:
    """Nodeid prefix per ``require_demo`` call site, keyed by its source location.

    A call inside a test function yields that test (with its class); a call
    inside any other function (a fixture or helper) is attributed to the whole
    file, because the gate cannot tell which tests use it without running them.
    """
    functions = ast.FunctionDef | ast.AsyncFunctionDef
    sites = {}
    for path in sorted(E2E_DIR.glob("test_*.py")):
        source = path.read_text()
        if "require_demo" not in source:
            continue
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(source)
        owners = [(node, None) for node in tree.body if isinstance(node, functions)]
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                owners += [(child, node.name) for child in node.body if isinstance(child, functions)]
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or ast.unparse(call.func) != "require_demo":
                continue
            owner = next(((f, c) for f, c in owners if f.lineno <= call.lineno <= f.end_lineno), None)
            location = f"{relative}:{call.lineno}"
            if owner is None or not owner[0].name.startswith("test"):
                sites[location] = relative
            else:
                function, class_name = owner
                sites[location] = "::".join(part for part in (relative, class_name, function.name) if part)
    return sites


def test_demo_target_is_locked_and_selects_demo_specs():
    result = subprocess.run(["make", "-n", "test-e2e-demo"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    for expected in (
        "flock",
        DEMO_ENV,
        "E2E_SEED_SCALE=demo",
        "E2E_PORT_BASE=8854",
        "test_demo_scenario_switch.py",
        "test_demo_offline_key_notice.py",
        "test_demo_offline_key_preparation.py",
        "test_i18n_locale.py::TestAnonymousLocale::test_demo_query_switch_error_and_start",
        "test_system_compliance_maintenance_results.py::TestDemoCards",
        "-rs",
    ):
        assert expected in result.stdout


def test_every_allowlisted_demo_skip_is_covered_by_the_demo_gate():
    """Refs #2679: a skip justified by the demo gate must actually run in the gate."""
    selectors = demo_gate_selectors()
    demo_nodeids = [
        entry["nodeid"] for entry in json.loads(ALLOWLIST.read_text()) if entry.get("requires_env") == DEMO_ENV
    ]
    assert demo_nodeids
    missing = [nodeid for nodeid in demo_nodeids if not covered(nodeid, selectors)]
    assert missing == [], f"Demo skips outside make test-e2e-demo: {missing}"


def test_every_require_demo_call_site_is_covered_by_the_demo_gate():
    """A demo spec that is not yet in the allowlist must still be part of the gate."""
    selectors = demo_gate_selectors()
    sites = require_demo_call_sites()
    assert len({site.split(":")[0] for site in sites}) >= 5
    missing = {location: nodeid for location, nodeid in sites.items() if not covered(nodeid, selectors)}
    assert missing == {}, f"require_demo call sites outside make test-e2e-demo: {missing}"


def test_demo_gate_selectors_exist():
    """A stale selector would make pytest error out before the first demo case."""
    for selector in demo_gate_selectors():
        file_part, *parts = selector.split("::")
        path = ROOT / file_part
        assert path.is_file(), selector
        names = {
            node.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert set(parts) <= names, selector


def test_covered_matches_file_class_and_test_but_not_siblings():
    selectors = ["src/tests/e2e/test_a.py", "src/tests/e2e/test_b.py::TestX", "src/tests/e2e/test_c.py::test_y"]
    assert covered("src/tests/e2e/test_a.py::TestAny::test_any[chromium]", selectors)
    assert covered("src/tests/e2e/test_b.py::TestX::test_any[chromium]", selectors)
    assert covered("src/tests/e2e/test_c.py::test_y[chromium]", selectors)
    assert not covered("src/tests/e2e/test_b.py::TestY::test_any[chromium]", selectors)
    assert not covered("src/tests/e2e/test_c.py::test_y_other[chromium]", selectors)
    assert not covered("src/tests/e2e/test_abc.py::test_any[chromium]", selectors)


def test_demo_gate_guard_turns_red_for_the_pre_2679_target(tmp_path):
    """The selector list before Refs #2679 left three specs outside the gate."""
    makefile = (ROOT / "Makefile").read_text()
    start = makefile.index("E2E_DEMO_SPECS := ")
    end = makefile.index("\n.PHONY: test-e2e-demo", start)
    old_specs = (
        "E2E_DEMO_SPECS := src/tests/e2e/test_demo_scenario_switch.py "
        "src/tests/e2e/test_system_compliance_maintenance_results.py::TestDemoCards"
    )
    (tmp_path / "Makefile").write_text(makefile[:start] + old_specs + makefile[end:])
    selectors = demo_gate_selectors(tmp_path)
    assert len(selectors) == 2
    uncovered = {nodeid for nodeid in require_demo_call_sites().values() if not covered(nodeid, selectors)}
    assert uncovered == {
        "src/tests/e2e/test_demo_offline_key_notice.py",
        "src/tests/e2e/test_demo_offline_key_preparation.py",
        "src/tests/e2e/test_i18n_locale.py::TestAnonymousLocale::test_demo_query_switch_error_and_start",
    }


def test_expected_demo_fails_for_broken_server(monkeypatch):
    from tests.e2e._profiles import require_demo

    monkeypatch.setenv("E2E_DEMO_MODE", "1")
    monkeypatch.setenv("E2E_SEED_SCALE", "demo")
    try:
        with pytest.raises(AssertionError, match="Demo server"):
            require_demo(lambda: False)
    except pytest.skip.Exception as error:
        pytest.fail(f"Expected demo was skipped: {error}")


def test_standard_profile_skips_without_probing(monkeypatch):
    from tests.e2e._profiles import require_demo

    monkeypatch.delenv("E2E_DEMO_MODE", raising=False)
    with pytest.raises(pytest.skip.Exception, match="demo profile"):
        require_demo(lambda: pytest.fail("Unexpected probe"))


def test_expected_demo_requires_demo_seed(monkeypatch):
    from tests.e2e._profiles import require_demo

    monkeypatch.setenv("E2E_DEMO_MODE", "1")
    monkeypatch.setenv("E2E_SEED_SCALE", "standard")
    with pytest.raises(AssertionError, match="E2E_SEED_SCALE"):
        require_demo(lambda: True)


def test_demo_dry_run_does_not_execute_flock(tmp_path):
    import os

    marker = tmp_path / "executed"
    executable = tmp_path / "flock"
    executable.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n")
    executable.chmod(0o755)
    result = subprocess.run(
        ["make", "-n", "test-e2e-demo"],
        cwd=ROOT,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), "Dry run must not execute the shared lock"


@pytest.mark.parametrize("port", ["8844", "8854"])
def test_e2e_origins_follow_server_port(port):
    import os
    import sys

    env = {**os.environ, "E2E_PORT_BASE": port, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e"}
    env.pop("EXTERNAL_ORIGIN", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; "
            f"assert settings.EXTERNAL_ORIGIN == 'http://localhost:{port}'; "
            f"assert 'http://localhost:{port}' in settings.OTP_WEBAUTHN_ALLOWED_ORIGINS",
        ],
        cwd=ROOT / "src",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_checklist_requires_demo_before_release_and_in_evidence():
    path = ROOT / "docs/dev/release-checklist.md"
    if not path.is_file():
        pytest.skip("docs/dev/release-checklist.md not in the public snapshot — guard only relevant in the dev repo")
    checklist = path.read_text()
    section = checklist.split("## 1.", 1)[1].split("## 2.", 1)[0]
    evidence = next(line for line in checklist.splitlines() if line.startswith("**Nachweise aus dem Pre-Release"))
    assert "make test-e2e-demo" in section
    assert "make test-e2e-demo" in evidence
