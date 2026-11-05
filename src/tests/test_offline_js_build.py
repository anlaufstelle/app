"""Public build/check and delivered asset contracts for the offline stack."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "crypto",
    "offline-db",
    "offline-bundles",
    "offline-revocations",
    "offline-store",
    "offline-queue",
    "offline-client",
    "offline-edit",
    "sync-orchestrator",
)


def run_build(root, *args):
    return subprocess.run(
        [sys.executable, str(root / "scripts/minify_offline_js.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_committed_assets_match_build():
    result = run_build(ROOT, "--check")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def build_tree(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/minify_offline_js.py", tmp_path / "scripts/minify_offline_js.py")
    (tmp_path / "node_modules").symlink_to(ROOT / "node_modules", target_is_directory=True)
    assets = tmp_path / "src/static/js"
    assets.mkdir(parents=True)
    for name in SOURCES:
        shutil.copyfile(ROOT / f"src/static/js/{name}.js", assets / f"{name}.js")
    return tmp_path


def test_build_is_reproducible_and_smaller(build_tree):
    first = run_build(build_tree)
    assert first.returncode == 0, first.stderr
    assets = build_tree / "src/static/js"
    before = {name: (assets / f"{name}.min.js").read_bytes() for name in SOURCES}
    second = run_build(build_tree)
    assert second.returncode == 0, second.stderr
    assert before == {name: (assets / f"{name}.min.js").read_bytes() for name in SOURCES}
    for name, content in before.items():
        assert len(content) < (assets / f"{name}.js").stat().st_size
    assert run_build(build_tree, "--check").returncode == 0


@pytest.mark.parametrize("drift", ["source", "artifact", "missing"])
def test_check_rejects_drift_without_writing(build_tree, drift):
    assert run_build(build_tree).returncode == 0
    source = build_tree / "src/static/js/offline-store.js"
    artifact = source.with_suffix(".min.js")
    if drift == "source":
        source.write_bytes(source.read_bytes() + b"\nwindow.buildDrift = true;\n")
    elif drift == "artifact":
        artifact.write_bytes(artifact.read_bytes() + b"\n// drift\n")
    else:
        artifact.unlink()
    before = artifact.read_bytes() if artifact.exists() else None
    result = run_build(build_tree, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "offline-store.min.js" in result.stdout + result.stderr
    assert (artifact.read_bytes() if artifact.exists() else None) == before


def test_delivered_crypto_exports_key_epoch():
    """Refs #2688: the pages ship the minified crypto session, so the key epoch
    that offline views bind open drafts to must be part of that artifact. A
    stale artifact silently disables the binding even with an updated source."""
    templates = ROOT / "src/templates"
    sources = {path.relative_to(templates).as_posix(): path.read_text() for path in templates.rglob("*.html")}
    loaders = sorted(name for name, text in sources.items() if "js/crypto.min.js" in text)
    assert loaders, "no template loads crypto.min.js"
    assert not [name for name, text in sources.items() if "'js/crypto.js'" in text or '"js/crypto.js"' in text]
    assert "keyEpoch" in (ROOT / "src/static/js/crypto.min.js").read_text()
