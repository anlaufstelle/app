"""Deploy scripts must ship every repo file that the shipped shell scripts source.

Refs #2408: since the env-file reader moved into ``scripts/ops/lib/env-file.sh``,
the host-side backup, restore and drill scripts ``source`` it. A deploy script
that copies only the callers leaves the library behind, and the nightly
backup on the host dies with "No such file or directory".

The guard derives both sides from the scripts themselves: the transferred set
from the ``rsync``/``scp`` calls of the deploy script (local source -> remote
path under ``$REMOTE_DIR``), the required set from the ``source``/``.`` lines
of every transferred shell script, resolved against the script's remote
directory (``dirname "${BASH_SOURCE[0]}"``).
"""

import os
import posixpath
import re
import shlex
from fnmatch import fnmatch
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

DEPLOY_SCRIPTS = (
    "dev-ops/deploy/deploy-dev.sh",
    "dev-ops/deploy/barbara/deploy-barbara.sh",
    "dev-ops/deploy/deploy-demo.sh",
)

# Directories never worth walking (and excluded by every whole-tree sync).
_ALWAYS_PRUNED = {".git", ".venv", "venv", "node_modules", "__pycache__"}

_TRANSFER_CALL = re.compile(r"^\s*(rsync|scp)\s+(.*)$", re.MULTILINE)
_REMOTE_DEST = re.compile(r"^\$\{?\w+\}?:\$\{?REMOTE_DIR\}?(/.*)?$")
_SOURCE_LINE = re.compile(r"^\s*(?:source|\.)\s+(.*)$", re.MULTILINE)
_SCRIPT_DIR_DEF = re.compile(r'^\s*SCRIPT_DIR="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)"', re.MULTILINE)
_DIRNAME_SELF = re.compile(r'\$\(dirname "?\$\{BASH_SOURCE\[0\]\}"?\)')
_SCRIPT_DIR_REF = re.compile(r"\$\{SCRIPT_DIR\}|\$SCRIPT_DIR\b")


