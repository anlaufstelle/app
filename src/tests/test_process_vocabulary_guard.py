"""Reject new internal process references in release files (Refs #2647)."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROCESS_WORDS = (
    "lane",
    "standardblock",
    "briefing",
    "besitzliste",
    "tabu",
    "orchestrator",
    "ruling",
    "fix-runde",
)

# Match inflections, but preserve unrelated words such as Tabulator.
_WORD_RE = re.compile(
    r"\btabu(?!l)\w*|\b(?:"
    + "|".join(re.escape(word) for word in _PROCESS_WORDS if word != "tabu")
    + r")\w*|\bWelle [A-Z]\b|/work/|\bCLAUDE\.md\b",
    re.IGNORECASE,
)

# Mask only the product token; other references on the same line remain visible.
_SYNC_ORCHESTRATOR_RE = re.compile(r"sync[-_]?orchestrator", re.IGNORECASE)
_DEFAULT_EXCLUDED_DIR_NAMES = frozenset({"staticfiles", "media", "__pycache__", "node_modules", ".git"})
_DEFAULT_EXTENSIONS = None
_BASELINE_PATH = Path(__file__).with_name("process_vocabulary_baseline.json")
# These files necessarily contain scanner patterns or forbidden-path examples.
_ALLOWLIST = {
    "src/tests/test_process_vocabulary_guard.py": "Scanner implementation and fixtures",
    "src/tests/process_vocabulary_baseline.json": "Frozen scanner findings",
    ".gitignore": "Local-only paths must be ignored",
    "scripts/verify_release_test_guard.py": "Release exclusion scanner",
}


class Hit(NamedTuple):
    relpath: str
    lineno: int
    word: str
    line: str


def _candidate_files(root: Path) -> list[Path]:
    """Return tracked files, or regular files outside a Git checkout."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--", "."],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        files = [root / rel for rel in proc.stdout.decode("utf-8").split("\0") if rel]
        return [p for p in files if p.is_file()]
    return [p for p in root.rglob("*") if p.is_file()]


