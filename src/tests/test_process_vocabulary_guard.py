"""Reject new internal process references in release files (Refs #2647)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
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
    + r")\w*|\bWelle [A-Z]\b|/work/|\bCLAUDE\.md\b"
    + r"|\b(?:kimi|codex|opus|sonnet|haiku|ollama|claude)\b|\bglm-[a-z0-9][\w.-]*|\bgpt-[0-9][\w.-]*",
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


# The release sanitizer rewrites repository addresses; the key must not depend on those rewrites.
# Dev issue addresses lose their link: "[#N](address)" and a bare address become "#N",
# "[label](address)" becomes "label #N". The dev repository name becomes the public one,
# and blob/tree addresses point to the public "main" branch.
_KEY_FORMAT = 2
_KNOWN_KEY_FORMATS = (1, 2)
_KEY_FORMAT_MIGRATION = (1, 2)
# Assembled from parts: the release sanitizer rewrites or rejects the literal dev repository name in source files.
_DEV_REPOSITORY = "tobiasnix" + "/" + "anlaufstelle"
_DEV_ISSUE_HOST = r"https?://(?:www\.)?github\.com/" + _DEV_REPOSITORY + r"/(?:issues|pull)/(?P<number>\d+)"
_DEV_ISSUE_LINK_RE = re.compile(r"\[(?P<label>[^\[\]]*)\]\(" + _DEV_ISSUE_HOST + r"[^)\s]*\)")
_DEV_ISSUE_ADDRESS_RE = re.compile(_DEV_ISSUE_HOST + r"[\w~%.+/?=\x26;:@!$,*#-]*")
_DEV_REPOSITORY_RE = re.compile(re.escape(_DEV_REPOSITORY) + r"(?![\w-])")
_REPOSITORY_REF_RE = re.compile(r"(anlaufstelle/app/(?:blob|tree))/[^/\s\\%]+/")


def _normalize_release_rewrites(line: str) -> str:
    """Write a line the way it reads after the release sanitizer's repository rewrites."""
    line = _unlink_dev_issues(line)
    return _REPOSITORY_REF_RE.sub(r"\1/main/", _DEV_REPOSITORY_RE.sub("anlaufstelle/app", line))


def _unlink_dev_issues(line: str) -> str:
    """Write dev issue addresses the way the release sanitizer leaves them."""

    def link(match: re.Match[str]) -> str:
        label, number = match.group("label"), match.group("number")
        return label if label.endswith(f"#{number}") else f"{label} #{number}"

    return _DEV_ISSUE_ADDRESS_RE.sub(lambda m: f"#{m.group('number')}", _DEV_ISSUE_LINK_RE.sub(link, line))


def finding_counts(hits: list[Hit]) -> dict[str, int]:
    """Pin file, matched word and release-normalized line content, independent of line-number shifts."""
    return dict(
        Counter(
            f"{hit.relpath}:{hit.word.lower()}:{hashlib.sha256(_normalize_release_rewrites(hit.line).encode()).hexdigest()}"
            for hit in hits
        )
    )


def baseline_is_subset(current: dict[str, int], ceiling: dict[str, int]) -> bool:
    """Reject added keys and increased counts, including duplicate lines."""
    return all(isinstance(count, int) and 0 < count <= ceiling.get(key, 0) for key, count in current.items())


