"""Guard for the Actions policy of the public app repository.

The public repository only allows Actions created by GitHub (owners ``actions``
and ``github``) plus the explicitly selected third-party Actions below, and
requires every Action to be pinned to a full commit SHA
(``allowed_actions: selected``, ``sha_pinning_required: true``). A shipped
workflow that uses another Action, or one without a full SHA, would only fail
there, at the next release. This guard fails here instead.

``ALLOWED_THIRD_PARTY`` must match the selected Actions of the repository
setting; a new third-party Action needs both a change of the setting and of
this list. Workflows that the release build removes from the public tree
(``rm -f "$RELEASE_DIR/.github/workflows/<name>"`` in the release build script)
are dev-only and not subject to that policy, but still need full SHA pins.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
RELEASE_BUILD = REPO_ROOT / "dev-ops" / "release" / "build-release.sh"

# Owners whose Actions GitHub itself creates; the policy allows them as a group.
GITHUB_OWNERS = frozenset({"actions", "github"})
# The third-party Actions selected in the repository setting (owner/repo).
ALLOWED_THIRD_PARTY = frozenset(
    {
        "docker/build-push-action",
        "docker/login-action",
        "docker/setup-buildx-action",
        "docker/setup-qemu-action",
        "sigstore/cosign-installer",
    }
)
USES_RE = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)(?:/[^@\s]+)?@(?P<ref>\S+)$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REMOVED_WORKFLOW_RE = re.compile(r'rm -f\s+"\$RELEASE_DIR/\.github/workflows/(?P<name>[\w.-]+)"')


def workflow_uses(path: Path) -> list[str]:
    """Every ``uses:`` of a workflow: job-level (reusable workflows) and step-level."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = data.get("jobs") if isinstance(data, dict) else None
    assert isinstance(jobs, dict) and jobs, f"{path.name}: no jobs found — workflow format changed?"
    uses = []
    for job in jobs.values():
        if "uses" in job:
            uses.append(str(job["uses"]))
        uses += [str(step["uses"]) for step in job.get("steps") or [] if "uses" in step]
    return uses


def violations(uses: str, *, shipped: bool) -> list[str]:
    """Why a ``uses:`` reference breaks the policy; empty when it complies."""
    match = USES_RE.match(uses)
    if match is None:
        # Local actions (./...) and docker:// references are not covered by the policy check.
        return ["not an owner/repo@sha reference"]
    problems = []
    if not FULL_SHA_RE.match(match.group("ref")):
        problems.append("not pinned to a full commit SHA")
    action = f"{match.group('owner')}/{match.group('repo')}"
    if shipped and match.group("owner") not in GITHUB_OWNERS and action not in ALLOWED_THIRD_PARTY:
        problems.append(f"{action} is not allowed by the policy of the public repository")
    return problems


def removed_from_release() -> set[str]:
    if not RELEASE_BUILD.exists():
        pytest.skip("dev-ops/release/build-release.sh is stripped from the public snapshot")
    return set(REMOVED_WORKFLOW_RE.findall(RELEASE_BUILD.read_text(encoding="utf-8")))


def workflow_files() -> list[Path]:
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def shipped_workflows() -> list[Path]:
    # Without the dev-only build script, every remaining workflow is shipped.
    removed = removed_from_release() if RELEASE_BUILD.exists() else set()
    return [path for path in workflow_files() if path.name not in removed]


def _report(paths: list[Path], *, shipped: bool) -> list[str]:
    return [
        f"{path.name}: {uses} — {problem}"
        for path in paths
        for uses in workflow_uses(path)
        for problem in violations(uses, shipped=shipped)
    ]


class TestShippedWorkflowsFollowTheActionsPolicy:
    def test_inventory_is_not_empty(self):
        shipped = {path.name for path in shipped_workflows()}
        assert "release.yml" in shipped

    def test_release_exclusions_match_workflow_inventory(self):
        removed = removed_from_release()
        assert removed, "release build removes no workflow — pattern of the release build script changed?"
        assert removed <= {path.name for path in workflow_files()}, f"removed workflows not found: {removed}"

    def test_every_shipped_action_is_allowed_and_pinned(self):
        problems = _report(shipped_workflows(), shipped=True)
        assert not problems, "\n".join(problems)

    def test_every_dev_only_action_is_pinned(self):
        removed = removed_from_release()
        dev_only = [path for path in workflow_files() if path.name in removed]
        problems = _report(dev_only, shipped=False)
        assert not problems, "\n".join(problems)

    def test_every_selected_third_party_action_is_used(self):
        """A selected Action no shipped workflow uses means list and setting drifted apart."""
        used = set()
        for path in shipped_workflows():
            for uses in workflow_uses(path):
                match = USES_RE.match(uses)
                if match is not None:
                    used.add(f"{match.group('owner')}/{match.group('repo')}")
        assert used >= ALLOWED_THIRD_PARTY, f"selected but unused: {sorted(ALLOWED_THIRD_PARTY - used)}"