def scan_process_vocabulary(
    root: Path,
    *,
    extensions: tuple[str, ...] | None = _DEFAULT_EXTENSIONS,
    excluded_dir_names: frozenset[str] = _DEFAULT_EXCLUDED_DIR_NAMES,
) -> list[Hit]:
    """Scan text files without modifying the tree."""
    hits: list[Hit] = []
    for path in sorted(_candidate_files(root)):
        if extensions is not None and path.suffix not in extensions:
            continue
        relparts = path.relative_to(root).parts
        if any(part in excluded_dir_names for part in relparts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relpath = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            masked = _SYNC_ORCHESTRATOR_RE.sub("", line)
            for match in _WORD_RE.finditer(masked):
                hits.append(Hit(relpath, lineno, match.group(0), line))
    return hits


class TestScanFunctionAgainstFixtures:
    """Die Scan-Funktion selbst — ausschliesslich gegen ``tmp_path``-Proben,
    nie gegen den echten Baum (Scanner-Tests duerfen keine Proben in den
    echten Baum legen)."""

    def test_finds_case_insensitive_hit_with_flexion(self, tmp_path: Path) -> None:
        (tmp_path / "sample.py").write_text(
            "# Nur STANDARDBLOCKS duerfen das aendern.\n",
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert [h.word for h in hits] == ["STANDARDBLOCKS"]

    def test_finds_each_word_in_the_list(self, tmp_path: Path) -> None:
        (tmp_path / "sample.py").write_text(
            "\n".join(
                [
                    "# Lane",
                    "# Standardblock",
                    "# Briefing",
                    "# Besitzliste",
                    "# TABU",
                    "# Orchestrator",
                    "# Ruling",
                    "# Fix-Runde",
                ]
            ),
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert len(hits) == 8

    def test_ignores_sync_orchestrator_compound_forms(self, tmp_path: Path) -> None:
        (tmp_path / "sample.js").write_text(
            "\n".join(
                [
                    "// window.syncOrchestrator laedt das Sync-Modul.",
                    "// siehe sync-orchestrator.js fuer Details.",
                    "// Der SYNC-ORCHESTRATOR ist optional.",
                ]
            ),
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert hits == []

    def test_bare_orchestrator_next_to_sync_orchestrator_still_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "sample.js").write_text(
            "// Ohne Sync-Orchestrator faellt der Orchestrator auf den Fallback zurueck.\n",
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert [h.word for h in hits] == ["Orchestrator"]

    def test_ignores_excluded_directories(self, tmp_path: Path) -> None:
        for dirname in ("staticfiles", "media", "__pycache__", "node_modules"):
            sub = tmp_path / dirname
            sub.mkdir()
            (sub / "sample.py").write_text("# TABU\n", encoding="utf-8")
        (tmp_path / "src_sample.py").write_text("# TABU\n", encoding="utf-8")
        hits = scan_process_vocabulary(tmp_path)
        assert [h.relpath for h in hits] == ["src_sample.py"]

    def test_only_scans_configured_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "sample.png").write_bytes(b"TABU")
        (tmp_path / "sample.py").write_text("# TABU\n", encoding="utf-8")
        hits = scan_process_vocabulary(tmp_path, extensions=(".py",))
        assert [h.relpath for h in hits] == ["sample.py"]

    def test_binary_files_are_skipped_without_error(self, tmp_path: Path) -> None:
        (tmp_path / "sample.py").write_bytes(b"\xff\xfe# TABU garbage \x00\x01")
        scan_process_vocabulary(tmp_path)  # nur: darf nicht werfen


def scan_release_vocabulary(root: Path) -> list[Hit]:
    """Select release files using the build script's literal removal list."""
    build_script = root / "dev-ops/release/build-release.sh"
    removed: set[str] = set()
    if (root / "dev-ops/release/build-release.sh").exists():
        removed.update(
            re.findall(
                r'^\s*rm -[rf]+\s+"\$RELEASE_DIR/([^"\n]+)"\s*$', build_script.read_text(encoding="utf-8"), re.MULTILINE
            )
        )
    return [
        hit
        for hit in scan_process_vocabulary(root)
        if hit.relpath not in _ALLOWLIST
        and not any(hit.relpath == name or hit.relpath.startswith(name + "/") for name in removed)
    ]


def finding_counts(hits: list[Hit]) -> dict[str, int]:
    """Pin file and exact line content, independent of line-number shifts."""
    return dict(Counter(f"{hit.relpath}:{hashlib.sha256(hit.line.encode()).hexdigest()}" for hit in hits))


def baseline_is_subset(current: dict[str, int], ceiling: dict[str, int]) -> bool:
    """Reject added keys and increased counts, including duplicate lines."""
    return all(isinstance(count, int) and 0 < count <= ceiling.get(key, 0) for key, count in current.items())


def test_release_findings_match_remaining_baseline() -> None:
    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    current = finding_counts(scan_release_vocabulary(REPO_ROOT))
    assert current == baseline["remaining"], "Update removed findings; new findings must be fixed."


def test_frozen_baseline_cannot_grow() -> None:
    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    frozen = json.dumps(baseline["ceiling"], sort_keys=True).encode()
    assert hashlib.sha256(frozen).hexdigest() == "c2b7831b973a96365cf1e74774637069317285e8ac3377d5de8e32b2c3bacbb8"
    assert baseline_is_subset(baseline["remaining"], baseline["ceiling"])


@pytest.mark.parametrize("text", ["// Welle U", "// /work/example/file.py", "// CLAUDE" + ".md"])
def test_detects_internal_references_in_javascript_tests(tmp_path: Path, text: str) -> None:
    directory = tmp_path / "tests" / "js"
    directory.mkdir(parents=True)
    (directory / "example.test.mjs").write_text(text, encoding="utf-8")
    hits = scan_process_vocabulary(tmp_path)
    assert len(hits) == 1
    assert hits[0].relpath == "tests/js/example.test.mjs"


def test_sync_token_does_not_hide_internal_path(tmp_path: Path) -> None:
    (tmp_path / "sample.js").write_text("// Sync-Orchestrator /work/example\n", encoding="utf-8")
    assert [hit.word for hit in scan_process_vocabulary(tmp_path)] == ["/work/"]


def test_release_selection_includes_root_files_and_javascript_tests(tmp_path: Path) -> None:
    build_script = tmp_path / "dev-ops/release/build-release.sh"
    build_script.parent.mkdir(parents=True)
    build_script.write_text('rm -rf "$RELEASE_DIR/dev-ops"\nrm -rf "$RELEASE_DIR/docs/dev"\n', encoding="utf-8")
    for name in ("Makefile", "tests/js/example.test.mjs", Path("docs") / "dev" / "private.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Ruling\n", encoding="utf-8")
    assert {hit.relpath for hit in scan_release_vocabulary(tmp_path)} == {"Makefile", "tests/js/example.test.mjs"}


def test_baseline_can_only_shrink() -> None:
    assert baseline_is_subset({"sample:hash": 1}, {"sample:hash": 2})
    assert not baseline_is_subset({"sample:hash": 3}, {"sample:hash": 2})
    assert not baseline_is_subset({"new:hash": 1}, {"sample:hash": 2})