def _git_text(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.decode("utf-8") if proc.returncode == 0 else None


def _committed_baselines(root: Path) -> list[tuple[int, dict[str, int]]]:
    """Return the committed ``(key_format, remaining)`` pairs of the baseline file, oldest first."""
    relpath = _BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
    log = _git_text(root, "log", "--reverse", "--format=%H", "--", relpath)
    versions: list[tuple[int, dict[str, int]]] = []
    for commit in (log or "").split():
        blob = _git_text(root, "show", f"{commit}:{relpath}")
        if blob is not None:
            data = json.loads(blob)
            versions.append((data.get("key_format", 1), data["remaining"]))
    return versions


def _counts_per_file(counts: dict[str, int]) -> dict[str, int]:
    """Sum finding counts per file; keys start with the file path in every key format."""
    per_file: Counter[str] = Counter()
    for key, count in counts.items():
        per_file[key.split(":", 1)[0]] += count
    return dict(per_file)


def baseline_history_allows(root: Path, current: dict[str, int], current_format: int = _KEY_FORMAT) -> bool:
    """Every committed baseline version, and the current one, may only shrink.

    Within one key format the comparison is key by key. The only accepted format change is the
    one-time migration from format 1 to format 2, which must keep the count per file unchanged.
    Downgrades, unknown formats and repeated changes are rejected, so a format change can never
    serve as a reset that lets a removed finding come back.
    """
    versions = [*_committed_baselines(root), (current_format, current)]
    if any(version_format not in _KNOWN_KEY_FORMATS for version_format, _ in versions):
        return False
    migrations = 0
    for (older_format, older), (newer_format, newer) in zip(versions, versions[1:], strict=False):
        if older_format == newer_format:
            allowed = baseline_is_subset(newer, older)
        else:
            migrations += 1
            allowed = (
                (older_format, newer_format) == _KEY_FORMAT_MIGRATION
                and migrations == 1
                and _counts_per_file(newer) == _counts_per_file(older)
            )
        if not allowed:
            return False
    return True


def test_release_findings_match_remaining_baseline() -> None:
    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    assert baseline["key_format"] == _KEY_FORMAT
    current = finding_counts(scan_release_vocabulary(REPO_ROOT))
    assert current == baseline["remaining"], "Update removed findings; new findings must be fixed."


def test_frozen_baseline_cannot_grow() -> None:
    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    frozen = json.dumps(baseline["ceiling"], sort_keys=True).encode()
    assert hashlib.sha256(frozen).hexdigest() == "191d9c57c2ea745abb300c5f84462f8877c58a78a4d1234578822b04d9a584ad"
    assert baseline_is_subset(baseline["remaining"], baseline["ceiling"])


@pytest.mark.parametrize("text", ["// Welle U", "// /work/example/file.py", "// CLAUDE" + ".md"])
def test_detects_internal_references_in_javascript_tests(tmp_path: Path, text: str) -> None:
    directory = tmp_path / "tests" / "js"
    directory.mkdir(parents=True)
    (directory / "example.test.mjs").write_text(text, encoding="utf-8")
    hits = scan_process_vocabulary(tmp_path)
    assert len(hits) == 1
    assert hits[0].relpath == "tests/js/example.test.mjs"


# Assemble the tool label so the release sanitizer preserves the detection fixture.
@pytest.mark.parametrize(
    "label",
    ["kimi-k3", "KIMI", "glm-5", "Co" + "dex-Re-Review", "gpt-6", "Opus", "Sonnet", "Haiku", "Ollama", "Claude"],
)
def test_detects_model_and_tool_labels(tmp_path: Path, label: str) -> None:
    (tmp_path / "sample.py").write_text(f"# {label} review\n", encoding="utf-8")
    assert len(scan_release_vocabulary(tmp_path)) == 1


@pytest.mark.parametrize("word", ["alchemist", "codexology", "opuses", "sonnets", "haikus", "claudette", "gpt-x"])
def test_model_patterns_preserve_unrelated_words(tmp_path: Path, word: str) -> None:
    (tmp_path / "sample.py").write_text(f"# {word}\n", encoding="utf-8")
    assert scan_release_vocabulary(tmp_path) == []


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


def _commit_baseline(repo: Path, content: dict) -> None:
    """Commit one version of the baseline file into a throwaway repository."""
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    baseline_path = repo / _BASELINE_PATH.relative_to(REPO_ROOT)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(content), encoding="utf-8")
    subprocess.run(["git", "add", str(baseline_path)], cwd=repo, check=True)
    identity = [
        "-c",
        "user.name=Fixture Author",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run(["git", *identity, "commit", "-qm", "Update baseline"], cwd=repo, check=True)


def test_history_rejects_restored_finding_after_shrink(tmp_path: Path) -> None:
    for count in (2, 1):
        _commit_baseline(tmp_path, {"remaining": {"sample:hash": count}})
    assert baseline_history_allows(tmp_path, {"sample:hash": 1}, 1)
    assert not baseline_history_allows(tmp_path, {"sample:hash": 2}, 1)


def _commit_versions(repo: Path, versions: list[tuple[int, dict[str, int]]]) -> None:
    for key_format, remaining in versions:
        _commit_baseline(repo, {"key_format": key_format, "remaining": remaining})


def _legacy(path: str, line_no: int) -> str:
    return f"{path}:{line_no:064x}"


def _current(path: str, word: str, line_no: int) -> str:
    return f"{path}:{word}:{line_no:064x}"


def test_history_allows_the_one_time_migration_with_unchanged_counts_per_file(tmp_path: Path) -> None:
    _commit_versions(tmp_path, [(1, {_legacy("a.py", 1): 2, _legacy("b.py", 2): 1})])
    migrated = {_current("a.py", "x", 1): 1, _current("a.py", "y", 2): 1, _current("b.py", "x", 3): 1}
    assert baseline_history_allows(tmp_path, migrated, 2)


@pytest.mark.parametrize(
    "migrated",
    [
        {_current("a.py", "x", 1): 2},
        {_current("a.py", "x", 1): 3, _current("b.py", "x", 3): 1},
        {_current("a.py", "x", 1): 2, _current("b.py", "x", 3): 1, _current("c.py", "x", 4): 1},
        {_current("a.py", "x", 1): 2, _current("b.py", "x", 3): 1, _current("b.py", "y", 4): 1},
    ],
    ids=["finding-dropped", "count-grown", "new-file", "second-finding-in-known-file"],
)
def test_history_requires_the_migration_to_keep_the_stock(tmp_path: Path, migrated: dict[str, int]) -> None:
    _commit_versions(tmp_path, [(1, {_legacy("a.py", 1): 2, _legacy("b.py", 2): 1})])
    assert not baseline_history_allows(tmp_path, migrated, 2)


def test_history_rejects_a_return_to_the_old_format_and_a_second_migration(tmp_path: Path) -> None:
    initial = {_current("a.py", "x", 1): 1, _current("a.py", "y", 2): 1}
    shrunk = {_current("a.py", "x", 1): 1}
    restored = {_current("a.py", "y", 2): 1}
    _commit_versions(tmp_path, [(2, initial), (2, shrunk), (1, {_legacy("a.py", 1): 1})])
    assert not baseline_history_allows(tmp_path, restored, 2)
    assert not baseline_history_allows(tmp_path, {_legacy("a.py", 1): 1}, 1)


def test_history_rejects_a_downgrade_in_the_working_copy(tmp_path: Path) -> None:
    _commit_versions(tmp_path, [(2, {_current("a.py", "x", 1): 1})])
    assert not baseline_history_allows(tmp_path, {_legacy("a.py", 1): 1}, 1)


@pytest.mark.parametrize("unknown_format", [0, 3, 99])
def test_history_rejects_unknown_key_formats(tmp_path: Path, unknown_format: int) -> None:
    _commit_versions(tmp_path, [(1, {_legacy("a.py", 1): 1})])
    assert not baseline_history_allows(tmp_path, {"a.py:" + "9" * 8: 1}, unknown_format)
    committed_unknown = tmp_path / "committed-unknown"
    _commit_versions(committed_unknown, [(unknown_format, {"a.py:" + "9" * 8: 1})])
    assert not baseline_history_allows(committed_unknown, {"a.py:" + "9" * 8: 1}, unknown_format)


def test_history_keeps_comparing_key_by_key_within_a_format(tmp_path: Path) -> None:
    _commit_versions(tmp_path, [(2, {_current("a.py", "x", 1): 1, _current("a.py", "y", 2): 1})])
    assert baseline_history_allows(tmp_path, {_current("a.py", "x", 1): 1}, 2)
    assert not baseline_history_allows(tmp_path, {_current("a.py", "z", 3): 1}, 2)


def test_format_changes_cannot_restore_a_removed_real_finding(tmp_path: Path) -> None:
    """Shrink, go through the old format and back, then restore one finding while dropping a sibling."""
    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    initial = baseline["remaining"]
    by_file: dict[str, list[str]] = {}
    for key in initial:
        by_file.setdefault(key.split(":", 1)[0], []).append(key)
    removed, replacement = next(keys for keys in by_file.values() if len(keys) >= 2)[:2]
    shrunk = {key: count for key, count in initial.items() if key != removed}
    restored = {key: count for key, count in initial.items() if key != replacement}
    legacy: dict[str, int] = {}
    for hit in scan_release_vocabulary(REPO_ROOT):
        if next(iter(finding_counts([hit]))) in shrunk:
            legacy_key = f"{hit.relpath}:{hashlib.sha256(hit.line.encode()).hexdigest()}"
            legacy[legacy_key] = legacy.get(legacy_key, 0) + 1
    _commit_versions(tmp_path, [(2, initial), (2, shrunk), (1, legacy)])
    assert baseline_is_subset(restored, baseline["ceiling"])
    assert not baseline_is_subset(restored, shrunk)
    assert not baseline_history_allows(tmp_path, restored, 2)


def test_baseline_history_never_restores_findings() -> None:
    if not _committed_baselines(REPO_ROOT):
        pytest.skip("No Git history for the baseline file (flat clone or release tree).")
    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    assert baseline_history_allows(REPO_ROOT, baseline["remaining"], baseline["key_format"]), (
        "A removed finding was restored."
    )


_SANITIZER_PATH = REPO_ROOT / "dev-ops" / "release" / "sanitize_release_tree.py"
_DEV_ISSUE_URL = f"https://github.com/{_DEV_REPOSITORY}/issues/2149"


def _load_sanitizer():
    """Load the release sanitizer from its file; the release tree does not ship it."""
    spec = importlib.util.spec_from_file_location("release_sanitizer_under_test", _SANITIZER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_key_is_stable_when_dev_issue_links_are_unlinked() -> None:
    linked = [
        Hit("a.py", 3, "Welle J", f"# measured in Welle J ({_DEV_ISSUE_URL}),"),
        Hit("b.md", 1, "Ruling", f"- Ruling [#2149]({_DEV_ISSUE_URL}) applies"),
        Hit("c.md", 1, "Ruling", f"- Ruling, see [the ticket]({_DEV_ISSUE_URL}) here"),
    ]
    unlinked = [
        Hit("a.py", 3, "Welle J", "# measured in Welle J (#2149),"),
        Hit("b.md", 1, "Ruling", "- Ruling #2149 applies"),
        Hit("c.md", 1, "Ruling", "- Ruling, see the ticket #2149 here"),
    ]
    assert finding_counts(linked) == finding_counts(unlinked)


def test_key_still_changes_with_the_rest_of_the_line() -> None:
    first = [Hit("a.py", 1, "Ruling", f"# Ruling A ({_DEV_ISSUE_URL})")]
    second = [Hit("a.py", 1, "Ruling", f"# Ruling B ({_DEV_ISSUE_URL})")]
    other_word = [Hit("a.py", 1, "Lane", f"# Ruling A ({_DEV_ISSUE_URL})")]
    other_issue = [Hit("a.py", 1, "Ruling", f"# Ruling A (https://github.com/{_DEV_REPOSITORY}/issues/7)")]
    assert finding_counts(first) != finding_counts(second)
    assert finding_counts(first) != finding_counts(other_word)
    assert finding_counts(first) != finding_counts(other_issue)


@pytest.mark.skipif(not _SANITIZER_PATH.exists(), reason="The release sanitizer is not part of the release tree.")
def test_keys_survive_the_real_release_sanitizer(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(
        f"- **Entry** ([#2149]({_DEV_ISSUE_URL})) — measured in Welle J ({_DEV_ISSUE_URL}), see /work/example\n",
        encoding="utf-8",
    )
    (tmp_path / "sample.py").write_text(
        f'"""Docstring.\n\nSource: measuring run of Welle J ({_DEV_ISSUE_URL}),\nruling pending.\n"""\n',
        encoding="utf-8",
    )
    before = finding_counts(scan_release_vocabulary(tmp_path))
    assert before
    _load_sanitizer().sanitize_directory(tmp_path)
    assert _DEV_ISSUE_URL not in (tmp_path / "sample.py").read_text(encoding="utf-8")
    assert finding_counts(scan_release_vocabulary(tmp_path)) == before


@pytest.mark.skipif(not _SANITIZER_PATH.exists(), reason="The release sanitizer is not part of the release tree.")
def test_keys_of_all_current_findings_survive_the_real_release_sanitizer(tmp_path: Path) -> None:
    """Copy every file holding a finding and compare its keys before and after sanitizing."""
    files = {hit.relpath for hit in scan_release_vocabulary(REPO_ROOT)}
    assert files
    for relpath in files:
        target = tmp_path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relpath, target)
    before = finding_counts(scan_release_vocabulary(tmp_path))
    _load_sanitizer().sanitize_directory(tmp_path)
    assert finding_counts(scan_release_vocabulary(tmp_path)) == before