class TestGuardRejects:
    SHA = "a" * 40

    @pytest.mark.parametrize(
        "uses",
        [
            "actions/checkout@v6",
            "actions/checkout@" + "a" * 39,
            "actions/checkout@" + "A" * 40,
            "docker/login-action@main",
            "github/codeql-action/init@v4",
        ],
    )
    def test_reference_without_a_full_sha(self, uses):
        assert violations(uses, shipped=True) == ["not pinned to a full commit SHA"]

    @pytest.mark.parametrize(
        "uses",
        ["peter-evans/create-issue-from-file", "docker/metadata-action", "sigstore/gitsign", "actions-x/checkout"],
    )
    def test_third_party_action_outside_the_list(self, uses):
        problems = violations(f"{uses}@{self.SHA}", shipped=True)
        assert problems == [f"{uses} is not allowed by the policy of the public repository"]

    def test_third_party_action_outside_the_list_is_fine_in_a_dev_only_workflow(self):
        assert violations(f"peter-evans/create-issue-from-file@{self.SHA}", shipped=False) == []

    @pytest.mark.parametrize("uses", ["./.github/actions/setup", "docker://alpine:3.22", "checkout@v6"])
    def test_reference_the_guard_does_not_cover(self, uses):
        assert violations(uses, shipped=True) == ["not an owner/repo@sha reference"]

    @pytest.mark.parametrize(
        "uses",
        [
            "actions/checkout@" + "a" * 40,
            "github/codeql-action/analyze@" + "b" * 40,
            "docker/build-push-action@" + "c" * 40,
            "sigstore/cosign-installer@" + "d" * 40,
            "actions/reusable/.github/workflows/build.yml@" + "e" * 40,
        ],
    )
    def test_allowed_and_pinned_reference_passes(self, uses):
        assert violations(uses, shipped=True) == []

    def test_job_level_reusable_workflow_is_collected(self, tmp_path):
        path = tmp_path / "wf.yml"
        path.write_text(
            "on: push\njobs:\n  call:\n    uses: other/repo/.github/workflows/x.yml@v1\n"
            "  run:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v6\n      - run: true\n",
            encoding="utf-8",
        )
        assert workflow_uses(path) == ["other/repo/.github/workflows/x.yml@v1", "actions/checkout@v6"]
        assert _report([path], shipped=True) == [
            "wf.yml: other/repo/.github/workflows/x.yml@v1 — not pinned to a full commit SHA",
            "wf.yml: other/repo/.github/workflows/x.yml@v1 — other/repo is not allowed by the policy of the public "
            "repository",
            "wf.yml: actions/checkout@v6 — not pinned to a full commit SHA",
        ]


class TestPublicWorkflowPolicy:
    @pytest.mark.parametrize(
        ("extra_use", "exit_code", "message"),
        [
            (None, 0, "passed"),
            ("other/unselected@" + "a" * 40, 1, "not allowed by the policy"),
            ("actions/checkout@v6", 1, "not pinned to a full commit SHA"),
        ],
        ids=["allowed", "unselected", "unpinned"],
    )
    def test_policy_checks_shipped_workflows_without_release_sources(self, tmp_path, extra_use, exit_code, message):
        """Run the shipped guard against a public tree, including policy violations."""
        guard = tmp_path / "src/tests/test_actions_policy_guard.py"
        guard.parent.mkdir(parents=True)
        guard.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
        workflows = tmp_path / ".github/workflows"
        workflows.mkdir(parents=True)
        uses = [f"{action}@{'a' * 40}" for action in sorted(ALLOWED_THIRD_PARTY)]
        if extra_use is not None:
            uses.append(extra_use)
        (workflows / "release.yml").write_text(
            "jobs:\n  release:\n    runs-on: ubuntu-latest\n    steps:\n"
            + "".join(f"      - uses: {use}\n" for use in uses),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "src/tests/test_actions_policy_guard.py::TestShippedWorkflowsFollowTheActionsPolicy",
                "-x",
                "-q",
                "-rs",
                "-p",
                "no:cacheprovider",
                "-o",
                "markers=architecture: source file checks",
            ],
            cwd=tmp_path,
            env={**os.environ, "PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert message in result.stdout, result.stdout
        if exit_code == 0:
            assert "3 passed, 2 skipped" in result.stdout, result.stdout