def _flat(path: Path) -> str:
    """Script without comment lines, line continuations joined."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(lines).replace("\\\n", " ")


def _excluded(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("/"):
            anchored = pattern.lstrip("/")
            if rel == anchored or rel.startswith(anchored + "/"):
                return True
        elif any(fnmatch(part, pattern) for part in rel.split("/")):
            return True
    return False


def _walk(local: Path, patterns: list[str]):
    """Yield (path relative to ``local``, file) for every file rsync would copy."""
    for root, dirs, files in os.walk(local):
        rel_root = Path(root).relative_to(local).as_posix()
        rel_root = "" if rel_root == "." else rel_root
        dirs[:] = [d for d in dirs if d not in _ALWAYS_PRUNED and not _excluded(posixpath.join(rel_root, d), patterns)]
        for name in files:
            rel = posixpath.join(rel_root, name)
            if not _excluded(rel, patterns):
                yield rel, Path(root) / name


def _transferred(script: Path) -> dict[str, Path]:
    """Map remote path (relative to ``$REMOTE_DIR``) -> local file, per rsync semantics."""
    script_dir = script.parent.as_posix()
    flat = _flat(script)
    remote: dict[str, Path] = {}
    calls = _TRANSFER_CALL.findall(flat)
    assert calls, f"{script}: no rsync/scp call found — the guard cannot derive the transferred set"
    for _tool, args in calls:
        args = args.replace("${SCRIPT_DIR}", script_dir).replace("$SCRIPT_DIR", script_dir)
        args = args.replace("${REPO_ROOT}", ".").replace("$REPO_ROOT", ".")
        tokens = shlex.split(args)
        excludes = [t.split("=", 1)[1] for t in tokens if t.startswith("--exclude=")]
        operands = [t for t in tokens if not t.startswith("-")]
        dest_match = _REMOTE_DEST.match(operands[-1])
        assert dest_match, f"{script}: transfer target {operands[-1]!r} is not under $REMOTE_DIR"
        dest = (dest_match.group(1) or "/").strip("/")
        for src in operands[:-1]:
            local = Path(src)
            assert local.exists(), f"{script}: transfer source {src!r} does not exist"
            if local.is_dir():
                # rsync: "dir/" copies the contents, "dir" copies the directory itself.
                base = dest if src.endswith("/") else posixpath.join(dest, local.name)
                for rel, file in _walk(local, excludes):
                    remote[posixpath.normpath(posixpath.join(base, rel))] = file
            else:
                remote[posixpath.normpath(posixpath.join(dest, local.name))] = local
    return remote


def _sourced(remote_path: str, local: Path) -> list[str]:
    """Remote paths (relative to ``$REMOTE_DIR``) that ``local`` sources when run from ``remote_path``."""
    text = _flat(local)
    remote_dir = posixpath.dirname(remote_path) or "."
    has_script_dir = bool(_SCRIPT_DIR_DEF.search(text))
    required = []
    for raw in _SOURCE_LINE.findall(text):
        # shlex joins the nested quoting of "$(dirname "${BASH_SOURCE[0]}")/..".
        arg = shlex.split(raw)[0]
        arg = _DIRNAME_SELF.sub(remote_dir, arg)
        if has_script_dir:
            arg = _SCRIPT_DIR_REF.sub(remote_dir, arg)
        assert "$" not in arg, (
            f"{local}: cannot resolve sourced path {raw!r} — extend the guard or "
            'source relative to dirname "${BASH_SOURCE[0]}"'
        )
        if arg.startswith("/"):
            continue  # system file, not shipped from the repo
        required.append(posixpath.normpath(arg))
    return required


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS)
def test_deploy_ships_every_sourced_repo_file(script: str) -> None:
    path = Path(script)
    if not path.exists():
        pytest.skip(f"{script}: dev-ops/ (incl. dev-ops/deploy/deploy-dev.sh) is stripped from the public snapshot")
    remote = _transferred(path)
    missing = sorted(
        f"{remote_path} sources {needed}"
        for remote_path, local in remote.items()
        if remote_path.endswith(".sh")
        for needed in _sourced(remote_path, local)
        if needed not in remote
    )
    assert not missing, (
        f"{script} ships scripts whose sourced files stay behind on the host:\n  "
        + "\n  ".join(missing)
        + "\nTransfer them to the same path under $REMOTE_DIR."
    )


def _demo_deploy_text() -> str:
    path = Path("dev-ops/deploy/deploy-demo.sh")
    if not path.exists():
        pytest.skip("dev-ops/deploy/deploy-demo.sh is stripped from the public snapshot")
    return path.read_text(encoding="utf-8")


def _demo_audit_unit(kind: str) -> str:
    match = re.search(
        rf"^sudo tee /etc/systemd/system/anlaufstelle-audit-verify\.{kind} >[^\n]*<<'UNIT'\n(.*?)^UNIT$",
        _demo_deploy_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"Demo deploy must install anlaufstelle-audit-verify.{kind}"
    return match.group(1)


def _shared_audit_timer() -> list[str]:
    path = Path("dev-ops/deploy/install-timers.sh")
    if not path.exists():
        pytest.skip("dev-ops/deploy/install-timers.sh is stripped from the public snapshot")
    audit_call = next(line for line in _flat(path).splitlines() if line.startswith("install_timer audit-verify "))
    return shlex.split(audit_call)


def test_demo_audit_service_uses_demo_admin_stack() -> None:
    _demo_deploy_text()
    command = _shared_audit_timer()[-1]
    assert command == "$ADMIN_RUN verify_audit_chain"
    reseed = Path("dev-ops/deploy/demo-reseed.sh").read_text(encoding="utf-8")
    compose = re.search(r'^COMPOSE="(.*?)"$', reseed, re.MULTILINE)
    assert compose
    assert '$COMPOSE run --rm -T maintenance "$job"' in reseed
    service = _demo_audit_unit("service")
    assert "Type=oneshot\n" in service
    assert "After=docker.service\n" in service
    assert "Requires=docker.service\n" in service
    assert "WorkingDirectory=/opt/anlaufstelle\n" in service
    expected = f"/usr/bin/{compose.group(1)} run --rm -T maintenance {command.split()[-1]}"
    assert f"ExecStart={expected}\n" in service


def test_demo_audit_timer_matches_shared_schedule() -> None:
    _demo_deploy_text()
    schedule = _shared_audit_timer()[2]
    assert schedule == "*-*-* 03:30:00"
    timer = _demo_audit_unit("timer")
    assert f"OnCalendar={schedule}\n" in timer
    assert "Persistent=true\n" in timer
    assert "WantedBy=timers.target\n" in timer


def test_demo_audit_timer_is_installed_idempotently() -> None:
    text = _demo_deploy_text()
    service = text.index("sudo tee /etc/systemd/system/anlaufstelle-audit-verify.service")
    timer = text.index("sudo tee /etc/systemd/system/anlaufstelle-audit-verify.timer")
    reload = text.index("sudo systemctl daemon-reload", timer)
    enable = text.index("sudo systemctl enable --now anlaufstelle-audit-verify.timer", reload)
    assert text.index("$COMPOSE up -d --remove-orphans") < service < timer < reload < enable
    assert enable < text.index("$COMPOSE ps")


def test_demo_deploy_runs_audit_verification_synchronously_with_warning() -> None:
    text = _demo_deploy_text()
    start = text.index("sudo systemctl start anlaufstelle-audit-verify.service")
    enable = text.index("sudo systemctl enable --now anlaufstelle-audit-verify.timer")
    assert enable < start < text.index("$COMPOSE ps")
    assert text.count("systemctl start anlaufstelle-audit-verify.service") == 1
    assert "--no-block" not in text
    # Success, warning and infrastructure failure are exercised by command doubles.
